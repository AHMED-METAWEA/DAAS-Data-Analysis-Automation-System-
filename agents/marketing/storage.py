"""
PostgreSQL persistence for marketing artifacts.

Mirrors the forecasting storage pattern: creates tables on demand and saves a
marketing run (segments + campaigns + strategy report).  Silently skips when
PostgreSQL is not configured so the rest of the page keeps working.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import text


def _generate_run_id() -> str:
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    import random
    suffix = "".join(random.choices("abcdefghijklmnopqrstuvwxyz0123456789", k=6))
    return f"mkt_{ts}_{suffix}"


def _ensure_tables() -> bool:
    from tools.db_tools import get_engine, is_pg_configured

    if not is_pg_configured():
        return False

    engine = get_engine()
    ddl = """
    CREATE TABLE IF NOT EXISTS marketing_runs (
        run_id TEXT PRIMARY KEY,
        project_id TEXT,
        table_name TEXT,
        objective TEXT,
        budget TEXT,
        total_customers INTEGER,
        total_revenue DOUBLE PRECISION,
        strategy_report TEXT,
        created_at TIMESTAMP DEFAULT NOW()
    );
    ALTER TABLE marketing_runs ADD COLUMN IF NOT EXISTS project_id TEXT;
    CREATE TABLE IF NOT EXISTS marketing_segments (
        id SERIAL PRIMARY KEY,
        run_id TEXT REFERENCES marketing_runs(run_id),
        segment_name TEXT,
        customer_count INTEGER,
        customer_pct DOUBLE PRECISION,
        revenue DOUBLE PRECISION,
        revenue_pct DOUBLE PRECISION,
        avg_monetary DOUBLE PRECISION,
        avg_frequency DOUBLE PRECISION,
        avg_recency_days DOUBLE PRECISION,
        created_at TIMESTAMP DEFAULT NOW()
    );
    CREATE TABLE IF NOT EXISTS marketing_campaigns (
        id SERIAL PRIMARY KEY,
        run_id TEXT REFERENCES marketing_runs(run_id),
        name TEXT,
        target_segment TEXT,
        objective TEXT,
        channel TEXT,
        offer TEXT,
        message_angle TEXT,
        primary_kpi TEXT,
        expected_impact TEXT,
        budget_allocation_pct DOUBLE PRECISION,
        priority TEXT,
        created_at TIMESTAMP DEFAULT NOW()
    );
    """
    with engine.begin() as conn:
        for stmt in ddl.split(";"):
            s = stmt.strip()
            if s:
                conn.execute(text(s))
    return True


def save_marketing_run(
    marketing_payload: dict,
    *,
    table_name: str = "",
    objective: str = "",
    budget: str = "",
    strategy_report: str = "",
    campaign_plan: dict | None = None,
    run_id: str | None = None,
    project_id: str | None = None,
) -> dict:
    """Persist a marketing run.  Returns a status dict (mirrors forecasting)."""
    run_id = run_id or _generate_run_id()
    rfm = marketing_payload.get("rfm", {})
    segments = rfm.get("segments", {}) if rfm.get("available") else {}
    campaigns = (campaign_plan or {}).get("campaigns", [])

    try:
        if not _ensure_tables():
            return {"status": "skipped", "reason": "PostgreSQL not configured"}

        from tools.db_tools import get_engine

        engine = get_engine()
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO marketing_runs "
                    "(run_id, project_id, table_name, objective, budget, total_customers, "
                    "total_revenue, strategy_report) "
                    "VALUES (:run_id, :project_id, :table, :obj, :budget, :cust, :rev, :report)"
                ),
                {
                    "run_id": run_id,
                    "project_id": project_id,
                    "table": table_name,
                    "obj": objective,
                    "budget": budget,
                    "cust": rfm.get("total_customers"),
                    "rev": rfm.get("total_revenue"),
                    "report": strategy_report,
                },
            )

            for name, s in segments.items():
                conn.execute(
                    text(
                        "INSERT INTO marketing_segments "
                        "(run_id, segment_name, customer_count, customer_pct, revenue, "
                        "revenue_pct, avg_monetary, avg_frequency, avg_recency_days) "
                        "VALUES (:run_id, :name, :cnt, :cpct, :rev, :rpct, :am, :af, :ar)"
                    ),
                    {
                        "run_id": run_id,
                        "name": name,
                        "cnt": s.get("count"),
                        "cpct": s.get("customer_pct"),
                        "rev": s.get("revenue"),
                        "rpct": s.get("revenue_pct"),
                        "am": s.get("avg_monetary"),
                        "af": s.get("avg_frequency"),
                        "ar": s.get("avg_recency_days"),
                    },
                )

            for c in campaigns:
                conn.execute(
                    text(
                        "INSERT INTO marketing_campaigns "
                        "(run_id, name, target_segment, objective, channel, offer, "
                        "message_angle, primary_kpi, expected_impact, "
                        "budget_allocation_pct, priority) "
                        "VALUES (:run_id, :name, :seg, :obj, :ch, :offer, :angle, "
                        ":kpi, :impact, :budget, :prio)"
                    ),
                    {
                        "run_id": run_id,
                        "name": c.get("name"),
                        "seg": c.get("target_segment"),
                        "obj": c.get("objective"),
                        "ch": c.get("channel"),
                        "offer": c.get("offer"),
                        "angle": c.get("message_angle"),
                        "kpi": c.get("primary_kpi"),
                        "impact": c.get("expected_impact"),
                        "budget": _as_float(c.get("budget_allocation_pct")),
                        "prio": c.get("priority"),
                    },
                )

        return {"status": "stored", "run_id": run_id, "segments": len(segments),
                "campaigns": len(campaigns)}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def _as_float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
