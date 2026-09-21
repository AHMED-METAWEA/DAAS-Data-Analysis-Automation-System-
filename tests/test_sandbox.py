from __future__ import annotations

import base64
import json

import numpy as np
import pandas as pd

from tools.sandbox import describe_figure, df_context, run_analysis, strip_fences


def _bdata(values: list[float], dtype: str = "f8") -> dict:
    """Build a Plotly compact typed-array-encoded field, matching what
    `fig.to_json()` actually emits for numeric arrays on this project's
    Plotly version — verified against a real Executive Dashboard chart."""
    arr = np.array(values, dtype=dtype)
    return {"dtype": dtype, "bdata": base64.b64encode(arr.tobytes()).decode()}


class TestStripFences:
    def test_strips_python_fence(self) -> None:
        code = "```python\nprint('hello')\n```"
        assert strip_fences(code) == "print('hello')"

    def test_strips_plain_fence(self) -> None:
        code = "```\nprint('hello')\n```"
        assert strip_fences(code) == "print('hello')"

    def test_no_fence(self) -> None:
        code = "print('hello')"
        assert strip_fences(code) == "print('hello')"


class TestRunAnalysis:
    def test_simple_code(self) -> None:
        df = pd.DataFrame({"x": [1, 2, 3]})
        result = run_analysis("print(df['x'].sum())", df)
        assert result["error"] is None
        assert "6" in result["output"]

    def test_code_error(self) -> None:
        df = pd.DataFrame({"x": [1, 2, 3]})
        result = run_analysis("print(undefined_var)", df)
        assert result["error"] is not None

    def test_fig_capture(self) -> None:
        df = pd.DataFrame({"x": [1, 2, 3], "y": [4, 5, 6]})
        code = "fig = px.line(df, x='x', y='y')"
        result = run_analysis(code, df)
        assert result["error"] is None
        assert result.get("fig") is not None

    def test_dataframe_not_mutated(self) -> None:
        df = pd.DataFrame({"x": [1, 2, 3]})
        result = run_analysis("df['x'] = df['x'] * 0", df)
        assert result["error"] is None
        assert result["df"]["x"].tolist() == [0, 0, 0]

    def test_output_capped(self) -> None:
        df = pd.DataFrame({"x": [1]})
        code = "print('A' * 2_000_000)"
        result = run_analysis(code, df)
        assert result["error"] is None
        assert len(result["output"]) < 2_500_000

    def test_numpy_available(self) -> None:
        df = pd.DataFrame({"x": [1, 2, 3, 4]})
        result = run_analysis("print(np.mean(df['x']))", df)
        assert result["error"] is None
        assert "2.5" in result["output"]


class TestDfContext:
    def test_basic_context(self) -> None:
        df = pd.DataFrame({"x": [1, 2, 3], "y": [4.0, 5.0, 6.0]})
        ctx = df_context(df)
        assert "3 rows" in ctx
        assert "x" in ctx
        assert "y" in ctx

    def test_empty_df(self) -> None:
        df = pd.DataFrame()
        ctx = df_context(df)
        assert "0 rows" in ctx


class TestDescribeFigure:
    def _fig(self, *traces: dict, title: str | None = "Revenue by Month") -> dict:
        return {
            "data": list(traces),
            "layout": {
                "title": {"text": title} if title else None,
                "xaxis": {"title": {"text": "Month"}},
                "yaxis": {"title": {"text": "Revenue"}},
            },
        }

    def test_bar_trace(self) -> None:
        fig = self._fig({
            "type": "bar",
            "name": "Revenue",
            "x": ["Jan", "Feb", "Mar"],
            "y": [100, 200, 150],
        })
        summary = describe_figure(fig)
        assert summary["chart_title"] == "Revenue by Month"
        assert summary["x_axis_title"] == "Month"
        assert summary["y_axis_title"] == "Revenue"
        trace = summary["traces"][0]
        assert trace["type"] == "bar"
        assert trace["min"] == 100 and trace["max"] == 200
        assert trace["category_count"] == 3
        assert "Jan" in trace["categories_sample"]

    def test_scatter_line_trace(self) -> None:
        fig = self._fig({"type": "scatter", "mode": "lines", "x": [1, 2, 3], "y": [10, 20, 30]})
        trace = describe_figure(fig)["traces"][0]
        assert trace["count"] == 3
        assert trace["first"] == 10 and trace["last"] == 30

    def test_pie_trace(self) -> None:
        fig = self._fig({
            "type": "pie",
            "labels": ["Champions", "Loyal", "At Risk"],
            "values": [700, 200, 100],
        })
        trace = describe_figure(fig)["traces"][0]
        assert trace["segment_count"] == 3
        assert trace["total_value"] == 1000
        assert trace["top_segments"][0]["label"] == "Champions"

    def test_histogram_trace(self) -> None:
        fig = self._fig({"type": "histogram", "x": [1, 2, 2, 3, 3, 3, 4]})
        trace = describe_figure(fig)["traces"][0]
        assert trace["count"] == 7
        assert trace["min"] == 1 and trace["max"] == 4

    def test_box_trace(self) -> None:
        fig = self._fig({"type": "box", "name": "Order value", "y": [10, 20, 30, 40, 50]})
        trace = describe_figure(fig)["traces"][0]
        assert trace["median"] == 30
        assert trace["min"] == 10 and trace["max"] == 50

    def test_heatmap_trace(self) -> None:
        fig = self._fig({
            "type": "heatmap",
            "x": ["Mon", "Tue"],
            "y": ["Morning", "Evening"],
            "z": [[1, 2], [3, 4]],
        })
        trace = describe_figure(fig)["traces"][0]
        assert trace["rows"] == 2 and trace["cols"] == 2
        assert trace["value_max"] == 4
        assert trace["top_cells"][0]["value"] == 4

    def test_treemap_trace(self) -> None:
        fig = self._fig({
            "type": "treemap",
            "labels": ["A", "B", "C"],
            "values": [50, 30, 20],
        })
        trace = describe_figure(fig)["traces"][0]
        assert trace["segment_count"] == 3
        assert trace["top_segments"][0]["label"] == "A"

    def test_unknown_trace_type_falls_back_without_crashing(self) -> None:
        fig = self._fig({"type": "candlestick", "close": [10, 12, 11]})
        trace = describe_figure(fig)["traces"][0]
        assert trace["type"] == "candlestick"
        assert "note" not in trace
        assert trace["field"] == "close"

    def test_completely_malformed_trace_never_crashes(self) -> None:
        # x/y are scalars instead of arrays — not iterable, so the xy
        # handler raises internally; the outer dispatch must still return a
        # usable entry instead of propagating the exception.
        fig = self._fig({"type": "bar", "x": 5, "y": 10})
        trace = describe_figure(fig)["traces"][0]
        assert trace["type"] == "bar"
        assert trace["note"] == "could not summarize this trace"

    def test_traces_capped_at_max_traces(self) -> None:
        traces = [{"type": "bar", "x": [str(i)], "y": [i]} for i in range(20)]
        fig = self._fig(*traces)
        summary = describe_figure(fig, max_traces=8)
        assert len(summary["traces"]) == 8
        assert summary["trace_count"] == 20
        assert summary["traces_omitted"] == 12

    def test_large_trace_summary_stays_compact(self) -> None:
        fig = self._fig({
            "type": "scatter",
            "x": list(range(10_000)),
            "y": [float(i % 500) for i in range(10_000)],
        })
        summary = describe_figure(fig)
        assert len(json.dumps(summary)) < 2_000

    def test_missing_layout_and_data_never_crashes(self) -> None:
        summary = describe_figure({})
        assert summary["trace_count"] == 0
        assert summary["traces"] == []

    def test_binary_encoded_numeric_arrays_are_decoded(self) -> None:
        # Real Plotly `to_json()` output serializes numeric arrays as compact
        # {"dtype": "f8", "bdata": <base64>} blobs, not plain JSON lists —
        # confirmed against a live Executive Dashboard chart, where this
        # silently produced an empty summary before this was handled.
        fig = self._fig({
            "type": "scatter",
            "mode": "lines",
            "x": _bdata([1, 2, 3, 4, 5]),
            "y": _bdata([100.0, 200.0, 150.0, 300.0, 250.0]),
        })
        trace = describe_figure(fig)["traces"][0]
        assert trace["count"] == 5
        assert trace["min"] == 100.0 and trace["max"] == 300.0
        assert trace["first"] == 100.0 and trace["last"] == 250.0

    def test_binary_encoded_pie_values_are_decoded(self) -> None:
        fig = self._fig({
            "type": "pie",
            "labels": ["A", "B"],
            "values": _bdata([70.0, 30.0]),
        })
        trace = describe_figure(fig)["traces"][0]
        assert trace["total_value"] == 100.0
        assert trace["top_segments"][0]["label"] == "A"

    def test_binary_encoded_heatmap_z_with_shape_is_decoded(self) -> None:
        fig = self._fig({
            "type": "heatmap",
            "x": ["Mon", "Tue"],
            "y": ["AM", "PM"],
            "z": {**_bdata([1.0, 2.0, 3.0, 4.0]), "shape": "2,2"},
        })
        trace = describe_figure(fig)["traces"][0]
        assert trace["rows"] == 2 and trace["cols"] == 2
        assert trace["value_max"] == 4.0
