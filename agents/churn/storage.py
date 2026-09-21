"""
PostgreSQL persistence for churn runs (mirrors the forecasting/marketing pattern).

Creates tables on demand and saves a churn run (model metrics + per-customer
scores). Silently skips when PostgreSQL is not configured.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime

from sqlalchemy import text


def _generate_run_id() -> str:
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    suffix = "".join(random.choices("abcdefghijklmnopqrstuvwxyz0123456789", k=6))
    return f"churn_{ts}_{suffix}"


def _ensure_tables() -> bool:
    from tools.db_tools import get_engine, is_pg_configured

    if not is_pg_configured():
        return False

    engine = get_engine()
    ddl = """
    CREATE TABLE IF NOT EXISTS churn_runs (
        run_id TEXT PRIMARY KEY,
        project_id TEXT,
        table_name TEXT,
        horizon_days INTEGER,
        snapshot_date TEXT,
        model_name TEXT,
        auc DOUBLE PRECISION,
        base_churn_rate DOUBLE PRECISION,
        customers_scored INTEGER,
        high_risk INTEGER,
        revenue_at_risk DOUBLE PRECISION,
        created_at TIMESTAMP DEFAULT NOW()
    );
    ALTER TABLE churn_runs ADD COLUMN IF NOT EXISTS project_id TEXT;
    CREATE TABLE IF NOT EXISTS churn_scores (
        id SERIAL PRIMARY KEY,
        run_id TEXT REFERENCES churn_runs(run_id),
        customer TEXT,
        customer_name TEXT,
        churn_probability DOUBLE PRECISION,
        risk_tier TEXT,
        recency_days INTEGER,
        frequency INTEGER,
        monetary DOUBLE PRECISION,
        created_at TIMESTAMP DEFAULT NOW()
    );
    """
    with engine.begin() as conn:
        for stmt in ddl.split(";"):
            s = stmt.strip()
            if s:
                conn.execute(text(s))
    return True


def save_churn_run(
    payload: dict,
    *,
    table_name: str = "",
    run_id: str | None = None,
    project_id: str | None = None,
) -> dict:
    """Persist a churn run. Returns a status dict (mirrors forecasting/marketing)."""
    if not payload.get("available"):
        return {"status": "skipped", "reason": "no churn payload"}

    run_id = run_id or _generate_run_id()
    model = payload.get("model", {})
    scores = payload.get("at_risk_customers", [])

    try:
        if not _ensure_tables():
            return {"status": "skipped", "reason": "PostgreSQL not configured"}

        from tools.db_tools import get_engine

        engine = get_engine()
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO churn_runs "
                    "(run_id, project_id, table_name, horizon_days, snapshot_date, model_name, auc, "
                    "base_churn_rate, customers_scored, high_risk, revenue_at_risk) "
                    "VALUES (:run_id, :project_id, :table, :hor, :snap, :model, :auc, :base, :n, :high, :rev)"
                ),
                {
                    "run_id": run_id,
                    "project_id": project_id,
                    "table": table_name,
                    "hor": payload.get("horizon_days"),
                    "snap": payload.get("snapshot_date"),
                    "model": model.get("name"),
                    "auc": model.get("auc"),
                    "base": model.get("base_churn_rate"),
                    "n": payload.get("customers_scored"),
                    "high": payload.get("risk_distribution", {}).get("high"),
                    "rev": payload.get("revenue_at_risk"),
                },
            )
            for s in scores:
                conn.execute(
                    text(
                        "INSERT INTO churn_scores "
                        "(run_id, customer, customer_name, churn_probability, risk_tier, "
                        "recency_days, frequency, monetary) "
                        "VALUES (:run_id, :cust, :name, :prob, :tier, :rec, :freq, :mon)"
                    ),
                    {
                        "run_id": run_id,
                        "cust": s.get("customer"),
                        "name": s.get("name"),
                        "prob": s.get("churn_probability"),
                        "tier": s.get("risk_tier"),
                        "rec": s.get("recency_days"),
                        "freq": s.get("frequency"),
                        "mon": s.get("monetary"),
                    },
                )
        return {"status": "stored", "run_id": run_id, "scores": len(scores)}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}
