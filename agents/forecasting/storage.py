"""
Step 9 — PostgreSQL persistence for forecast artifacts.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from agents.forecasting.forecast_models import ForecastState


def _generate_run_id() -> str:
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    import random
    suffix = "".join(random.choices("abcdefghijklmnopqrstuvwxyz0123456789", k=6))
    return f"fc_{ts}_{suffix}"


def _ensure_tables() -> bool:
    from tools.db_tools import get_engine, is_pg_configured

    if not is_pg_configured():
        return False

    engine = get_engine()
    ddl = """
    CREATE TABLE IF NOT EXISTS forecast_runs (
        run_id TEXT PRIMARY KEY,
        project_id TEXT,
        table_name TEXT,
        horizon_days INTEGER,
        frequency TEXT,
        date_column TEXT,
        target_count INTEGER,
        status TEXT DEFAULT 'completed',
        created_at TIMESTAMP DEFAULT NOW()
    );
    ALTER TABLE forecast_runs ADD COLUMN IF NOT EXISTS project_id TEXT;
    CREATE TABLE IF NOT EXISTS forecasts (
        id SERIAL PRIMARY KEY,
        run_id TEXT REFERENCES forecast_runs(run_id),
        metric_name TEXT,
        current_value DOUBLE PRECISION,
        forecasted_value DOUBLE PRECISION,
        change_percent DOUBLE PRECISION,
        confidence_score DOUBLE PRECISION,
        selected_model TEXT,
        forecast_horizon TEXT,
        business_impact TEXT,
        trend TEXT,
        horizons_json JSONB DEFAULT '{}'::jsonb,
        created_at TIMESTAMP DEFAULT NOW()
    );
    CREATE TABLE IF NOT EXISTS forecast_evaluations (
        id SERIAL PRIMARY KEY,
        run_id TEXT,
        metric_name TEXT,
        mae DOUBLE PRECISION,
        rmse DOUBLE PRECISION,
        mape DOUBLE PRECISION,
        created_at TIMESTAMP DEFAULT NOW()
    );
    CREATE TABLE IF NOT EXISTS forecast_explanations (
        id SERIAL PRIMARY KEY,
        run_id TEXT,
        metric_name TEXT,
        business_summary TEXT,
        risks JSONB DEFAULT '[]'::jsonb,
        opportunities JSONB DEFAULT '[]'::jsonb,
        recommended_actions JSONB DEFAULT '[]'::jsonb,
        key_drivers JSONB DEFAULT '[]'::jsonb,
        confidence_statement TEXT,
        created_at TIMESTAMP DEFAULT NOW()
    );
    CREATE TABLE IF NOT EXISTS forecast_history (
        id SERIAL PRIMARY KEY,
        run_id TEXT,
        metric_name TEXT,
        ds TIMESTAMP,
        y DOUBLE PRECISION,
        yhat_lower DOUBLE PRECISION,
        yhat_upper DOUBLE PRECISION,
        kind TEXT,
        created_at TIMESTAMP DEFAULT NOW()
    );
  """
    with engine.begin() as conn:
        for stmt in ddl.split(";"):
            s = stmt.strip()
            if s:
                conn.execute(text(s))
    return True


def save_forecast_run(result: dict, *, project_id: str | None = None) -> dict:
    run_id = result.get("run_id") or _generate_run_id()
    outputs = result.get("forecast_outputs", [])
    evaluations = {e["metric"]: e for e in result.get("forecast_evaluations", [])}
    history = result.get("forecast_history_rows", [])

    try:
        if not _ensure_tables():
            return {"status": "skipped", "reason": "PostgreSQL not configured"}

        from tools.db_tools import get_engine

        engine = get_engine()
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO forecast_runs "
                    "(run_id, project_id, table_name, horizon_days, frequency, date_column, target_count) "
                    "VALUES (:run_id, :project_id, :table, :horizon, :freq, :date_col, :n)"
                ),
                {
                    "run_id": run_id,
                    "project_id": project_id,
                    "table": result.get("table_name", ""),
                    "horizon": result.get("horizon_days", 30),
                    "freq": result.get("frequency", ""),
                    "date_col": result.get("date_column", ""),
                    "n": len(outputs),
                },
            )

            for rec in outputs:
                metric = rec.get("metric", "")
                ev = rec.get("evaluation") or evaluations.get(metric, {})
                conn.execute(
                    text(
                        "INSERT INTO forecasts "
                        "(run_id, metric_name, current_value, forecasted_value, change_percent, "
                        "confidence_score, selected_model, forecast_horizon, business_impact, trend, horizons_json) "
                        "VALUES (:run_id, :metric, :cur, :fc, :chg, :conf, :model, :hor, :impact, :trend, :horizons)"
                    ),
                    {
                        "run_id": run_id,
                        "metric": metric,
                        "cur": rec.get("current_value"),
                        "fc": rec.get("forecasted_value"),
                        "chg": rec.get("change_percent"),
                        "conf": rec.get("confidence_score"),
                        "model": rec.get("selected_model"),
                        "hor": rec.get("forecast_horizon"),
                        "impact": rec.get("business_impact"),
                        "trend": rec.get("trend"),
                        "horizons": json.dumps(rec.get("horizons", {})),
                    },
                )
                conn.execute(
                    text(
                        "INSERT INTO forecast_evaluations (run_id, metric_name, mae, rmse, mape) "
                        "VALUES (:run_id, :metric, :mae, :rmse, :mape)"
                    ),
                    {
                        "run_id": run_id,
                        "metric": metric,
                        "mae": ev.get("mae"),
                        "rmse": ev.get("rmse"),
                        "mape": ev.get("mape"),
                    },
                )
                conn.execute(
                    text(
                        "INSERT INTO forecast_explanations "
                        "(run_id, metric_name, business_summary, risks, opportunities, "
                        "recommended_actions, key_drivers, confidence_statement) "
                        "VALUES (:run_id, :metric, :summary, :risks, :opps, :actions, :drivers, :conf)"
                    ),
                    {
                        "run_id": run_id,
                        "metric": metric,
                        "summary": rec.get("business_summary", ""),
                        "risks": json.dumps(rec.get("risks", [])),
                        "opps": json.dumps(rec.get("opportunities", [])),
                        "actions": json.dumps(rec.get("recommended_actions", [])),
                        "drivers": json.dumps(rec.get("key_drivers", [])),
                        "conf": f"Confidence {rec.get('confidence_score', 0):.0%}",
                    },
                )

            for row in history:
                conn.execute(
                    text(
                        "INSERT INTO forecast_history "
                        "(run_id, metric_name, ds, y, yhat_lower, yhat_upper, kind) "
                        "VALUES (:run_id, :metric, :ds, :y, :lo, :hi, :kind)"
                    ),
                    {
                        "run_id": row.get("run_id", run_id),
                        "metric": row.get("metric_name"),
                        "ds": row.get("ds"),
                        "y": row.get("y"),
                        "lo": row.get("yhat_lower"),
                        "hi": row.get("yhat_upper"),
                        "kind": row.get("kind", "actual"),
                    },
                )

        return {"status": "stored", "run_id": run_id, "targets": len(outputs)}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def load_prior_forecast(
    metric: str,
    project_id: str | None = None,
    exclude_run_id: str | None = None,
) -> tuple[Any | None, float | None]:
    """Most recent previously-issued forecast for ``metric``, and its back-test MAE.

    This is what makes drift detection possible: to know whether a model still
    works you have to compare what it *said would happen* against what happened,
    and that requires the earlier prediction to still exist. Returns a
    ``(DataFrame[ds, yhat, yhat_lower, yhat_upper], expected_mae)`` pair, or
    ``(None, None)`` when there is no usable history — no Postgres, no earlier
    run, or an earlier run that stored no forecast rows.

    Never raises: a missing or unreachable history store must degrade the run to
    "no drift information", not fail it.
    """
    try:
        import pandas as pd

        from tools.db_tools import get_engine, is_pg_configured

        if not is_pg_configured():
            return None, None

        engine = get_engine()
        with engine.connect() as conn:
            run_row = conn.execute(
                text(
                    "SELECT h.run_id FROM forecast_history h "
                    "JOIN forecast_runs r ON r.run_id = h.run_id "
                    "WHERE h.metric_name = :metric AND h.kind = 'forecast' "
                    "  AND (:project IS NULL OR r.project_id = :project) "
                    "  AND (:exclude IS NULL OR h.run_id <> :exclude) "
                    "ORDER BY r.created_at DESC LIMIT 1"
                ),
                {"metric": metric, "project": project_id, "exclude": exclude_run_id},
            ).fetchone()
            if not run_row:
                return None, None
            prior_run = run_row[0]

            rows = conn.execute(
                text(
                    "SELECT ds, y, yhat_lower, yhat_upper FROM forecast_history "
                    "WHERE run_id = :run AND metric_name = :metric AND kind = 'forecast' "
                    "ORDER BY ds"
                ),
                {"run": prior_run, "metric": metric},
            ).fetchall()
            if not rows:
                return None, None

            mae_row = conn.execute(
                text(
                    "SELECT mae FROM forecast_evaluations "
                    "WHERE run_id = :run AND metric_name = :metric LIMIT 1"
                ),
                {"run": prior_run, "metric": metric},
            ).fetchone()

        # `y` holds the predicted value on kind='forecast' rows.
        frame = pd.DataFrame(
            [{"ds": r[0], "yhat": r[1], "yhat_lower": r[2], "yhat_upper": r[3]} for r in rows]
        )
        expected_mae = float(mae_row[0]) if mae_row and mae_row[0] is not None else None
        return frame, expected_mae
    except Exception:
        return None, None


def store_node(state: ForecastState) -> dict:
    run_id = state.get("run_id") or _generate_run_id()
    project_id = state.get("project_id")
    outputs = state.get("forecast_outputs", [])
    evaluations = {e["metric"]: e for e in state.get("forecast_evaluations", [])}
    history = state.get("forecast_history_rows", [])

    try:
        if not _ensure_tables():
            return {
                "storage_result": {"status": "skipped", "reason": "PostgreSQL not configured"},
                "run_id": run_id,
            }

        from tools.db_tools import get_engine

        engine = get_engine()
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO forecast_runs "
                    "(run_id, project_id, table_name, horizon_days, frequency, date_column, target_count) "
                    "VALUES (:run_id, :project_id, :table, :horizon, :freq, :date_col, :n)"
                ),
                {
                    "run_id": run_id,
                    "project_id": project_id,
                    "table": state.get("table_name", ""),
                    "horizon": state.get("horizon_days", 30),
                    "freq": state.get("frequency", ""),
                    "date_col": state.get("date_column", ""),
                    "n": len(outputs),
                },
            )

            for rec in outputs:
                metric = rec.get("metric", "")
                ev = rec.get("evaluation") or evaluations.get(metric, {})
                conn.execute(
                    text(
                        "INSERT INTO forecasts "
                        "(run_id, metric_name, current_value, forecasted_value, change_percent, "
                        "confidence_score, selected_model, forecast_horizon, business_impact, trend, horizons_json) "
                        "VALUES (:run_id, :metric, :cur, :fc, :chg, :conf, :model, :hor, :impact, :trend, :horizons)"
                    ),
                    {
                        "run_id": run_id,
                        "metric": metric,
                        "cur": rec.get("current_value"),
                        "fc": rec.get("forecasted_value"),
                        "chg": rec.get("change_percent"),
                        "conf": rec.get("confidence_score"),
                        "model": rec.get("selected_model"),
                        "hor": rec.get("forecast_horizon"),
                        "impact": rec.get("business_impact"),
                        "trend": rec.get("trend"),
                        "horizons": json.dumps(rec.get("horizons", {})),
                    },
                )
                conn.execute(
                    text(
                        "INSERT INTO forecast_evaluations (run_id, metric_name, mae, rmse, mape) "
                        "VALUES (:run_id, :metric, :mae, :rmse, :mape)"
                    ),
                    {
                        "run_id": run_id,
                        "metric": metric,
                        "mae": ev.get("mae"),
                        "rmse": ev.get("rmse"),
                        "mape": ev.get("mape"),
                    },
                )
                conn.execute(
                    text(
                        "INSERT INTO forecast_explanations "
                        "(run_id, metric_name, business_summary, risks, opportunities, "
                        "recommended_actions, key_drivers, confidence_statement) "
                        "VALUES (:run_id, :metric, :summary, :risks, :opps, :actions, :drivers, :conf)"
                    ),
                    {
                        "run_id": run_id,
                        "metric": metric,
                        "summary": rec.get("business_summary", ""),
                        "risks": json.dumps(rec.get("risks", [])),
                        "opps": json.dumps(rec.get("opportunities", [])),
                        "actions": json.dumps(rec.get("recommended_actions", [])),
                        "drivers": json.dumps(rec.get("key_drivers", [])),
                        "conf": f"Confidence {rec.get('confidence_score', 0):.0%}",
                    },
                )

            for row in history:
                conn.execute(
                    text(
                        "INSERT INTO forecast_history "
                        "(run_id, metric_name, ds, y, yhat_lower, yhat_upper, kind) "
                        "VALUES (:run_id, :metric, :ds, :y, :lo, :hi, :kind)"
                    ),
                    {
                        "run_id": row.get("run_id", run_id),
                        "metric": row.get("metric_name"),
                        "ds": row.get("ds"),
                        "y": row.get("y"),
                        "lo": row.get("yhat_lower"),
                        "hi": row.get("yhat_upper"),
                        "kind": row.get("kind", "actual"),
                    },
                )

        return {
            "storage_result": {"status": "stored", "run_id": run_id, "targets": len(outputs)},
            "run_id": run_id,
        }
    except Exception as exc:
        return {
            "storage_result": {"status": "error", "error": str(exc)},
            "run_id": run_id,
        }
