"""
Visualization Agent — LangGraph graph for generating Plotly charts.

Supports two data sources:
  1. PostgreSQL table (via table_name, loads on each execution)
  2. In-memory DataFrame (via df field in VizState, for post-cleaning data)

Flow: retrieve_schema → coder → executor → END (retry coder on failure)
"""

from __future__ import annotations

import io
import re
import traceback
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from typing import Any

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from langgraph.graph import END, StateGraph

from agents.visualization.theme import style_figure
from agents.visualization.viz_models import VizState
from tools.db_tools import get_schema_info, load_df_from_pg
from tools.llm_client import complete
from tools.sandbox import SAFE_BUILTINS, sanitize_fig

MAX_RETRIES = 2
_EXEC_TIMEOUT = 30


def _exec_in_thread(code: str, g: dict) -> None:
    exec(code, g)


def _retrieve_schema(state: VizState) -> dict:
    if state.get("schema_info"):
        return {}

    df = state.get("df")
    if df is not None:
        buf = io.StringIO()
        buf.write(f"DataFrame: {len(df)} rows x {len(df.columns)} columns\n")
        buf.write(f"Columns: {list(df.columns)}\n")
        buf.write(f"Dtypes:\n{df.dtypes.to_string()}\n")
        buf.write(f"\nFirst 5 rows:\n{df.head(5).to_string()}\n")
        num_cols = df.select_dtypes(include="number").columns
        if len(num_cols) > 0:
            buf.write(f"\nNumeric summary:\n{df[num_cols].describe().to_string()}\n")
        return {"schema_info": buf.getvalue()}

    table = state["table_name"]
    schema = get_schema_info(table)
    return {"schema_info": schema}


def _coder(state: VizState) -> dict:
    retry = state.get("retry_count", 0)
    last_error = state.get("last_error", "")

    error_context = ""
    if retry > 0 and last_error:
        error_context = (
            f"\n\nYour previous code failed with this error:\n"
            f"{last_error}\n"
            f"Please fix the issue and try again."
        )

    sys_prompt = (
        "You are a data visualization expert. "
        "You have access to data with this schema:\n\n"
        f"{state['schema_info']}\n\n"
        f"There are already {state['existing_chart_count']} charts in the dashboard.\n\n"
        "The user will ask you to create a new visualization.\n\n"
        "IMPORTANT: Do NOT use import statements. The following are already pre-loaded:\n"
        "- pandas as pd\n"
        "- plotly.express as px\n"
        "- plotly.graph_objects as go\n\n"
        "The data is available in a pandas DataFrame called `df`.\n"
        "Create a figure and assign it to a variable named `fig`. "
        "You can also create subplots or multiple traces — just use `fig` for the main result.\n\n"
        "Examples:\n"
        "- fig = px.bar(df, x='category', y='total', title='Sales by Category')\n"
        "- fig = px.line(df, x='date', y='revenue', title='Revenue Trend')\n"
        "- fig = px.scatter(df, x='price', y='quantity', color='category')\n"
        "- fig = px.pie(df, names='product', values='sales')\n"
        "- fig = px.histogram(df, x='age', nbins=20)\n"
        "- fig = px.box(df, x='department', y='salary')\n\n"
        "IMPORTANT: Wrap your code in ```python ... ``` fences. "
        "Assign the final figure to `fig`."
        f"{error_context}"
    )

    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": state["user_query"]},
    ]

    from agents.constants import DEFAULT_VIZ_MODEL
    model = state.get("model", DEFAULT_VIZ_MODEL)
    raw = complete("viz", messages, model=model, temperature=0.2, max_tokens=2048)

    code_match = re.search(r"```python\n(.*?)```", raw, re.DOTALL)
    if code_match:
        raw = code_match.group(1).strip()

    return {"generated_code": raw}


def _executor(state: VizState) -> dict:
    code = state["generated_code"]
    retry_count = state.get("retry_count", 0)
    buffer = io.StringIO()

    def _sandbox_print(*a, **kw):
        kw["file"] = buffer
        print(*a, **kw)

    df = state.get("df")
    if df is None:
        table = state["table_name"]
        try:
            df = load_df_from_pg(table)
        except Exception as e:
            return {
                "execution_result": {
                    "fig": None, "figures": [], "output": "", "error": f"Failed to load data: {e}",
                },
                "retry_count": retry_count + 1,
                "last_error": str(e),
            }

    g: dict[str, Any] = {
        "pd": pd,
        "px": px,
        "go": go,
        "df": df,
        "__builtins__": {**SAFE_BUILTINS, "print": _sandbox_print},
    }

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_exec_in_thread, code, g)
            future.result(timeout=_EXEC_TIMEOUT)
        out = buffer.getvalue()

        # Collect primary fig + additional fig1..figN
        figs = []
        fig = g.get("fig")
        if fig is not None:
            figs.append(fig)
        i = 1
        while g.get(f"fig{i}") is not None:
            figs.append(g[f"fig{i}"])
            i += 1

        if not figs:
            raise RuntimeError(
                "No figures found after execution. Assign your chart to `fig` or `fig1`, `fig2`, etc."
            )

        sanitized_figs = [style_figure(sanitize_fig(f)) for f in figs]
        return {
            "execution_result": {"fig": sanitized_figs[0], "figures": sanitized_figs, "output": out, "error": ""},
        }
    except TimeoutError:
        return {
            "execution_result": {"fig": None, "figures": [], "output": "", "error": "Execution timed out (>30s)"},
            "retry_count": retry_count + 1,
            "last_error": "Execution timed out (>30s)",
        }
    except Exception:
        tb = traceback.format_exc()
        return {
            "execution_result": {"fig": None, "figures": [], "output": buffer.getvalue(), "error": tb},
            "retry_count": retry_count + 1,
            "last_error": tb,
        }


def _route_after_executor(state: VizState) -> str:
    retry = state.get("retry_count", 0)
    result = state.get("execution_result", {})
    if result.get("error") and retry < MAX_RETRIES:
        return "coder"
    return "end"


def build_viz_graph():
    graph = StateGraph(VizState)
    graph.add_node("retrieve_schema", _retrieve_schema)
    graph.add_node("coder", _coder)
    graph.add_node("executor", _executor)

    graph.set_entry_point("retrieve_schema")
    graph.add_edge("retrieve_schema", "coder")
    graph.add_edge("coder", "executor")

    graph.add_conditional_edges(
        "executor",
        _route_after_executor,
        {"coder": "coder", "end": END},
    )

    return graph.compile()
