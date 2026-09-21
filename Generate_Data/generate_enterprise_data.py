"""Generate a large, realistic, multi-table retail dataset — "Nile Retail Co."

Unlike the other generators here, this one is built for *scale* and for
*verifiable correctness*:

* **Scale.** Defaults to 70,000 customers and ~840,000 order lines over three
  and a half years — roughly 100x the toy datasets, and in the range a real
  mid-market retailer actually produces. The whole analytics path pulls this
  into pandas with no sampling, so it is a genuine load test.

* **Verifiable correctness.** Every interesting pattern in the data is injected
  deliberately and then *measured on the generated result*, and the measured
  figures are written to ``ground_truth.json``. That turns "did the analysis
  run?" into "did the analysis find the answer we already know is true?" —
  the only question worth asking of an analytics product.

The business is Egyptian and omnichannel on purpose: Friday/Saturday weekends,
Ramadan and Eid demand, cash-on-delivery, and governorate-level geography are
the conditions this product is meant to serve, and none of them show up in a
US-shaped dataset.

Tables (orders is line-grain with direct FKs, which is the shape
``db.views.build_fact_view`` joins in one hop):

    customers   dimension
    products    dimension
    stores      dimension
    orders      fact, one row per order line

Usage:
    python Generate_Data/generate_enterprise_data.py                 # full size
    python Generate_Data/generate_enterprise_data.py --scale 0.05    # quick
"""

from __future__ import annotations

import argparse
import json
import os

import numpy as np
import pandas as pd

SEED = 20260818
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(_REPO_ROOT, "data", "nile_retail")

# The data must end yesterday. A dataset that stops months ago makes the
# freshness rule the loudest finding in every briefing and drowns out
# everything else the system found.
START = pd.Timestamp("2023-01-01")
END = pd.Timestamp("2026-08-17")

# ── Injected events, all re-measured against the generated data afterwards ──
OUTAGE_START, OUTAGE_END = pd.Timestamp("2024-08-05"), pd.Timestamp("2024-08-20")
OUTAGE_CATEGORY = "Grinders"
PRICE_RISE_DATE = pd.Timestamp("2025-03-01")
PRICE_RISE_CATEGORY = "Coffee Beans"
PRICE_RISE_PCT = 0.12
CAMPAIGN_START, CAMPAIGN_END = pd.Timestamp("2026-02-05"), pd.Timestamp("2026-02-25")
# The finding the monitoring feature is meant to catch, with a known cause.
DECLINE_DAYS = 21
DECLINE_CATEGORY = "Electronics Accessories"
DECLINE_GOVERNORATE = "all (nationwide)"
DECLINE_DROP_FRACTION = 0.72

RAMADAN = [
    (pd.Timestamp("2023-03-23"), pd.Timestamp("2023-04-20")),
    (pd.Timestamp("2024-03-11"), pd.Timestamp("2024-04-09")),
    (pd.Timestamp("2025-03-01"), pd.Timestamp("2025-03-30")),
    (pd.Timestamp("2026-02-18"), pd.Timestamp("2026-03-19")),
]
EID = [
    (pd.Timestamp("2023-04-21"), pd.Timestamp("2023-04-24")),
    (pd.Timestamp("2024-04-10"), pd.Timestamp("2024-04-13")),
    (pd.Timestamp("2025-03-31"), pd.Timestamp("2025-04-03")),
    (pd.Timestamp("2026-03-20"), pd.Timestamp("2026-03-23")),
]
BLACK_FRIDAY = [
    pd.Timestamp("2023-11-24"), pd.Timestamp("2024-11-29"), pd.Timestamp("2025-11-28"),
]

GOVERNORATES = [
    ("Cairo", 0.34), ("Giza", 0.17), ("Alexandria", 0.13), ("Dakahlia", 0.07),
    ("Sharqia", 0.06), ("Qalyubia", 0.055), ("Gharbia", 0.05), ("Beheira", 0.04),
    ("Port Said", 0.03), ("Suez", 0.025), ("Luxor", 0.02), ("Aswan", 0.015),
    ("Red Sea", 0.02),
]
# category -> (department, share, price_low, price_high, gross_margin)
CATEGORIES = {
    "Coffee Beans":            ("Consumables", 0.30, 120, 620, 0.42),
    "Brewing Equipment":       ("Hardware",    0.14, 450, 5200, 0.38),
    "Grinders":                ("Hardware",    0.09, 700, 8800, 0.36),
    "Electronics Accessories": ("Hardware",    0.18, 90, 2400, 0.31),
    "Home & Kitchen":          ("Home",        0.15, 110, 1900, 0.44),
    "Tea & Infusions":         ("Consumables", 0.09, 70, 480, 0.47),
    "Merchandise":             ("Home",        0.05, 60, 700, 0.55),
}
CHANNELS = ["organic_search", "paid_search", "paid_social", "email", "referral",
            "direct", "marketplace", "influencer"]
CHANNEL_P = [0.19, 0.16, 0.17, 0.10, 0.07, 0.15, 0.11, 0.05]
PAYMENTS = ["cash_on_delivery", "credit_card", "debit_card", "wallet",
            "installments", "bank_transfer"]
PAYMENT_P = [0.41, 0.21, 0.14, 0.13, 0.07, 0.04]
SEGMENTS = ["Champion", "Loyal", "Promising", "Occasional", "At Risk", "Dormant"]
SEGMENT_P = [0.07, 0.13, 0.16, 0.34, 0.18, 0.12]
# Mean orders over a lifetime, by segment — the driver of the RFM structure.
SEGMENT_ORDERS = {"Champion": 34.0, "Loyal": 19.0, "Promising": 9.0,
                  "Occasional": 4.2, "At Risk": 6.0, "Dormant": 2.6}
# How long a segment stays active, in days. This has to be an intrinsic property
# of the customer, not a fraction of the time left in the dataset: tying it to
# the remaining window gives everyone who signed up late an artificially short
# life, so the active population collapses toward the end of the period and the
# most recent weeks show a steep fake decline.
SEGMENT_LIFETIME_DAYS = {"Champion": 1250.0, "Loyal": 1000.0, "Promising": 620.0,
                         "Occasional": 450.0, "At Risk": 300.0, "Dormant": 150.0}

AR_FIRST = ["Ahmed", "Mohamed", "Mahmoud", "Youssef", "Omar", "Khaled", "Mostafa",
            "Hassan", "Karim", "Amr", "Tarek", "Sherif", "Fatma", "Aya", "Nour",
            "Mariam", "Salma", "Hoda", "Yasmin", "Dina", "Rana", "Heba", "Sara",
            "Menna", "Nada", "Reem", "Laila", "Ghada", "Mona", "Amira"]
AR_LAST = ["Hassan", "Ibrahim", "Abdelrahman", "El-Sayed", "Mansour", "Fouad",
           "Zaki", "Shawky", "El-Masry", "Nassar", "Ramadan", "Selim", "Kamal",
           "Farouk", "Abdelaziz", "Gaber", "Sobhy", "Helmy", "Rashad", "Attia"]
BRANDS = ["Nile Roasters", "Delta Home", "Cairo Craft", "Aswan Gear", "Rosetta",
          "Fayrouz", "Karnak", "Sinai Supply", "Horus", "Papyrus Goods"]


def _events(days: pd.DatetimeIndex) -> np.ndarray:
    """Multiplicative demand shocks — the calendar an Egyptian retailer lives by."""
    m = np.ones(len(days))
    for lo, hi in RAMADAN:
        m[(days >= lo) & (days <= hi)] *= 1.34
    for lo, hi in EID:
        m[(days >= lo) & (days <= hi)] *= 1.9
    for bf in BLACK_FRIDAY:
        window = (days >= bf - pd.Timedelta(days=2)) & (days <= bf + pd.Timedelta(days=3))
        m[window] *= 3.1
    # Late-December gifting, then the January slump that always follows it.
    m[(days.month == 12) & (days.day >= 15)] *= 1.5
    m[(days.month == 1) & (days.day <= 20)] *= 0.78
    # Egyptian summer: nobody is buying hot-coffee gear in July and August.
    m[days.month.isin([7, 8])] *= 0.85
    m[(days >= CAMPAIGN_START) & (days <= CAMPAIGN_END)] *= 1.45
    return m


def _daily_demand(days: pd.DatetimeIndex, rng) -> np.ndarray:
    t = np.arange(len(days))
    growth = 1.018 ** (t / 30.44)                      # ~1.8% compounding monthly
    weekday = np.ones(len(days))
    dow = days.dayofweek.values                        # Mon=0 .. Sun=6
    weekday[dow == 4] = 1.28                           # Friday — Egyptian weekend
    weekday[dow == 5] = 1.22                           # Saturday
    weekday[dow == 3] = 1.08
    weekday[dow == 0] = 0.88
    annual = 1 + 0.12 * np.sin(2 * np.pi * (t / 365.25) + 1.1)
    noise = rng.lognormal(0.0, 0.16, len(days))
    return growth * weekday * annual * _events(days) * noise


def _make_customers(n: int, days, rng) -> pd.DataFrame:
    seg = rng.choice(SEGMENTS, size=n, p=SEGMENT_P)
    govs = [g for g, _ in GOVERNORATES]
    gov_p = np.array([p for _, p in GOVERNORATES], dtype=float)
    gov_p /= gov_p.sum()
    # Signups accelerate with the business rather than being uniform — a flat
    # acquisition curve makes every cohort analysis meaningless.
    weights = np.linspace(0.55, 1.7, len(days))
    weights /= weights.sum()
    signup_idx = rng.choice(len(days), size=n, p=weights)
    first = rng.choice(AR_FIRST, size=n)
    last = rng.choice(AR_LAST, size=n)
    return pd.DataFrame({
        "customer_id": [f"CUST-{i:06d}" for i in range(1, n + 1)],
        "full_name": [f"{a} {b}" for a, b in zip(first, last)],
        "email": [f"{a.lower()}.{b.lower().replace('-', '')}{i}@example.com"
                  for i, (a, b) in enumerate(zip(first, last), 1)],
        "phone": ["+20" + str(x) for x in rng.integers(1000000000, 1999999999, n)],
        "governorate": rng.choice(govs, size=n, p=gov_p),
        "country": "Egypt",
        "signup_date": days[signup_idx],
        "customer_segment": seg,
        "acquisition_channel": rng.choice(CHANNELS, size=n, p=CHANNEL_P),
        "birth_year": rng.integers(1965, 2007, size=n),
        "gender": rng.choice(["M", "F"], size=n, p=[0.54, 0.46]),
        "loyalty_tier": rng.choice(["bronze", "silver", "gold", "platinum"],
                                   size=n, p=[0.52, 0.30, 0.14, 0.04]),
        "_signup_idx": signup_idx,
    })


def _make_products(n: int, rng) -> pd.DataFrame:
    cats = list(CATEGORIES)
    cat_p = np.array([CATEGORIES[c][1] for c in cats], dtype=float)
    cat_p /= cat_p.sum()
    cat = rng.choice(cats, size=n, p=cat_p)
    lo = np.array([CATEGORIES[c][2] for c in cat], dtype=float)
    hi = np.array([CATEGORIES[c][3] for c in cat], dtype=float)
    margin = np.array([CATEGORIES[c][4] for c in cat], dtype=float)
    price = np.round(lo * (hi / lo) ** rng.random(n), 2)
    return pd.DataFrame({
        "product_id": [f"SKU-{i:05d}" for i in range(1, n + 1)],
        "product_name": [f"{b} {c.split()[0]} {x}" for b, c, x in
                         zip(rng.choice(BRANDS, size=n), cat,
                             rng.integers(100, 999, n))],
        "category": cat,
        "department": [CATEGORIES[c][0] for c in cat],
        "brand": rng.choice(BRANDS, size=n),
        "list_price": price,
        "unit_cost": np.round(price * (1 - margin) * rng.uniform(0.92, 1.08, n), 2),
        "is_active": rng.random(n) > 0.06,
        # A real catalogue is Pareto-ish: the top fifth of SKUs carries most of
        # the business, but no single SKU is half of it — a heavy-tailed Pareto
        # draw produces exactly that unrealistic single-SKU monopoly.
        "_popularity": rng.lognormal(0.0, 1.25, n),
    })


def _make_stores(rng) -> pd.DataFrame:
    rows = [("STORE-01", "Online Store", "online", "Cairo"),
            ("STORE-02", "Marketplace Channel", "marketplace", "Cairo")]
    cities = ["Cairo", "Giza", "Alexandria", "Mansoura", "Tanta", "Port Said",
              "Luxor", "Aswan", "Hurghada", "Zagazig", "Suez", "Ismailia"]
    for i, city in enumerate(cities, start=3):
        rows.append((f"STORE-{i:02d}", f"{city} Branch", "retail", city))
    df = pd.DataFrame(rows, columns=["store_id", "store_name", "store_type", "store_city"])
    df["opened_date"] = [START - pd.Timedelta(days=int(x))
                         for x in rng.integers(200, 2500, len(df))]
    df["floor_area_sqm"] = np.where(df.store_type == "retail",
                                    rng.integers(80, 400, len(df)), 0)
    return df


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", type=float, default=1.0,
                    help="Fraction of full size. 1.0 = 70k customers / ~840k lines.")
    args = ap.parse_args()

    rng = np.random.default_rng(SEED)
    n_customers = max(200, int(70_000 * args.scale))
    n_products = max(40, int(900 * args.scale ** 0.5))

    days = pd.date_range(START, END, freq="D")
    demand = _daily_demand(days, rng)

    customers = _make_customers(n_customers, days, rng)
    products = _make_products(n_products, rng)
    stores = _make_stores(rng)

    # ── How many orders each customer places, and when ──────────────────────
    seg_mean = customers.customer_segment.map(SEGMENT_ORDERS).to_numpy()
    seg_life = customers.customer_segment.map(SEGMENT_LIFETIME_DAYS).to_numpy()

    start_idx = customers._signup_idx.to_numpy()
    lifetime = seg_life * rng.uniform(0.7, 1.3, n_customers)
    # Truncated by the end of the dataset, not defined by it — a customer who
    # signed up last month is simply still active, not short-lived.
    end_idx = np.minimum(len(days) - 1, start_idx + lifetime).astype(int)

    # Orders are a *rate*, not a lifetime total. Giving every customer the same
    # expected order count regardless of how long they have been observed piles
    # a full lifetime of orders into the short window of everyone who signed up
    # recently — which, because signups accelerate, manufactures a large fake
    # surge in exactly the most recent weeks the monitoring tests care about.
    observed_days = (end_idx - start_idx + 1).astype(float)
    n_orders = rng.poisson(seg_mean * observed_days / seg_life)

    cust_of_order = np.repeat(np.arange(n_customers), n_orders)
    m_orders = cust_of_order.size
    lo_i, hi_i = start_idx[cust_of_order], end_idx[cust_of_order]

    # Inverse-CDF sampling against the global demand curve, truncated to each
    # customer's own active window: seasonality is preserved, but nobody buys
    # before they signed up or after they went quiet.
    cdf = np.concatenate([[0.0], np.cumsum(demand)])
    u = rng.uniform(cdf[lo_i], cdf[hi_i + 1])
    order_day = np.clip(np.searchsorted(cdf, u) - 1, 0, len(days) - 1)

    # ── Expand orders into lines ────────────────────────────────────────────
    lines_per_order = np.clip(1 + rng.poisson(1.15, m_orders), 1, 9)
    order_of_line = np.repeat(np.arange(m_orders), lines_per_order)
    n_lines = order_of_line.size

    pop = products._popularity.to_numpy()
    prod_of_line = rng.choice(n_products, size=n_lines, p=pop / pop.sum())

    line_day = order_day[order_of_line]
    line_cust = cust_of_order[order_of_line]
    line_date = days[line_day]
    cat_of_line = products.category.to_numpy()[prod_of_line]
    gov_of_line = customers.governorate.to_numpy()[line_cust]

    keep = np.ones(n_lines, dtype=bool)
    # Supply outage: the category is simply unavailable for two weeks.
    keep &= ~((cat_of_line == OUTAGE_CATEGORY)
              & (line_date >= OUTAGE_START) & (line_date <= OUTAGE_END))
    # The recent, concentrated decline the monitor is supposed to find and explain.
    decline_from = END - pd.Timedelta(days=DECLINE_DAYS - 1)
    in_decline = ((cat_of_line == DECLINE_CATEGORY)
                  & (line_date >= decline_from))
    keep &= ~(in_decline & (rng.random(n_lines) < DECLINE_DROP_FRACTION))

    order_of_line, prod_of_line = order_of_line[keep], prod_of_line[keep]
    line_cust, line_day = line_cust[keep], line_day[keep]
    line_date = days[line_day]
    n_lines = order_of_line.size

    # ── Money ───────────────────────────────────────────────────────────────
    list_price = products.list_price.to_numpy()[prod_of_line]
    unit_cost = products.unit_cost.to_numpy()[prod_of_line]
    cat_of_line = products.category.to_numpy()[prod_of_line]

    price_mult = np.ones(n_lines)
    price_mult[(cat_of_line == PRICE_RISE_CATEGORY)
               & (line_date >= PRICE_RISE_DATE)] += PRICE_RISE_PCT
    unit_price = np.round(list_price * price_mult * rng.uniform(0.97, 1.03, n_lines), 2)

    quantity = np.clip(1 + rng.poisson(0.8, n_lines), 1, 12)
    discount = np.zeros(n_lines)
    promo = rng.random(n_lines) < 0.28
    discount[promo] = rng.choice([0.05, 0.10, 0.15, 0.20, 0.25], size=int(promo.sum()),
                                 p=[0.30, 0.28, 0.20, 0.14, 0.08])
    for bf in BLACK_FRIDAY:                      # deeper cuts on the spike days
        w = (line_date >= bf - pd.Timedelta(days=2)) & (line_date <= bf + pd.Timedelta(days=3))
        discount[w] = np.maximum(discount[w], 0.30)

    line_total = np.round(unit_price * quantity * (1 - discount), 2)
    line_cost = np.round(unit_cost * quantity, 2)

    store_p = np.array([0.44, 0.16] + [0.40 / (len(stores) - 2)] * (len(stores) - 2))
    store_p /= store_p.sum()

    orders = pd.DataFrame({
        "line_id": [f"L-{i:08d}" for i in range(1, n_lines + 1)],
        "order_id": [f"ORD-{i:07d}" for i in order_of_line],
        "order_date": line_date,
        "customer_id": customers.customer_id.to_numpy()[line_cust],
        "product_id": products.product_id.to_numpy()[prod_of_line],
        "store_id": stores.store_id.to_numpy()[rng.choice(len(stores), n_lines, p=store_p)],
        "quantity": quantity,
        "unit_price": unit_price,
        "discount_pct": np.round(discount, 2),
        "line_total": line_total,
        "unit_cost": unit_cost,
        "line_cost": line_cost,
        "line_profit": np.round(line_total - line_cost, 2),
        "marketing_channel": rng.choice(CHANNELS, size=n_lines, p=CHANNEL_P),
        "payment_method": rng.choice(PAYMENTS, size=n_lines, p=PAYMENT_P),
        "order_status": rng.choice(["completed", "returned", "cancelled"],
                                   size=n_lines, p=[0.945, 0.033, 0.022]),
        "delivery_days": np.clip(rng.poisson(3.1, n_lines), 0, 21),
    }).sort_values("order_date").reset_index(drop=True)

    customers = customers.drop(columns=["_signup_idx"])
    products = products.drop(columns=["_popularity"])

    os.makedirs(OUT_DIR, exist_ok=True)
    customers.to_csv(os.path.join(OUT_DIR, "customers.csv"), index=False)
    products.to_csv(os.path.join(OUT_DIR, "products.csv"), index=False)
    stores.to_csv(os.path.join(OUT_DIR, "stores.csv"), index=False)
    orders.to_csv(os.path.join(OUT_DIR, "orders.csv"), index=False)

    # ── Measure what was actually produced ──────────────────────────────────
    # The injected constants above are intent; these figures are fact, and they
    # are what any claim about this dataset has to be checked against.
    done = orders[orders.order_status == "completed"].copy()
    done = done.merge(products[["product_id", "category"]], on="product_id", how="left")
    done = done.merge(customers[["customer_id", "governorate"]], on="customer_id", how="left")

    recent = done[done.order_date >= decline_from]
    prior = done[(done.order_date >= decline_from - pd.Timedelta(days=DECLINE_DAYS))
                 & (done.order_date < decline_from)]
    is_slice_now = (recent.category == DECLINE_CATEGORY)
    is_slice_pre = (prior.category == DECLINE_CATEGORY)
    slice_now = float(recent[is_slice_now].line_total.sum())
    slice_before = float(prior[is_slice_pre].line_total.sum())
    rev_now, rev_before = float(recent.line_total.sum()), float(prior.line_total.sum())

    by_sku = done.groupby("product_id").line_total.sum().sort_values(ascending=False)
    top20 = by_sku.head(max(1, int(0.2 * len(by_sku)))).sum() / by_sku.sum()

    truth = {
        "rows": {"orders": int(len(orders)), "customers": int(len(customers)),
                 "products": int(len(products)), "stores": int(len(stores))},
        "date_range": [str(orders.order_date.min().date()),
                       str(orders.order_date.max().date())],
        "distinct_orders": int(orders.order_id.nunique()),
        "total_revenue_completed": round(float(done.line_total.sum()), 2),
        "total_profit_completed": round(float(done.line_profit.sum()), 2),
        "recent_decline": {
            "window_days": DECLINE_DAYS,
            "window_from": str(decline_from.date()),
            "revenue_recent": round(rev_now, 2),
            "revenue_prior": round(rev_before, 2),
            "change_pct": round((rev_now - rev_before) / rev_before * 100, 2),
            "cause_category": DECLINE_CATEGORY,
            "cause_governorate": DECLINE_GOVERNORATE,
            "slice_revenue_recent": round(slice_now, 2),
            "slice_revenue_prior": round(slice_before, 2),
            "slice_share_of_total_change_pct": (
                round((slice_now - slice_before) / (rev_now - rev_before) * 100, 2)
                if rev_now != rev_before else None),
        },
        "supply_outage": {
            "category": OUTAGE_CATEGORY,
            "from": str(OUTAGE_START.date()), "to": str(OUTAGE_END.date()),
            "lines_in_window": int(((done.category == OUTAGE_CATEGORY)
                                    & (done.order_date >= OUTAGE_START)
                                    & (done.order_date <= OUTAGE_END)).sum()),
        },
        "price_rise": {"category": PRICE_RISE_CATEGORY,
                       "date": str(PRICE_RISE_DATE.date()), "pct": PRICE_RISE_PCT},
        "pareto_top20pct_sku_revenue_share": round(float(top20) * 100, 2),
        "status_mix": {str(k): int(v) for k, v in orders.order_status.value_counts().items()},
    }
    with open(os.path.join(OUT_DIR, "ground_truth.json"), "w", encoding="utf-8") as fh:
        json.dump(truth, fh, indent=2, ensure_ascii=False)

    print(json.dumps(truth, indent=2, ensure_ascii=False))
    print(f"\nWritten to {OUT_DIR}")


if __name__ == "__main__":
    main()
