"""
Churn Agent — LLM layer.

Turns the deterministic churn payload into a markdown **retention strategy**:
per-risk-tier actions, the main churn drivers (from feature importance), and a
prioritised win-back plan. Grounded in the payload so the model does not invent
numbers. Prompt lives in ``prompts/churn/strategy.md``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from agents.constants import DEFAULT_CHURN_MODEL
from tools.db_tools import get_schema_info
from tools.llm_client import complete
from tools.sandbox import df_context

_PROMPT_DIR = Path(__file__).resolve().parent.parent.parent / "prompts" / "churn"


def _load_prompt(stem: str) -> str:
    return (_PROMPT_DIR / f"{stem}.md").read_text(encoding="utf-8")


def generate_retention_plan(
    churn_payload: dict,
    data_df: pd.DataFrame | None = None,
    table_name: str = "",
    business_context: str = "",
    model: str | None = None,
) -> str:
    """Generate a markdown retention strategy grounded in the churn payload."""
    if data_df is not None:
        schema = df_context(data_df)
    elif table_name:
        schema = get_schema_info(table_name)
    else:
        schema = ""

    system_prompt = _load_prompt("strategy")

    # Trim the per-customer list so the prompt stays compact; the model only
    # needs the headline numbers + a sample of the highest-risk customers.
    # Underscore-prefixed keys are internal (full score maps etc.) — never
    # sent to the LLM.
    slim = {k: v for k, v in churn_payload.items() if not k.startswith("_")}
    slim["at_risk_customers"] = churn_payload.get("at_risk_customers", [])[:15]

    parts = []
    if schema:
        parts.append(f"Dataset overview:\n{schema}\n\n")
    parts.append(
        "Churn analysis payload (use as the foundation; do not invent numbers):\n"
        f"```json\n{json.dumps(slim, indent=2, default=str)}\n```\n\n"
    )
    if business_context:
        parts.append(f"Business context from the user:\n{business_context}\n\n")
    parts.append("Write the retention strategy following the required structure.")

    return complete(
        "churn",
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "".join(parts)},
        ],
        model=model or DEFAULT_CHURN_MODEL,
        temperature=0.4,
        max_tokens=2048,
    )
