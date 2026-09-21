"""
Sandbox execution utilities for LLM-generated code.

Provides safe code execution with restricted globals, stdout capture,
and automatic figure extraction for Plotly charts.

Security: no __import__, no file I/O, no subprocess, no network access.
Output is capped at 1 MB; execution times out after 30 seconds.
"""

from __future__ import annotations

import base64
import io
import re
import traceback
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from typing import Any

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

SAFE_BUILTINS: dict[str, Any] = {
    "range": range,
    "len": len,
    "int": int,
    "float": float,
    "str": str,
    "bool": bool,
    "list": list,
    "dict": dict,
    "tuple": tuple,
    "set": set,
    "round": round,
    "min": min,
    "max": max,
    "abs": abs,
    "sum": sum,
    "sorted": sorted,
    "enumerate": enumerate,
    "zip": zip,
    "isinstance": isinstance,
    "hasattr": hasattr,
    # getattr is intentionally omitted — it can be used to walk __class__.__mro__
    "type": type,
    "filter": filter,
    "map": map,
    "any": any,
    "all": all,
    "ValueError": ValueError,
    "TypeError": TypeError,
    "KeyError": KeyError,
    "Exception": Exception,
}

_BLOCKED_PATTERNS = [
    "__class__", "__bases__", "__subclasses__", "__globals__",
    "__builtins__", "__import__", "os.system", "subprocess", "socket",
]

# Callables that must not be invoked. Matched as whole identifiers so that a
# legitimate string or column name containing them — a column literally named
# "open(x)", say — is not mistaken for a sandbox escape, while a real call
# still is. Plain substring matching rejected valid cleaning code.
_BLOCKED_CALLS = ["eval", "exec", "compile", "open", "input", "globals", "locals", "getattr"]
_BLOCKED_CALL_RE = re.compile(
    r"(?<![\w.])(" + "|".join(_BLOCKED_CALLS) + r")\s*\(", re.MULTILINE
)


def _check_code_safety(code: str) -> str | None:
    """Return an error string if the code contains a blocked pattern, else None."""
    for pattern in _BLOCKED_PATTERNS:
        if pattern in code:
            return f"Blocked: code contains disallowed pattern '{pattern}'"
    call = _BLOCKED_CALL_RE.search(code)
    if call:
        return f"Blocked: code calls the disallowed function '{call.group(1)}'"
    return None


def strip_fences(code: str) -> str:
    """Strip ```python ... ``` fences from LLM-generated code."""
    code = code.strip()
    code = re.sub(r"^```(?:python)?\s*\n?", "", code)
    code = re.sub(r"\n?```\s*$", "", code)
    return code.strip()


_EXEC_TIMEOUT = 30
_MAX_OUTPUT_SIZE = 1_048_576


def _exec_in_thread(code: str, g: dict) -> None:
    exec(code, g)


def run_with_timeout(fn, *args, timeout: float = _EXEC_TIMEOUT, **kwargs):
    """Run ``fn(*args, **kwargs)`` in a worker thread, raising
    ``concurrent.futures.TimeoutError`` if it exceeds ``timeout`` seconds.

    Shared by ``run_analysis`` (chat/viz code-exec) and
    ``agents/cleaning/executor.py`` (cleaning code-exec) so every surface
    that runs LLM-generated code gets the same timeout protection against
    runaway or hanging generated code.

    Deliberately does NOT use ``ThreadPoolExecutor`` as a context manager:
    ``__exit__`` calls ``shutdown(wait=True)``, which blocks until the worker
    thread actually finishes — silently defeating the timeout for code that's
    merely slow (the caller would still wait out the full runtime, just to
    be told afterwards that it "timed out"). Shutting down with
    ``wait=False`` lets a still-running thread finish on its own in the
    background while control returns to the caller at ``timeout`` seconds,
    as the name promises. (A truly non-terminating loop still can't be
    force-killed — Python can't do that to a thread — that's a Python
    limitation no amount of code here can fix; full isolation needs a
    subprocess, tracked separately as sandbox hardening.)
    """
    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(fn, *args, **kwargs)
    try:
        return future.result(timeout=timeout)
    finally:
        pool.shutdown(wait=False)


def run_analysis(code: str, df: pd.DataFrame) -> dict:
    """
    Execute LLM-generated code on a DataFrame copy.

    Returns {output, error, df, fig} where *fig* is only present
    when the code assigned a Plotly figure to a variable named ``fig``.
    Times out after 30 seconds; output capped at 1 MB.
    """
    code = strip_fences(code)
    safety_error = _check_code_safety(code)
    if safety_error:
        return {"output": "", "error": safety_error, "df": df}
    buffer = io.StringIO()

    def _sandbox_print(*a, **kw):
        kw["file"] = buffer
        print(*a, **kw)

    g: dict[str, Any] = {
        "pd": pd,
        "np": np,
        "px": px,
        "go": go,
        "df": df.copy(),
        "__builtins__": {**SAFE_BUILTINS, "print": _sandbox_print},
    }
    try:
        run_with_timeout(_exec_in_thread, code, g)

        out = buffer.getvalue()
        if len(out) > _MAX_OUTPUT_SIZE:
            out = out[:_MAX_OUTPUT_SIZE] + "\n... (truncated)"
        new_df = g.get("df", df)
        fig = g.get("fig")
        result: dict[str, Any] = {"output": out, "error": None, "df": new_df}
        if fig is not None:
            result["fig"] = fig
        return result
    except TimeoutError:
        return {"output": buffer.getvalue(), "error": "Execution timed out (>30s)", "df": df}
    except Exception:
        return {"output": buffer.getvalue(), "error": traceback.format_exc(), "df": df}


def df_context(df: pd.DataFrame) -> str:
    """Short textual summary of a DataFrame for LLM context."""
    buf = io.StringIO()
    buf.write(f"Shape: {df.shape[0]} rows x {df.shape[1]} columns\n")
    buf.write(f"Columns: {list(df.columns)}\n")
    buf.write(f"Dtypes:\n{df.dtypes.to_string()}\n")
    buf.write(f"\nFirst 5 rows:\n{df.head(5).to_string()}\n")
    if len(df) > 0:
        num_cols = df.select_dtypes(include="number").columns
        if len(num_cols) > 0:
            buf.write(f"\nNumeric summary:\n{df[num_cols].describe().to_string()}\n")
    return buf.getvalue()


def _convert_value(v):
    """Recursively convert a value to a JSON-safe type."""
    if isinstance(v, pd.Period):
        return str(v)
    if isinstance(v, pd.Timestamp):
        return v.isoformat()
    if isinstance(v, pd.Timedelta):
        return str(v)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v)
    if isinstance(v, dict):
        return {k: _convert_value(val) for k, val in v.items()}
    if isinstance(v, list):
        return [_convert_value(item) for item in v]
    if isinstance(v, tuple):
        return tuple(_convert_value(item) for item in v)
    return v


def sanitize_fig(fig):
    """Convert a Plotly figure in-place, replacing non-JSON-serializable
    types (e.g. pandas Period) with their string representations."""
    d = fig.to_dict()
    cleaned = _convert_value(d)
    fig.update(data=cleaned.get("data", fig.data), layout=cleaned.get("layout", fig.layout))
    if "frames" in cleaned:
        fig.frames = cleaned["frames"]
    return fig


_XY_TRACE_TYPES = {"bar", "scatter", "scattergl", "histogram", "box"}
_PROPORTIONAL_TRACE_TYPES = {"pie", "treemap", "sunburst"}


def _decode_plotly_array(value: Any) -> list:
    """Decode a Plotly ``to_json()`` array field into a plain Python list.

    Recent Plotly versions serialize numeric arrays as a compact typed-array
    encoding — ``{"dtype": "f8", "bdata": <base64>[, "shape": "r,c"]}`` —
    instead of a plain JSON list. ``react-plotly.js`` decodes this natively
    in the browser, but any server-side code reading trace data (like this
    summarizer) must decode it explicitly or every numeric stat silently
    comes out empty.
    """
    if isinstance(value, dict) and "bdata" in value:
        try:
            raw = base64.b64decode(value["bdata"])
            arr = np.frombuffer(raw, dtype=value.get("dtype") or "f8")
            shape = value.get("shape")
            if shape:
                dims = tuple(int(d) for d in str(shape).split(","))
                arr = arr.reshape(dims)
            return arr.tolist()
        except Exception:
            return []
    if isinstance(value, list):
        return [_decode_plotly_array(v) if isinstance(v, dict) else v for v in value]
    if value is None:
        return []
    return value


def _title_text(value: Any) -> str | None:
    if isinstance(value, dict):
        return value.get("text")
    if isinstance(value, str):
        return value
    return None


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _numeric_stats(values: list) -> dict[str, Any] | None:
    nums = [v for v in values if _is_number(v)]
    if not nums:
        return None
    return {
        "count": len(nums),
        "min": min(nums),
        "max": max(nums),
        "mean": round(sum(nums) / len(nums), 4),
        "first": nums[0],
        "last": nums[-1],
    }


def _categorical_sample(values: list, max_categories: int) -> dict[str, Any]:
    str_values = [str(v) for v in values]
    total = len(str_values)
    if total <= max_categories:
        sample = str_values
    else:
        half = max(1, max_categories // 2)
        sample = str_values[:half] + ["..."] + str_values[-half:]
    return {"categories_sample": sample, "category_count": total}


def _summarize_xy_trace(trace: dict, max_categories: int) -> dict[str, Any]:
    entry: dict[str, Any] = {}
    y = _decode_plotly_array(trace.get("y"))
    x = _decode_plotly_array(trace.get("x"))

    y_is_numeric = any(_is_number(v) for v in y)
    numeric_source, cat_source = (y, x) if y_is_numeric else (x, y)

    stats = _numeric_stats(numeric_source)
    if stats:
        if trace.get("type") == "box":
            nums = sorted(v for v in numeric_source if _is_number(v))
            if nums:
                stats["median"] = nums[len(nums) // 2]
        entry.update(stats)

    if cat_source and not all(_is_number(v) for v in cat_source):
        entry.update(_categorical_sample(cat_source, max_categories))

    return entry


def _summarize_proportional_trace(trace: dict, max_categories: int) -> dict[str, Any]:
    labels = _decode_plotly_array(trace.get("labels"))
    values = _decode_plotly_array(trace.get("values"))
    pairs = [(str(label), v) for label, v in zip(labels, values) if _is_number(v)]
    if not pairs:
        return {}
    pairs.sort(key=lambda p: p[1], reverse=True)
    top = pairs[:max_categories]
    total_value = sum(v for _, v in pairs)
    top_value = sum(v for _, v in top)
    return {
        "segment_count": len(pairs),
        "total_value": round(total_value, 4),
        "top_segments": [{"label": label, "value": round(v, 4)} for label, v in top],
        "other_value": round(total_value - top_value, 4) if len(pairs) > len(top) else 0,
    }


def _summarize_heatmap_trace(trace: dict) -> dict[str, Any]:
    # _decode_plotly_array already recurses into per-row bdata dicts (the
    # z-as-list-of-encoded-rows shape) as well as a single 2D-shaped blob
    # (the z-as-one-bdata-blob-with-"shape" shape), so `z` is a plain nested
    # list either way by the time it gets here.
    z = _decode_plotly_array(trace.get("z"))
    rows = [row for row in z if isinstance(row, list)]
    entry: dict[str, Any] = {"rows": len(rows), "cols": len(rows[0]) if rows else 0}

    flat = [v for row in rows for v in row if _is_number(v)]
    if flat:
        entry["value_min"] = min(flat)
        entry["value_max"] = max(flat)
        entry["value_mean"] = round(sum(flat) / len(flat), 4)

    try:
        x_labels = _decode_plotly_array(trace.get("x"))
        y_labels = _decode_plotly_array(trace.get("y"))
        cells = [
            (v, ri, ci)
            for ri, row in enumerate(rows)
            for ci, v in enumerate(row)
            if _is_number(v)
        ]
        cells.sort(key=lambda c: c[0], reverse=True)
        top_cells = [
            {
                "x": str(x_labels[ci]) if ci < len(x_labels) else str(ci),
                "y": str(y_labels[ri]) if ri < len(y_labels) else str(ri),
                "value": round(v, 4),
            }
            for v, ri, ci in cells[:3]
        ]
        if top_cells:
            entry["top_cells"] = top_cells
    except Exception:
        pass

    return entry


def _summarize_fallback_trace(trace: dict) -> dict[str, Any]:
    for key in ("y", "values", "close", "high", "low", "x"):
        arr = _decode_plotly_array(trace.get(key))
        if arr:
            stats = _numeric_stats(arr)
            return {"field": key, **stats} if stats else {"field": key, "length": len(arr)}
    return {}


def _summarize_trace(trace: dict, max_categories: int) -> dict[str, Any]:
    ttype = trace.get("type", "scatter")
    entry: dict[str, Any] = {"type": ttype}
    if trace.get("name"):
        entry["name"] = trace["name"]
    try:
        if ttype in _XY_TRACE_TYPES:
            entry.update(_summarize_xy_trace(trace, max_categories))
        elif ttype in _PROPORTIONAL_TRACE_TYPES:
            entry.update(_summarize_proportional_trace(trace, max_categories))
        elif ttype == "heatmap":
            entry.update(_summarize_heatmap_trace(trace))
        else:
            entry.update(_summarize_fallback_trace(trace))
    except Exception:
        entry["note"] = "could not summarize this trace"
    return entry


def describe_figure(fig: dict, *, max_traces: int = 8, max_categories: int = 10) -> dict:
    """Reduce a Plotly figure JSON dict (``{"data": [...], "layout": {...}}``)
    to a compact, LLM-safe summary of what it actually shows.

    Structurally bounded regardless of input size — every trace is reduced to
    O(1) scalars or a capped category sample, so a 50-point trace and a
    50,000-point trace produce a similarly small summary. Never raises: a
    trace that can't be summarized degrades to a ``note`` field instead of
    failing the whole figure.
    """
    layout = fig.get("layout") or {}
    traces = fig.get("data") or []
    xaxis = layout.get("xaxis") or {}
    yaxis = layout.get("yaxis") or {}

    return {
        "chart_title": _title_text(layout.get("title")),
        "x_axis_title": _title_text(xaxis.get("title")),
        "y_axis_title": _title_text(yaxis.get("title")),
        "trace_count": len(traces),
        "traces_omitted": max(0, len(traces) - max_traces),
        "traces": [
            _summarize_trace(trace, max_categories)
            for trace in traces[:max_traces]
            if isinstance(trace, dict)
        ],
    }
