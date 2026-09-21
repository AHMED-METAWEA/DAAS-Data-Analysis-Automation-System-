from __future__ import annotations

import pandas as pd

from agents.insights import agent as insights_agent
from backend.app.services import insights_service


def _sales_df(n_days: int = 90) -> pd.DataFrame:
    rows = []
    base = pd.Timestamp("2024-01-01")
    oid = 0
    for d in range(n_days):
        for k in range(1 + d % 3):
            oid += 1
            rows.append({
                "order_id": f"O{oid}",
                "customer_id": f"C{oid % 25}",
                "order_date": (base + pd.Timedelta(days=d)).strftime("%Y-%m-%d"),
                "product": f"P{oid % 6}",
                "category": ["Coffee", "Tea", "Gear"][oid % 3],
                "total_price": 20.0 + (oid % 10) * 5,
            })
    return pd.DataFrame(rows)


def test_clean_report_skips_correction(monkeypatch) -> None:
    calls: list[str] = []

    def fake_complete(purpose, messages, **kwargs):
        calls.append("draft")
        return "# Report\nEverything looks healthy this period."

    monkeypatch.setattr(insights_agent, "complete", fake_complete)

    result = insights_service.generate_grounded_insights(_sales_df(), "", model="test-model")

    assert calls == ["draft"], "a report with no material numeric claims must not trigger a correction call"
    assert result.grounding.status == "none"
    assert result.report_md.startswith("# Report")


def test_ungrounded_report_triggers_one_correction(monkeypatch) -> None:
    calls: list[str] = []

    def fake_draft(purpose, messages, **kwargs):
        calls.append("draft")
        return "# Report\nTotal revenue was $9,999,999 this period."

    def fake_correction(purpose, messages, **kwargs):
        calls.append("correction")
        return "# Report\nSee the findings below for verified revenue figures."

    monkeypatch.setattr(insights_agent, "complete", fake_draft)
    monkeypatch.setattr(insights_service, "complete", fake_correction)

    result = insights_service.generate_grounded_insights(_sales_df(), "", model="test-model")

    assert calls == ["draft", "correction"]
    assert "9,999,999" not in result.report_md


def test_correction_failure_falls_back_to_draft(monkeypatch) -> None:
    def fake_draft(purpose, messages, **kwargs):
        return "# Report\nTotal revenue was $9,999,999 this period."

    def fake_correction(purpose, messages, **kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(insights_agent, "complete", fake_draft)
    monkeypatch.setattr(insights_service, "complete", fake_correction)

    result = insights_service.generate_grounded_insights(_sales_df(), "", model="test-model")

    assert "9,999,999" in result.report_md
    assert result.grounding.status != "clean"


def test_returns_analytics_payload_and_template_stem(monkeypatch) -> None:
    def fake_complete(purpose, messages, **kwargs):
        return "# Report\nSummary text with no numbers."

    monkeypatch.setattr(insights_agent, "complete", fake_complete)

    result = insights_service.generate_grounded_insights(_sales_df(), "", model="test-model")

    assert result.template_stem in ("decision_brief", "non_technical", "executive", "detailed")
    assert "kpi" in result.analytics_payload
    assert result.analytics_payload["kpi"]["revenue"] > 0
