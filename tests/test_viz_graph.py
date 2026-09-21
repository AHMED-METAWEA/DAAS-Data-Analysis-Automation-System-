from __future__ import annotations

import pandas as pd

from agents.visualization import viz_graph
from agents.visualization import theme


def _df() -> pd.DataFrame:
    return pd.DataFrame({
        "category": ["A", "B", "C", "A", "B"],
        "revenue": [10.0, 20.0, 30.0, 15.0, 25.0],
    })


def _base_state(**overrides) -> dict:
    state = {
        "user_query": "chart it",
        "schema_info": "cols: category, revenue",
        "table_name": "",
        "model": "test-model",
        "existing_chart_count": 0,
        "generated_code": "",
        "execution_result": {},
        "retry_count": 0,
        "last_error": "",
        "df": _df(),
    }
    state.update(overrides)
    return state


def test_executor_sandbox_excludes_getattr() -> None:
    state = _base_state(generated_code="fig = px.bar(df, x='category', y='revenue')\nx = getattr(df, 'shape')")
    result = viz_graph._executor(state)
    exec_result = result["execution_result"]
    assert exec_result["error"], "getattr must not be available in the sandbox"
    assert "NameError" in exec_result["error"]
    assert "getattr" in exec_result["error"]


def test_executor_applies_smart_analyst_theme() -> None:
    state = _base_state(generated_code="fig = px.bar(df, x='category', y='revenue', title='Revenue by Category')")
    result = viz_graph._executor(state)
    exec_result = result["execution_result"]
    assert not exec_result["error"]
    fig = exec_result["fig"]
    assert list(fig.layout.template.layout.colorway) == theme.SEQUENCE
