"""
Step 8 — Business interpretation via LLM only (no forecasting math).
"""

from __future__ import annotations

import json

from agents.forecasting.forecast_models import ForecastState
from tools.llm_client import complete

_SYSTEM = """You are a senior e-commerce analyst. You receive structured forecast JSON.
Write business language only — do NOT invent numbers not in the JSON.

CRITICAL — respect the `reliability` field. It is the result of out-of-sample
validation, and your narrative must not contradict it:
- "reliable": write normally.
- "indicative": hedge explicitly. Say the direction is informative but the exact
  figures are approximate. Recommend actions that are robust to being wrong.
- "unreliable": the forecast FAILED validation. Your summary must open by saying
  so plainly. Do NOT recommend acting on the numbers. Your recommended_actions
  should be about getting a better forecast (collect more history, forecast at
  the granularity in `recommended_granularity`, investigate the data-quality
  issues in `notes`) — not about inventory, pricing or campaigns.

Never present a figure as dependable when `reliability` says it is not. An
executive acting on a number this system has measured to be unreliable is a
worse outcome than no forecast at all.

Return valid JSON:
{
  "business_summary": "2-3 sentences for executives",
  "risks": ["2-4 items"],
  "opportunities": ["2-4 items"],
  "recommended_actions": ["2-4 concrete actions"]
}
"""


def _interpret_one(record: dict, business_context: str, model: str) -> dict:
    """Enrich a single forecast record with LLM narrative. Returns the enriched record."""
    ctx = {k: record.get(k) for k in (
        "metric", "current_value", "forecasted_value", "change_percent",
        "confidence_score", "selected_model", "evaluation", "key_drivers",
        "business_impact", "trend", "horizons",
        # The validation verdict and its caveats travel with the numbers, so the
        # narrative is written against the same evidence the report shows rather
        # than against the point estimate alone.
        "reliability", "reliability_headline", "reliability_reasons",
        "recommended_granularity", "skill_score", "measured_coverage",
        "predictability", "signal_verdict", "aggregation",
        "horizon_total", "horizon_total_lower", "horizon_total_upper",
        "notes",
    )}
    if business_context:
        ctx["business_context"] = business_context
    try:
        raw = complete(
            "forecast",
            [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": json.dumps(ctx, indent=2)},
            ],
            model=model,
            temperature=0.3,
            max_tokens=1024,
            json_mode=True,
        )
        llm = json.loads(raw)
        record = {**record,
                  "business_summary": llm.get("business_summary", ""),
                  "risks": llm.get("risks", []),
                  "opportunities": llm.get("opportunities", []),
                  "recommended_actions": llm.get("recommended_actions", [])}
    except Exception:
        pass
    return record


def _fallback(record: dict) -> dict:
    """Deterministic narrative for when the LLM call fails.

    Mirrors the reliability rules in ``_SYSTEM``: this path runs on every LLM
    outage, so if it stayed generically upbeat then the exact moment the model
    is unavailable would be the moment the product started recommending action
    on forecasts it had measured as unreliable.
    """
    m = record.get("metric", "Metric").replace("_", " ").title()
    chg = record.get("change_percent", 0)
    horizon = str(record.get("forecast_horizon", "30_days")).replace("_", " ")
    level = record.get("reliability", "unknown")
    reasons = record.get("reliability_reasons") or []

    if level == "unreliable":
        summary = (
            f"This {m} forecast did not pass out-of-sample validation and should not "
            f"be planned against. "
            + (reasons[0] if reasons else "")
        ).strip()
        actions = ["Do not plan against these figures until the forecast validates"]
        rec = record.get("recommended_granularity")
        if rec:
            actions.append(f"Re-run the forecast at {rec} granularity, where the signal is stronger")
        actions.append("Collect more history, or investigate the data-quality notes on this run")
        risks = reasons[:3] or ["The model showed no measurable skill over a naive baseline"]
        opportunities = ["Resolving the data-quality issues above would make this metric forecastable"]
    else:
        total = record.get("horizon_total")
        label = "total" if record.get("aggregation") == "sum" else "average"
        headline = (
            f"{m} is projected to a {label} of {total:,.0f} over {horizon} ({chg:+.1f}% "
            f"vs the previous {horizon})."
            if total is not None
            else f"{m} is projected to change by {chg:+.1f}% over {horizon}."
        )
        hedge = (
            " Treat the exact figures as approximate — this forecast passed validation "
            "only marginally." if level == "indicative" else ""
        )
        summary = f"{headline}{hedge} Model: {record.get('selected_model', 'n/a')}."
        risks = record.get("risks") or ["Market shifts may alter actual results"]
        opportunities = record.get("opportunities") or [
            "Use the forecast range, not the point estimate, for inventory and campaign planning"
        ]
        actions = record.get("recommended_actions") or [
            "Compare actuals to forecast weekly and re-run when they diverge"
        ]

    return {
        **record,
        "business_summary": summary,
        "risks": risks,
        "opportunities": opportunities,
        "recommended_actions": actions,
    }


def _confidence_statement(record: dict) -> str:
    """One line stating what the confidence number rests on — and its limits."""
    conf = record.get("confidence_score")
    if conf is None:
        return "Not back-tested (insufficient history for cross-validation)."

    parts = [f"Confidence {conf:.0%}, from out-of-sample back-test error"]
    skill = record.get("skill_score")
    if skill is not None:
        parts.append(
            f"{skill * 100:+.0f}% skill vs the best naive baseline"
        )
    cov = record.get("measured_coverage")
    if cov is not None:
        parts.append(f"the 80% interval covered {cov:.0%} of back-test outcomes")
    statement = ", ".join(parts) + "."
    if record.get("reliability") == "unreliable":
        statement += " This forecast failed reliability checks — do not plan against it."
    elif record.get("reliability") == "indicative":
        statement += " Directionally useful; treat exact figures as approximate."
    return statement


def interpret_node(state: ForecastState) -> dict:
    """Enrich all forecast outputs with LLM business narrative (batch)."""
    outputs = list(state.get("forecast_outputs", []))
    if not outputs:
        return {}

    from agents.constants import DEFAULT_FORECAST_MODEL

    # NOTE: state["model"] is the *forecasting engine* selector ("Auto",
    # "Prophet", "ETS", "ARIMA", ...) set by ForecastPipeline.run()'s
    # model_override — not an LLM model id. Using it here for the `complete()`
    # call would send e.g. "auto" as a Groq model name and fail every time
    # (previously masked by the provider-fallback chain, which made it look
    # harmless until a Groq-only deployment silently lost real narratives to
    # the generic `_fallback()` text on every run). The LLM model id, when the
    # caller wants one other than the default, comes through the distinct
    # `llm_model` state key instead.
    model = state.get("llm_model") or DEFAULT_FORECAST_MODEL
    business_context = state.get("business_context", "")

    enriched: list[dict] = []
    interpretations: list[dict] = []

    for record in outputs:
        record = dict(record)
        record = _interpret_one(record, business_context, model)
        if not record.get("business_summary"):
            record = _fallback(record)
        enriched.append(record)
        interpretations.append({
            "metric": record.get("metric", ""),
            "executive_summary": record.get("business_summary", ""),
            "risks": record.get("risks", []),
            "opportunities": record.get("opportunities", []),
            "recommended_actions": record.get("recommended_actions", []),
            "confidence_statement": _confidence_statement(record),
            "reliability": record.get("reliability", "unknown"),
        })

    return {"forecast_outputs": enriched, "interpretations": interpretations}
