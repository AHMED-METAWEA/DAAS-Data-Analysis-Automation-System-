"""The insights prompt sizes itself to the budget that will actually bill it.

Groq bills ``prompt + max_tokens`` against 8,000 tokens/minute on the on-demand
tier. A ~6,000-token prompt with a 3,000-token reservation is 9,047 tokens of
request against an 8,000-token ceiling, which is why every insights report
failed with a 413 before the model ran.

Trimming the allow-list is the right lever and the citation contract is what
makes it safe: figures are dropped from the *tail* of a registry already
ordered by decision relevance, and anything the ranked brief cites is kept
regardless, because a number the model can read in the brief but cannot cite is
the exact pressure that makes it type digits instead.
"""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd

from agents.insights.agent import (
    MAX_REPORT_TOKENS,
    build_schema_brief,
    build_user_prompt,
    prepare_context,
    size_figure_table,
)
from agents.insights.evidence import render_evidence
from tools.token_budget import estimate_messages_tokens, provider_tpm_limit


def _sales_frame(rows: int = 400) -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=rows, freq="D")
    return pd.DataFrame({
        "order_id": [f"A{i}" for i in range(rows)],
        "order_date": dates,
        "customer_id": [f"C{i % 40}" for i in range(rows)],
        "product": [f"P{i % 12}" for i in range(rows)],
        "category": [f"Cat{i % 4}" for i in range(rows)],
        "region": [["West", "East", "North"][i % 3] for i in range(rows)],
        "quantity": [(i % 5) + 1 for i in range(rows)],
        "unit_price": [10.0 + (i % 20) for i in range(rows)],
        "total_price": [((i % 5) + 1) * (10.0 + (i % 20)) for i in range(rows)],
        "cost": [5.0 + (i % 10) for i in range(rows)],
    })


def _context():
    from agents.analytics.engine import run_analytics

    df = _sales_frame()
    payload = run_analytics(df, "sales")
    registry, decision, evidence = prepare_context(df, payload)
    return df, payload, registry, decision, evidence


def _prompts(figure_table: str | None, df, payload, registry, evidence, decision):
    from agents.insights.agent import _load_template

    system = _load_template("decision_brief")
    user = build_user_prompt(
        schema_brief=build_schema_brief(df, payload),
        business_context="",
        registry=registry,
        evidence=evidence,
        blind_spots=(decision or {}).get("blind_spots", []) or [],
        figure_table=figure_table,
    )
    return system, user


class TestTheRequestFitsTheBudget:
    def test_a_full_report_request_stays_under_the_per_minute_ceiling(self) -> None:
        df, payload, registry, decision, evidence = _context()
        system, _ = _prompts(None, df, payload, registry, evidence, decision)
        _, bare = _prompts("", df, payload, registry, evidence, decision)
        with patch("agents.insights.agent.first_available_provider", return_value="groq"):
            table, _offered = size_figure_table(
                registry=registry, evidence=evidence, system_prompt=system,
                user_prompt_without_table=bare,
            )
        _, user = _prompts(table, df, payload, registry, evidence, decision)
        request = estimate_messages_tokens(
            [{"role": "system", "content": system}, {"role": "user", "content": user}]
        ) + MAX_REPORT_TOKENS
        assert request < provider_tpm_limit("groq")

    def test_the_reservation_leaves_room_for_a_real_prompt(self) -> None:
        """A ceiling above about half the budget cannot coexist with the brief
        it is supposed to be written from."""
        assert provider_tpm_limit("groq") / 2 > MAX_REPORT_TOKENS


class TestTrimmingIsSafeForTheCitationContract:
    def test_every_figure_the_brief_cites_stays_citable(self) -> None:
        _df, _payload, registry, _decision, evidence = _context()
        table, _offered = size_figure_table(
            registry=registry, evidence=evidence, system_prompt="",
            user_prompt_without_table="", reserved_completion=MAX_REPORT_TOKENS,
        )
        with patch("agents.insights.agent.first_available_provider", return_value="groq"):
            tight, _ = size_figure_table(
                registry=registry, evidence=evidence,
                system_prompt="x" * 4000, user_prompt_without_table="y" * 4000,
            )
        cited = registry.used_keys(render_evidence(evidence))
        assert cited, "fixture should produce a brief that cites figures"
        for key in cited:
            assert f"{{{{{key}}}}}" in tight, f"{key} is cited by the brief but not citable"
        assert len(tight) <= len(table)

    def test_an_unlimited_provider_gets_the_whole_allow_list(self) -> None:
        _df, _payload, registry, _decision, evidence = _context()
        with patch("agents.insights.agent.first_available_provider", return_value="anthropic"):
            table, offered = size_figure_table(
                registry=registry, evidence=evidence,
                system_prompt="", user_prompt_without_table="",
            )
        assert table == registry.prompt_table()
        assert offered == len(registry.all())

    def test_no_configured_provider_leaves_the_prompt_untouched(self) -> None:
        _df, _payload, registry, _decision, evidence = _context()
        with patch("agents.insights.agent.first_available_provider", return_value=None):
            table, _ = size_figure_table(
                registry=registry, evidence=evidence,
                system_prompt="", user_prompt_without_table="",
            )
        assert table == registry.prompt_table()


class TestTrimmingOrder:
    def test_figures_are_dropped_from_the_tail_not_at_random(self) -> None:
        """The registry is ordered the way a decision should be reasoned about,
        so the tail is the least decision-relevant place to cut."""
        from agents.insights.figures import FigureRegistry

        registry = FigureRegistry()
        for i in range(60):
            registry.add(f"k{i}", f"Figure {i}", float(i), "currency", "sum of things")
        table, offered = registry.prompt_table_within(300)
        assert 0 < offered < 60
        assert "{{k0}}" in table
        assert "{{k59}}" not in table

    def test_the_kept_table_still_reads_in_registry_order(self) -> None:
        from agents.insights.figures import FigureRegistry

        registry = FigureRegistry()
        for i in range(60):
            registry.add(f"k{i}", f"Figure {i}", float(i), "currency", "sum of things")
        table, _ = registry.prompt_table_within(300, priority=["k59"])
        keys = [line.split("}}")[0][2:] for line in table.splitlines()]
        assert "k59" in keys
        assert keys == sorted(keys, key=lambda k: int(k[1:]))
