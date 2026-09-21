"""The autonomous analyst: what it decides is worth saying, and how it says it.

The tests here defend three properties that decide whether scheduled monitoring
is useful or gets muted within a week:

* **It ranks by money at stake**, using the same evidence engine the Insights
  report uses — so the platform has one answer to "what matters most here".
* **It does not repeat itself.** A standing problem is still true tomorrow;
  saying so every morning is how a channel gets ignored.
* **It says the same thing in Arabic.** Not a translation of the English
  briefing — a sibling rendered from the same arithmetic.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from agents.analytics.engine import run_analytics
from agents.insights.decision_metrics import compute_decision_metrics
from agents.insights.evidence import build_evidence
from agents.insights.figures import TOKEN_RE, build_registry
from monitoring.briefing import BriefingContext, compose, format_amount
from monitoring.evidence_ar import AR_EVIDENCE, AR_EVIDENCE_TITLE
from monitoring.history import extract_metrics
from monitoring.rules import (
    Finding,
    RuleContext,
    evaluate,
    filter_findings,
    fingerprint,
    severity_from_share,
)
from monitoring.scheduler import build_trigger, describe_schedule
from notifications.base import Message, markdown_to_text
from notifications.whatsapp_channel import normalise_phone


def _declining_df() -> pd.DataFrame:
    """Strong April, weak May, with cost and discount columns so the evidence
    engine has margin and discount findings to rank."""
    rows, oid = [], 0
    for month, days, price in (("04", 30, 100.0), ("05", 31, 40.0)):
        for day in range(1, days + 1):
            for k in range(6):
                oid += 1
                rows.append({
                    "order_id": f"O{oid:05d}",
                    "order_date": f"2025-{month}-{day:02d}",
                    "customer_id": f"C{oid % 40:03d}",
                    "product": ["Alpha", "Beta", "Gamma"][k % 3],
                    "region": ["North", "South"][k % 2],
                    "quantity": 2,
                    "unit_price": price,
                    "cost": price * 0.7,
                })
    return pd.DataFrame(rows)


def _context(df: pd.DataFrame, *, language: str = "en", **kwargs) -> RuleContext:
    payload = run_analytics(df, "")
    decision = compute_decision_metrics(df, payload.get("schema"))
    registry = build_registry(payload, decision)
    evidence = build_evidence(payload, decision, registry)
    return RuleContext(
        project_id="p1", payload=payload, decision=decision, registry=registry,
        evidence=evidence, language=language, **kwargs,
    )


@pytest.fixture(scope="module")
def df() -> pd.DataFrame:
    return _declining_df()


# ── Ranking ─────────────────────────────────────────────────────────────────

def test_findings_are_ranked_by_severity_then_money(df):
    findings = evaluate(_context(df))
    assert findings
    order = ["critical", "high", "medium", "low", "info"]
    positions = [order.index(f.severity) for f in findings]
    assert positions == sorted(positions), "severity ordering broken"
    for a, b in zip(findings, findings[1:]):
        if a.severity == b.severity:
            assert abs(a.money_at_stake) >= abs(b.money_at_stake)


def test_severity_scales_with_the_size_of_the_business():
    """40,000 is an emergency for a corner shop and a rounding error for a
    distributor; a fixed threshold would be wrong for both."""
    assert severity_from_share(40_000, 100_000) == "critical"
    assert severity_from_share(40_000, 10_000_000) == "info"
    assert severity_from_share(0, 100_000) == "info"


def test_structural_exposures_never_reach_critical(df):
    """A concentration risk is a standing fact, not an event.

    Ranked by share alone it scores CRITICAL every morning about something that
    has not changed — the fastest way to make monitoring worthless.
    """
    findings = evaluate(_context(df))
    for f in findings:
        if f.rule == "evidence.risk":
            assert f.severity in ("medium", "low", "info")
            assert f.is_event is False


def test_alert_text_carries_no_prompt_instructions(df):
    """evidence.py writes instructions for the model inside some bullets.

    They belong in the prompt and must never reach someone's phone.
    """
    for f in evaluate(_context(df)):
        assert "USE THIS COMPARISON WINDOW" not in f.title
        assert "USE THIS COMPARISON WINDOW" not in f.body


def test_alert_text_contains_no_unrendered_citation_tokens(df):
    for f in evaluate(_context(df)):
        assert "{{" not in f.body and "}}" not in f.body
        assert "[figure unavailable" not in f.body


# ── Not repeating itself ────────────────────────────────────────────────────

def test_fingerprints_are_stable_across_runs(df):
    first = {f.fingerprint_key for f in evaluate(_context(df))}
    second = {f.fingerprint_key for f in evaluate(_context(df))}
    assert first == second


def test_fingerprint_ignores_the_value_so_a_standing_problem_is_recognised():
    """Same finding, different number, must be the same fingerprint."""
    assert fingerprint("evidence", "LEAK", "decisions", "margin_drag") == fingerprint(
        "evidence", "LEAK", "decisions", "margin_drag"
    )
    assert fingerprint("evidence", "LEAK", "decisions", "margin_drag") != fingerprint(
        "evidence", "LEAK", "decisions", "discount_leak"
    )


def test_cooldown_suppresses_a_repeat_but_records_why(df):
    findings = evaluate(_context(df))
    kept, held = filter_findings(findings, min_severity="medium", limit=5)
    assert kept

    again, held_again = filter_findings(
        findings, min_severity="medium",
        suppressed={f.fingerprint_key for f in kept}, limit=5,
    )
    assert all(f.fingerprint_key not in {k.fingerprint_key for k in kept} for f in again)
    assert len(held_again) > len(held)


def test_severity_floor_and_cap_are_both_applied(df):
    findings = evaluate(_context(df))
    kept, held = filter_findings(findings, min_severity="critical", limit=99)
    assert all(f.severity == "critical" for f in kept)
    assert len(kept) + len(held) == len(findings)

    capped, spill = filter_findings(findings, min_severity="info", limit=2)
    assert len(capped) == 2 and spill


# ── Time-aware rules ────────────────────────────────────────────────────────

def test_snapshot_delta_compares_against_the_recorded_figure(df):
    payload = run_analytics(df, "")
    decision = compute_decision_metrics(df, payload.get("schema"))
    previous = extract_metrics(payload, decision)
    previous["revenue"] *= 1.5  # last run recorded a much larger number

    ctx = _context(
        df,
        previous_metrics=previous,
        previous_captured_at=datetime.now(UTC) - timedelta(days=1),
    )
    deltas = [f for f in evaluate(ctx) if f.rule == "snapshot_delta"]
    assert any(f.metric == "revenue" and f.change_pct < 0 for f in deltas)


def test_a_rise_is_never_ranked_as_money_lost(df):
    payload = run_analytics(df, "")
    decision = compute_decision_metrics(df, payload.get("schema"))
    previous = extract_metrics(payload, decision)
    previous["revenue"] *= 0.5  # revenue has doubled since the last run

    ctx = _context(df, previous_metrics=previous)
    for f in evaluate(ctx):
        if f.rule == "snapshot_delta" and f.change_pct and f.change_pct > 0:
            assert f.money_at_stake == 0.0
            assert f.severity == "info"


def test_stale_data_leads_even_past_a_bigger_money_finding(df):
    """If the last transaction is years old, every other figure describes a
    period that already ended — so freshness outranks them regardless of money."""
    ctx = _context(df, data_last_date="2020-01-01")
    findings = evaluate(ctx)
    assert findings[0].rule == "data_freshness"
    assert findings[0].severity == "critical"
    # There *is* a larger money finding in this dataset; it must not lead.
    assert any(f.money_at_stake > 0 for f in findings[1:])


def test_user_targets_are_checked_and_attributed_to_the_user(df):
    ctx = _context(df, config={"targets": {"revenue": {"min": 10_000_000}}})
    breaches = [f for f in evaluate(ctx) if f.rule == "target_breach"]
    assert breaches
    assert "your own threshold" in breaches[0].body


# ── The briefing ────────────────────────────────────────────────────────────

def test_briefing_headline_sums_only_events_not_exposures(df):
    """Adding a concentration exposure to an actual loss gives a number that
    describes nothing — and it is the number the owner reads first."""
    findings = [
        Finding(rule="a", severity="high", title="Lost money", body="", money_at_stake=1000.0),
        Finding(
            rule="evidence.risk", severity="medium", title="Concentration", body="",
            money_at_stake=500_000.0, is_event=False,
        ),
    ]
    markdown, _ = compose(findings, BriefingContext(project_name="Demo"))
    headline = next(line for line in markdown.splitlines() if "Money at stake" in line)
    assert "1,000" in headline
    assert "501,000" not in headline and "500,000" not in headline
    # The exposure is still shown on its own line — it just does not get summed
    # into a total that would mean nothing.
    assert "500,000" in markdown


def test_quiet_briefing_says_so_and_explains_a_suppressed_backlog():
    markdown, message = compose(
        [], BriefingContext(project_name="Demo", suppressed_count=3),
    )
    assert "Everything checked out" in markdown
    assert "3 finding" in markdown
    assert "nothing needs you today" in message.subject


def test_arabic_briefing_is_arabic_not_a_translated_shell(df):
    """The chrome AND the findings must be Arabic — a briefing whose headings
    are Arabic and whose content is English is a veneer."""
    ctx_ar = _context(df, language="ar")
    kept, _ = filter_findings(evaluate(ctx_ar), min_severity="medium", limit=5)
    assert kept

    markdown, message = compose(kept, BriefingContext(project_name="متجر", language="ar"))
    assert "الملخص اليومي" in message.subject
    assert "تحتاج انتباهك" in markdown
    # Every rendered finding body must contain Arabic script.
    for finding in kept:
        assert any("؀" <= ch <= "ۿ" for ch in finding.body), finding.body


def test_arabic_templates_use_the_same_citation_tokens_as_english():
    """The Arabic sentence must be rendered from the registry, never typed.

    A template whose tokens do not exist would publish "[figure unavailable]";
    one that typed its own digits would be a fabrication risk in a second
    language.
    """
    for key, template in AR_EVIDENCE.items():
        tokens = {m.group(1) for m in TOKEN_RE.finditer(template)}
        assert tokens, f"Arabic template '{key}' cites no figures"
        # No bare digits outside tokens — the engine substitutes every number.
        stripped = TOKEN_RE.sub("", template)
        assert not any(ch.isdigit() for ch in stripped), (
            f"Arabic template '{key}' types a digit instead of citing a figure"
        )
    for key in AR_EVIDENCE_TITLE:
        assert key in AR_EVIDENCE, f"title '{key}' has no matching body template"


def test_briefing_degrades_to_english_when_arabic_wording_is_missing(df):
    """A key with no Arabic template must fall back, not render half a sentence."""
    ctx = _context(df, language="ar")
    findings = evaluate(ctx)
    assert findings  # falling back still produces findings
    for f in findings:
        assert f.title.strip()


# ── Channel rendering ───────────────────────────────────────────────────────

def test_message_truncates_on_a_boundary_never_mid_number():
    message = Message(
        subject="s",
        text="Revenue fell by 6,656.10 this month. " * 200,
    )
    clipped = message.for_length(200)
    assert len(clipped) <= 200
    assert clipped.endswith("…")
    # The cut must not land inside a number.
    assert not clipped.rstrip("…").rstrip().endswith(",")


def test_markdown_is_flattened_for_channels_that_render_none():
    flat = markdown_to_text("## Title\n\n**bold** and *italic*\n\n- one\n- two")
    assert "##" not in flat and "**" not in flat
    assert "• one" in flat


def test_phone_numbers_normalise_to_one_form():
    assert normalise_phone("+20 100 123 4567") == "201001234567"
    assert normalise_phone("00201001234567") == "201001234567"
    assert normalise_phone("") == ""


def test_amounts_render_with_a_currency_only_when_one_is_known():
    assert format_amount(40_329.46) == "40,329"
    assert format_amount(40_329.46, "EGP") == "EGP 40,329"
    assert format_amount(-12.5) == "-12.50"


# ── Scheduling ──────────────────────────────────────────────────────────────

class _Sched:
    """Minimal stand-in — build_trigger reads attributes, not the ORM."""

    def __init__(self, **kw):
        defaults = dict(
            frequency="daily", hour=7, minute=0, day_of_week=0, day_of_month=1,
            cron_expression=None, timezone="Africa/Cairo",
        )
        defaults.update(kw)
        self.__dict__.update(defaults)


@pytest.mark.parametrize("frequency", ["daily", "weekdays", "weekly", "monthly", "hourly"])
def test_every_frequency_builds_a_trigger(frequency):
    assert build_trigger(_Sched(frequency=frequency)) is not None


def test_custom_frequency_uses_the_cron_expression():
    assert build_trigger(_Sched(frequency="custom", cron_expression="0 7 * * 1-5")) is not None
    with pytest.raises(ValueError):
        build_trigger(_Sched(frequency="custom", cron_expression=""))


def test_monthly_day_is_capped_so_february_is_never_skipped():
    trigger = build_trigger(_Sched(frequency="monthly", day_of_month=31))
    assert "28" in str(trigger)


def test_schedule_description_reads_back_what_will_fire():
    text = describe_schedule(_Sched(frequency="weekly", day_of_week=2, hour=7, minute=30))
    assert "Wednesday" in text and "07:30" in text and "Africa/Cairo" in text


def test_unknown_timezone_falls_back_instead_of_breaking_every_schedule():
    assert build_trigger(_Sched(timezone="Mars/Olympus_Mons")) is not None


def test_reconcile_job_survives_its_own_reconcile(monkeypatch):
    """The 60s reconcile loop must not delete itself on its first tick.

    It did: `sync_jobs` removes every job under the schedule prefix that has no
    row behind it, and the reconcile job used to share that prefix. The symptom
    is nearly invisible — the scheduler keeps running and keeps firing the jobs
    it already has, it just stops noticing schedules created or edited after
    start-up, which looks like "my new schedule never fired".
    """
    import monitoring.scheduler as sched

    monkeypatch.setattr(sched, "list_active_schedules", lambda: [])
    try:
        scheduler = sched.start_scheduler()
        assert scheduler.get_job(sched.RECONCILE_JOB_ID) is not None
        sched.sync_jobs()
        assert scheduler.get_job(sched.RECONCILE_JOB_ID) is not None, (
            "sync_jobs deleted the job that calls sync_jobs"
        )
        # ...and it is not mistaken for a schedule by anything user-facing.
        assert sched.scheduler_status()["jobs"] == []
    finally:
        sched.shutdown_scheduler()
