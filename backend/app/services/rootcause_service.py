"""Root-cause analysis for one project's saved data.

Thin by design: the analysis itself lives in :mod:`agents.rootcause`, which
knows nothing about HTTP, projects or users. This layer resolves the project's
semantic view, forwards the request and makes the result JSON-safe.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from agents.rootcause.engine import describe_options, run_root_cause
from agents.rootcause.schema import RootCauseResult
from backend.app.services.json_safe import json_safe


def analyse(df: pd.DataFrame, request: dict[str, Any]) -> dict[str, Any]:
    result: RootCauseResult = run_root_cause(
        df,
        measure=request.get("measure", "revenue"),
        window_mode=request.get("window_mode", "auto"),
        current_start=request.get("current_start"),
        current_end=request.get("current_end"),
        prior_start=request.get("prior_start"),
        prior_end=request.get("prior_end"),
        dimensions=request.get("dimensions"),
        include_weekday=bool(request.get("include_weekday", False)),
        max_depth=int(request.get("max_depth", 3)),
        beam_width=int(request.get("beam_width", 48)),
        top_k=int(request.get("top_k", 6)),
        min_explanatory_power=float(request.get("min_explanatory_power", 0.08)),
        min_signal_to_noise=float(request.get("min_signal_to_noise", 2.0)),
        with_narrative=bool(request.get("with_narrative", True)),
        model=request.get("model"),
        language=request.get("language", "en"),
        business_context=request.get("business_context", ""),
    )
    return json_safe(result.as_dict())


def options(df: pd.DataFrame, language: str = "en") -> dict[str, Any]:
    return json_safe(describe_options(df, language))
