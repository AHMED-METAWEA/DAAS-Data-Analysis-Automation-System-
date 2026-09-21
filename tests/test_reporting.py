"""
Tests for the shared reporting layer: grounding / faithfulness checks,
markdown → HTML export, and the deterministic forecast-report builder.
"""

from __future__ import annotations

from agents.forecasting.report import build_forecast_report_md
from agents.reporting.export import build_html_report, markdown_to_html
from agents.reporting.grounding import check_grounding, extract_claims

_PAYLOAD = {
    "kpi": {
        "revenue": 128450.75,
        "orders": 3120,
        "aov": 41.17,
        "total_customers": 1875,
        "monthly_growth_avg": 0.083,  # stored as a fraction -> 8.3%
        "top_products": {"Latte": 42000.5, "Espresso": 33110.0},
    },
    "rfm": {"segments": {"Champions": {"revenue_pct": 34.2, "count": 210}}},
}


# ── Grounding ───────────────────────────────────────────────────────────────

def test_grounding_verifies_real_figures():
    report = (
        "Revenue reached $128,450 across 3,120 orders with an AOV of $41.17. "
        "Champions drive 34.2% of revenue."
    )
    g = check_grounding(report, _PAYLOAD)
    assert g.total == 4
    assert g.verified_count == 4
    assert g.status == "clean"
    assert g.coverage == 1.0


def test_grounding_flags_invented_figures():
    report = "Our churn rate is 27% versus an industry benchmark of 15%."
    g = check_grounding(report, _PAYLOAD)
    assert g.total == 2
    assert g.verified_count == 0
    assert len(g.unverified) == 2
    assert g.status == "weak"


def test_grounding_percent_stored_as_fraction():
    g = check_grounding("Monthly growth is around 8.3%.", _PAYLOAD)
    assert g.verified_count == 1


def test_grounding_currency_magnitude_suffix():
    g = check_grounding("Top line is about $1.2M this year.", {"revenue": 1_200_000})
    assert g.total == 1
    assert g.verified_count == 1


def test_grounding_ignores_years_and_small_counts():
    # "10" and "2024" are structural, not data claims -> not counted.
    g = check_grounding("The top 10 products in 2024 performed well.", {})
    assert g.total == 0
    assert g.coverage == 1.0


def test_grounding_empty_report_is_neutral():
    g = check_grounding("", _PAYLOAD)
    assert g.total == 0
    assert g.status == "none"
    assert g.coverage == 1.0


def test_grounding_emits_per_figure_trace():
    # Every verified figure must cite the exact source value it matched.
    report = "Revenue reached $128,450 across 3,120 orders."
    g = check_grounding(report, _PAYLOAD)
    traces = g.traces
    assert len(traces) == g.verified_count == 2
    by_val = {t["value"]: t["matched_source_value"] for t in traces}
    assert by_val[128450.0] == 128450.75   # traced to the real revenue figure
    assert by_val[3120.0] == 3120          # traced to the real order count
    # Unverified claims never appear in the trace.
    assert all(t["matched_source_value"] is not None for t in traces)


def test_grounding_rejects_one_sig_fig_distortion():
    # $100,000 quoted for a true revenue of $128,450 is a 22% distortion. The
    # tightened matcher must flag it instead of accepting it as "rounding".
    g = check_grounding("Revenue was about $100,000.", {"revenue": 128450.75})
    assert g.total == 1
    assert g.verified_count == 0
    assert g.status == "weak"


def test_grounding_accepts_honest_two_sig_fig_rounding():
    # $128,000 (or $130,000) for 128,450.75 is honest rounding and must pass.
    for quoted in ("$128,000", "$130,000"):
        g = check_grounding(f"Revenue was {quoted}.", {"revenue": 128450.75})
        assert g.verified_count == 1, quoted


def test_grounding_label_binding_catches_misattribution():
    # $1,240 is the wrong number for AOV (real AOV is 41.17) even though 1240
    # is a plausible magnitude. Binding "average order value" -> 41.17 must flag
    # it, instead of letting it pass on some unrelated leaf.
    labeled = {"average order value": 41.17, "total revenue": 128450.75}
    g = check_grounding(
        "Average order value is $1,240 this quarter.",
        _PAYLOAD, labeled=labeled,
    )
    aov_claim = next(c for c in g.claims if c.value == 1240.0)
    assert aov_claim.verified is False


def test_grounding_label_binding_accepts_correct_metric():
    labeled = {"average order value": 41.17}
    g = check_grounding("The average order value is $41.17.", _PAYLOAD, labeled=labeled)
    assert g.status == "clean"
    assert g.verified_count == g.total == 1


def test_grounding_label_binding_tolerates_rounding():
    # "aov" is a bound label here, so this exercises rounding UNDER binding.
    labeled = {"average order value": 41.17, "aov": 41.17}
    g = check_grounding("AOV came in around $41.", _PAYLOAD, labeled=labeled)
    assert g.verified_count == 1


def test_grounding_label_binding_does_not_loosen_unbound_figures():
    # A figure NOT next to a bound label still uses the any-leaf check, so an
    # invented number remains flagged.
    labeled = {"average order value": 41.17}
    g = check_grounding("Our churn rate is 27%.", _PAYLOAD, labeled=labeled)
    assert g.verified_count == 0


def test_build_label_map_binds_headline_metrics():
    from agents.reporting.verify import build_reference_labels
    payload = {"kpi": {"revenue": 128450.75, "aov": 41.17, "orders": 3120,
                       "total_customers": 1875}}
    m = build_reference_labels(payload)
    assert m["total revenue"] == 128450.75
    assert m["average order value"] == 41.17
    assert m["total orders"] == 3120.0
    assert m["total customers"] == 1875.0


def test_extract_claims_dedupes():
    claims = extract_claims("$100,000 ... again $100,000 and 50%")
    kinds = sorted(c.kind for c in claims)
    assert kinds == ["currency", "percent"]  # duplicate currency collapsed


# ── Markdown → HTML ─────────────────────────────────────────────────────────

def test_markdown_to_html_blocks():
    md = (
        "# Title\n\nSome **bold** and *italic* and `code`.\n\n"
        "- a\n- b\n\n| X | Y |\n| --- | --- |\n| 1 | 2 |\n\n---\nEnd."
    )
    html = markdown_to_html(md)
    assert "<h1>Title</h1>" in html
    assert "<strong>bold</strong>" in html
    assert "<em>italic</em>" in html
    assert "<code>code</code>" in html
    assert "<ul>" in html and "<li>a</li>" in html
    assert "<table>" in html and "<th>X</th>" in html and "<td>1</td>" in html
    assert "<hr>" in html


def test_markdown_escapes_raw_html():
    html = markdown_to_html("Danger <script>alert(1)</script> here")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_build_html_report_is_self_contained():
    g = check_grounding("Revenue was $128,450.", _PAYLOAD)
    doc = build_html_report(
        "Business Insights",
        "## Summary\nRevenue was **$128,450**.",
        subtitle="Test",
        meta={"Agent": "Insights", "Rows": "3,120"},
        kpis=[("Revenue", "$128,451")],
        grounding=g,
    )
    assert doc.startswith("<!DOCTYPE html>")
    assert "<style>" in doc  # CSS embedded (no external assets)
    assert "Business Insights" in doc
    assert "DAAS" in doc  # brand + footer
    assert "trace back to your data" in doc  # grounding badge text


# ── Forecast report builder ─────────────────────────────────────────────────

def test_forecast_report_md_assembles_sections():
    results = {
        "forecast_outputs": [
            {
                "metric": "total_revenue",
                "current_value": 100000,
                "forecasted_value": 112000,
                "change_percent": 12.0,
                "forecast_horizon": "30_days",
                "selected_model": "Prophet",
                "model_selection_reason": "lowest out-of-sample MAPE",
                "evaluation": {"mae": 5.0, "rmse": 7.0, "mape": 6.0},
                "skill_score": 0.35,
                "confidence_score": 0.82,
                "business_impact": "High",
                "horizons": {"7d": 26000, "30d": 112000},
                "business_summary": "Revenue is set to grow.",
                "risks": ["Supply constraints"],
                "opportunities": ["Scale ad spend"],
                "recommended_actions": ["Increase inventory"],
            }
        ]
    }
    md = build_forecast_report_md(results)
    assert "# Sales & Demand Forecast" in md
    assert "## Total Revenue" in md
    assert "Prophet" in md
    assert "+12.0%" in md
    assert "Supply constraints" in md
    # The assembled report should be groundable against the same numbers.
    g = check_grounding(md, results["forecast_outputs"])
    assert g.verified_count == g.total and g.total > 0


def test_forecast_report_md_empty():
    assert build_forecast_report_md({"forecast_outputs": []}) == ""
