"""Marketing's `/run` endpoint now runs the same post-hoc grounding check
Insights already used (see backend/app/api/v1/marketing.py) — this verifies
the mechanism actually catches a misstated factual figure while not
penalising the model's legitimate forward-looking recommendations, using a
real computed marketing payload rather than a hand-built stub."""

from __future__ import annotations

import numpy as np
import pandas as pd

from agents.marketing.engine import run_marketing_analytics
from agents.reporting.grounding import check_grounding


def _sample_df(n_customers: int = 40, n_orders: int = 300) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    customers = [f"C{idx:03d}" for idx in range(n_customers)]
    dates = pd.date_range("2024-01-01", "2024-12-31", freq="D")
    return pd.DataFrame({
        "order_id": [f"ORD-{idx}" for idx in range(n_orders)],
        "customer_id": rng.choice(customers, n_orders),
        "order_date": rng.choice(dates, n_orders),
        "total_price": rng.uniform(10, 500, n_orders).round(2),
        "category": rng.choice(["Electronics", "Office", "Furniture"], n_orders),
    })


def test_report_restating_a_real_kpi_is_verified() -> None:
    result = run_marketing_analytics(data_df=_sample_df())
    total_revenue = round(result["rfm"]["total_revenue"], 2)

    # Currency claims are checked regardless of magnitude (unlike bare plain
    # numbers, which are only material above $1,000 — see grounding.py).
    report = f"Total revenue across this customer base is ${total_revenue}."
    grounding = check_grounding(report, result)
    assert grounding.total == 1
    assert grounding.verified_count == 1
    assert grounding.status == "clean"


def test_report_misstating_a_factual_figure_is_caught() -> None:
    result = run_marketing_analytics(data_df=_sample_df())
    real_total = round(result["rfm"]["total_revenue"], 2)
    fabricated = round(real_total * 3 + 999999, 2)  # nowhere near the real figure

    report = f"Total revenue across this customer base is ${fabricated}."
    grounding = check_grounding(report, result)
    assert grounding.total == 1
    assert grounding.verified_count == 0
    assert grounding.status != "clean"


def test_forward_looking_recommendation_numbers_are_not_penalised_as_lies() -> None:
    """Budget-allocation / target-lift numbers are the model's own
    recommendation, not a restated fact — check_grounding correctly reports
    them as unverified (no ground truth exists for them), which is honest,
    not a false "fabrication" alarm; the marketing endpoint surfaces this as
    a coverage score rather than a pass/fail gate for exactly this reason."""
    result = run_marketing_analytics(data_df=_sample_df())
    report = "Allocate 8500 of the budget to the VIP win-back campaign this quarter."
    grounding = check_grounding(report, result)
    assert grounding.total == 1
    assert grounding.verified_count == 0  # correctly unverified, not a bug
