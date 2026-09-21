"""
Generate a realistic MULTI-TABLE dataset for "Bloom & Bean" — the same
fictional online coffee roastery & equipment store as
generate_business_data.py, but normalized into three related tables the way
a real company's data actually looks, instead of one flat export. Built to
exercise the full multi-source ingestion pipeline end to end:

  • Multi-file upload    → 3 CSVs, each its own table (customers, products, orders)
  • Schema Discovery     → orders.customer_id -> customers.customer_id and
                           orders.product_id -> products.product_id should both
                           be detected (high confidence: exact name match,
                           high uniqueness on the dimension side, high coverage).
  • Relationship review  → both should land in the auto-approved bucket; expand
                           and look at the evidence (name similarity, PK
                           uniqueness, FK coverage) either way.
  • Cleaning             → each table has its own realistic dirtiness (missing
                           values, placeholders, type mismatches, outliers,
                           duplicates, malformed dates, inconsistent casing).
  • Reconciliation       → orders.customer_id / products.product_id have a
                           small amount of whitespace/casing drift versus the
                           dimension tables' key columns, on purpose — the
                           kind of thing independent per-table cleaning can
                           accidentally make worse, which is exactly what the
                           reconciliation pass is meant to catch and fix.
  • Storage/views        → orders is the natural fact table (it references
                           both dimensions), so the semantic view joins in
                           both customers and products in one hop.
  • Forecasting/Marketing/Churn → same growth trend, weekly + Q4 seasonality,
                           Black-Friday spike, supply-outage dip, and RFM
                           archetypes as the single-table version, so there's
                           real signal once the tables are joined back together.

Outputs (in data/multi_table_demo/):
  customers.csv, products.csv, orders.csv       — dirty (UPLOAD THESE, all 3, to Data Cleaning)
  clean/customers.csv, clean/products.csv, clean/orders.csv  — clean reference

Usage:
    python generate_multi_table_business_data.py
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd

SEED = 11
rng = np.random.default_rng(SEED)

# The repo's shared sample-data folder is top-level data/, a sibling of
# Generate_Data/ — not Generate_Data/data/.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(_REPO_ROOT, "data", "multi_table_demo")
CLEAN_DIR = os.path.join(DATA_DIR, "clean")

START = pd.Timestamp("2023-06-01")
END = pd.Timestamp("2025-05-31")
N_CUSTOMERS = 350

# ── Catalogue ────────────────────────────────────────────────────────────────
# (product_name, category, base_price, unit_cost)
PRODUCTS = [
    ("Ethiopia Yirgacheffe 250g", "Coffee Beans", 14.50, 8.20),
    ("Colombia Supremo 250g", "Coffee Beans", 13.00, 7.40),
    ("Brazil Santos 250g", "Coffee Beans", 11.50, 6.30),
    ("House Blend 1kg", "Coffee Beans", 32.00, 17.50),
    ("Decaf Swiss Water 250g", "Coffee Beans", 15.00, 9.10),
    ("Cold Brew Bottle 1L", "Coffee Beans", 9.90, 4.80),
    ("Espresso Machine Duo", "Equipment", 449.00, 280.00),
    ("Burr Grinder Pro", "Equipment", 129.00, 74.00),
    ("French Press 1L", "Equipment", 34.00, 15.00),
    ("Pour-Over Kit", "Equipment", 42.00, 19.50),
    ("Milk Frother", "Equipment", 28.00, 12.00),
    ("Reusable Travel Cup", "Accessories", 18.00, 6.50),
    ("Paper Filters (100)", "Accessories", 7.50, 2.40),
    ("Ceramic Mug", "Accessories", 12.00, 4.10),
    ("Cleaning Tablets", "Accessories", 9.00, 3.20),
    ("Vanilla Syrup 500ml", "Accessories", 8.50, 3.00),
    ("Coffee Gift Box", "Gifts", 39.00, 21.00),
    ("Tasting Sampler Set", "Gifts", 27.00, 13.50),
    ("Roaster's Choice Subscription", "Subscription", 24.00, 11.00),
    ("Office Bulk Subscription", "Subscription", 79.00, 44.00),
]

REGIONS = ["North", "South", "East", "West", "Central"]
COUNTRIES = ["Egypt", "UAE", "Saudi Arabia", "USA", "UK", "Germany"]
CHANNELS = ["Organic Search", "Paid Search", "Social", "Email", "Referral", "Direct"]
PAYMENTS = ["Credit Card", "PayPal", "Apple Pay", "Bank Transfer", "Cash on Delivery"]
DISCOUNTS = [0, 0, 0, 0, 0, 5, 10, 10, 15, 20]

FIRST = ["Ahmed", "Sara", "Mohamed", "Fatima", "Omar", "Layla", "Youssef", "Nour",
         "Ali", "Hana", "Khaled", "Dina", "Tarek", "Reem", "Karim", "Yasmin",
         "John", "Emma", "David", "Sophie", "James", "Olivia", "Liam", "Mia",
         "Noah", "Ava", "Lucas", "Zara", "Adam", "Lina"]
LAST = ["Ibrahim", "Hassan", "Ali", "Mohamed", "Salem", "Nasser", "Farouk",
        "Mansour", "Khalil", "Smith", "Johnson", "Williams", "Brown", "Garcia",
        "Miller", "Davis", "Wilson", "Taylor", "Schmidt", "Rossi"]

# ── RFM archetypes ───────────────────────────────────────────────────────────
ARCHETYPES = {
    "Champion":    dict(p=0.08, orders=(18, 45), qty_bonus=2, sub=0.60, lo=0.00, hi=1.00),
    "Loyal":       dict(p=0.15, orders=(8, 18),  qty_bonus=1, sub=0.40, lo=0.05, hi=1.00),
    "Potential":   dict(p=0.12, orders=(3, 7),   qty_bonus=1, sub=0.15, lo=0.50, hi=1.00),
    "New":         dict(p=0.15, orders=(1, 3),   qty_bonus=0, sub=0.05, lo=0.82, hi=1.00),
    "AtRisk":      dict(p=0.12, orders=(6, 14),  qty_bonus=1, sub=0.30, lo=0.15, hi=0.70),
    "Hibernating": dict(p=0.13, orders=(2, 6),   qty_bonus=0, sub=0.10, lo=0.00, hi=0.50),
    "Lost":        dict(p=0.10, orders=(1, 3),   qty_bonus=0, sub=0.05, lo=0.00, hi=0.30),
    "OneTime":     dict(p=0.15, orders=(1, 1),   qty_bonus=0, sub=0.02, lo=0.00, hi=1.00),
}


def _daily_demand(days: pd.DatetimeIndex) -> np.ndarray:
    """Sampling weight per day: growth trend × weekly × holiday seasonality ×
    noise, with a Black-Friday spike and a one-week supply-outage dip/year."""
    n = len(days)
    t = np.arange(n)

    trend = 1.0 + 0.0009 * t
    dow = days.dayofweek.to_numpy()
    weekly = np.ones(n)
    weekly[np.isin(dow, [4, 5])] = 1.30
    weekly[dow == 6] = 1.12
    weekly[dow == 0] = 0.92

    month = days.month.to_numpy()
    holiday = np.where(np.isin(month, [11, 12]), 1.45, 1.0)
    holiday = np.where(np.isin(month, [7, 8]), 0.85, holiday)

    noise = rng.normal(1.0, 0.08, n).clip(0.4, None)
    weight = trend * weekly * holiday * noise

    for yr in days.year.unique():
        novs = days[(days.year == yr) & (days.month == 11) & (days.dayofweek == 4)]
        if len(novs) >= 4:
            bf = novs[3]
            weight[days.get_loc(bf)] *= 4.0
            cm_loc = days.get_loc(bf) + 3
            if cm_loc < n:
                weight[cm_loc] *= 2.2

    for yr in days.year.unique():
        outage = np.asarray((days.year == yr) & (days.month == 3)
                            & (days.day >= 10) & (days.day <= 16))
        weight[outage] *= 0.15

    return weight.clip(min=0.05)


def _sample_day_indices(n: int, lo_frac: float, hi_frac: float,
                        prob: np.ndarray, n_days: int) -> np.ndarray:
    lo = int(lo_frac * (n_days - 1))
    hi = int(hi_frac * (n_days - 1))
    idx = np.arange(lo, hi + 1)
    p = prob[lo:hi + 1]
    p = p / p.sum()
    return rng.choice(idx, size=n, replace=True, p=p)


def _make_customers() -> pd.DataFrame:
    names = list(ARCHETYPES.keys())
    probs = [ARCHETYPES[n]["p"] for n in names]
    assigned = rng.choice(names, size=N_CUSTOMERS, p=probs)
    rows = []
    for i, arch in enumerate(assigned, start=1):
        first, last = rng.choice(FIRST), rng.choice(LAST)
        full = f"{first} {last}"
        email = f"{full.lower().replace(' ', '.')}{rng.integers(1, 999)}@" \
                f"{rng.choice(['gmail.com', 'outlook.com', 'yahoo.com', 'bloombean.co'])}"
        rows.append({
            "customer_id": f"CUST-{3000 + i}",
            "customer_name": full,
            "email": email,
            "region": rng.choice(REGIONS),
            "country": rng.choice(COUNTRIES),
            "archetype": arch,  # dropped before writing — internal use only
        })
    return pd.DataFrame(rows)


def _make_products() -> pd.DataFrame:
    rows = []
    for i, (name, category, base_price, unit_cost) in enumerate(PRODUCTS, start=1):
        rows.append({
            "product_id": f"SKU-{1000 + i}",
            "product_name": name,
            "category": category,
            "unit_cost": unit_cost,
            "base_price": base_price,
        })
    return pd.DataFrame(rows)


def _generate_orders(customers: pd.DataFrame, products: pd.DataFrame) -> pd.DataFrame:
    days = pd.date_range(START, END, freq="D")
    n_days = len(days)
    prob = _daily_demand(days)

    products_by_category = {
        cat: products[products["category"] == cat].to_dict("records")
        for cat in products["category"].unique()
    }
    sub_products = products_by_category.get("Subscription", [])
    non_sub_products = [
        p for cat, plist in products_by_category.items() if cat != "Subscription"
        for p in plist
    ]

    rows = []
    order_seq = 200000
    first_order_by_customer: dict[str, pd.Timestamp] = {}

    for _, cust in customers.iterrows():
        spec = ARCHETYPES[cust["archetype"]]
        n_orders = int(rng.integers(spec["orders"][0], spec["orders"][1] + 1))
        day_idx = _sample_day_indices(n_orders, spec["lo"], spec["hi"], prob, n_days)

        for di in sorted(day_idx):
            order_seq += 1
            order_id = f"ORD-{order_seq}"
            order_date = days[di]
            first_order_by_customer[cust["customer_id"]] = min(
                first_order_by_customer.get(cust["customer_id"], order_date), order_date
            )
            ship_date = order_date + pd.Timedelta(days=int(rng.integers(1, 8)))
            channel = rng.choice(CHANNELS)
            payment = rng.choice(PAYMENTS)
            n_lines = int(rng.choice([1, 2, 3], p=[0.5, 0.3, 0.2]))

            for _ in range(n_lines):
                if rng.random() < spec["sub"] and sub_products:
                    product = sub_products[rng.integers(0, len(sub_products))]
                else:
                    product = non_sub_products[rng.integers(0, len(non_sub_products))]

                qty = int(rng.integers(1, 4) + spec["qty_bonus"])
                if product["category"] == "Equipment":
                    qty = 1
                unit_price = round(product["base_price"] * rng.uniform(0.98, 1.06), 2)
                discount = int(rng.choice(DISCOUNTS))
                total_price = round(qty * unit_price * (1 - discount / 100), 2)
                cost = round(qty * product["unit_cost"], 2)
                profit = round(total_price - cost, 2)

                rows.append({
                    "order_id": order_id,
                    "order_date": order_date.strftime("%Y-%m-%d"),
                    "ship_date": ship_date.strftime("%Y-%m-%d"),
                    "customer_id": cust["customer_id"],
                    "product_id": product["product_id"],
                    "quantity": qty,
                    "unit_price": unit_price,
                    "discount_pct": discount,
                    "total_price": total_price,
                    "cost": cost,
                    "profit": profit,
                    "marketing_channel": channel,
                    "payment_method": payment,
                })

    orders = pd.DataFrame(rows).sort_values("order_date").reset_index(drop=True)
    return orders, first_order_by_customer


# ── Dirtiness injection (each operates on a copy) ────────────────────────────

PLACEHOLDERS = ["", "Unknown", "N/A", "-", "ERROR", "?"]


def _dirty_customers(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    n = len(df)

    for col in ["email", "region", "country"]:
        df[col] = df[col].astype(object)

    def pick(k):
        return rng.choice(n, size=min(k, n), replace=False)

    # Missing values / placeholders (~4%)
    for col in ["email", "region", "country"]:
        for idx in pick(int(n * 0.04)):
            df.at[idx, col] = rng.choice(PLACEHOLDERS)

    # Inconsistent casing / whitespace on region (~3%)
    for idx in pick(int(n * 0.03)):
        val = str(df.at[idx, "region"])
        df.at[idx, "region"] = rng.choice([val.upper(), val.lower(), f"  {val} ", f" {val.lower()}"])

    # A few full-row duplicates (~1%)
    dups = df.iloc[pick(max(1, int(n * 0.01)))].copy()
    df = pd.concat([df, dups], ignore_index=True)

    # Whitespace drift on the join key itself (~2%) — small enough that
    # Schema Discovery still finds the relationship (exact match still wins
    # on ~98% of rows), but real enough that independent cleaning of this
    # table vs. orders.csv could plausibly normalize it differently —
    # exactly the scenario reconciliation exists to catch.
    for idx in pick(int(n * 0.02)):
        df.at[idx, "customer_id"] = f" {df.at[idx, 'customer_id']} "

    df = df.drop(columns=["archetype"])
    return df.sample(frac=1, random_state=SEED).reset_index(drop=True)


def _dirty_products(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    n = len(df)

    for col in ["category", "unit_cost", "base_price"]:
        df[col] = df[col].astype(object)

    def pick(k):
        return rng.choice(n, size=min(k, n), replace=False)

    # Missing category (~5% — small catalogue, so this is 1-2 rows)
    for idx in pick(max(1, int(n * 0.05))):
        df.at[idx, "category"] = rng.choice(PLACEHOLDERS)

    # Type mismatch in price columns (a couple of rows)
    for idx in pick(2):
        df.at[idx, "base_price"] = rng.choice(["TBD", "N/A", "call for price"])

    # Whitespace drift on the join key (a couple of rows) — same rationale
    # as customers.customer_id above.
    for idx in pick(2):
        df.at[idx, "product_id"] = f"{df.at[idx, 'product_id']} "

    return df.sample(frac=1, random_state=SEED).reset_index(drop=True)


def _dirty_orders(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    n = len(df)

    for col in ["quantity", "unit_price", "marketing_channel", "payment_method",
                "order_date", "ship_date", "customer_id", "product_id"]:
        df[col] = df[col].astype(object)

    def pick(k):
        return rng.choice(n, size=min(k, n), replace=False)

    # Missing values / placeholders in non-critical fields (~3%)
    for col in ["marketing_channel", "payment_method"]:
        for idx in pick(int(n * 0.03)):
            df.at[idx, col] = rng.choice(PLACEHOLDERS)

    # Type mismatches in numeric columns (~1%)
    for idx in pick(int(n * 0.01)):
        df.at[idx, "quantity"] = rng.choice(["two", "three", "N/A", "TBD"])
    for idx in pick(int(n * 0.01)):
        df.at[idx, "unit_price"] = rng.choice(["free", "TBD", "-", "N/A"])

    # Outliers
    for idx in pick(10):
        df.at[idx, "unit_price"] = round(float(rng.uniform(5000, 99999)), 2)
    for idx in pick(10):
        df.at[idx, "quantity"] = int(rng.integers(500, 9999))

    # Duplicate rows (~1%)
    dups = df.iloc[pick(int(n * 0.01))].copy()
    df = pd.concat([df, dups], ignore_index=True)

    # Malformed dates
    bad_dates = ["13/25/2024", "2024/31/06", "Jan 15 2024", "15-01-2024",
                 "2024.03.20", "not_a_date", "02-30-2024", "2025-13-01"]
    for i, idx in enumerate(pick(len(bad_dates))):
        col = "order_date" if i % 2 == 0 else "ship_date"
        df.at[idx, col] = bad_dates[i]

    # Whitespace/casing drift on the two join keys (~2% each) — same
    # rationale as the dimension tables: small enough that Schema Discovery
    # still detects the relationship, real enough to exercise reconciliation.
    for idx in pick(int(n * 0.02)):
        df.at[idx, "customer_id"] = f"{df.at[idx, 'customer_id']} "
    for idx in pick(int(n * 0.02)):
        df.at[idx, "product_id"] = str(df.at[idx, "product_id"]).lower()

    return df.sample(frac=1, random_state=SEED).reset_index(drop=True)


def main() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(CLEAN_DIR, exist_ok=True)

    customers = _make_customers()
    products = _make_products()
    orders, first_order_by_customer = _generate_orders(customers, products)

    customers_clean = customers.drop(columns=["archetype"]).copy()
    customers_clean["signup_date"] = customers_clean["customer_id"].map(
        lambda cid: (first_order_by_customer.get(cid, START) - pd.Timedelta(days=int(rng.integers(1, 45)))).strftime("%Y-%m-%d")
    )
    products_clean = products.copy()
    orders_clean = orders.copy()

    customers_dirty = _dirty_customers(customers)
    # signup_date added after dirtying customers (dirtying operates on the
    # archetype-bearing frame; re-attach signup_date post-hoc so row order
    # from the shuffle doesn't need to carry it through).
    customers_dirty["signup_date"] = customers_dirty["customer_id"].str.strip().map(
        lambda cid: (first_order_by_customer.get(cid, START) - pd.Timedelta(days=int(rng.integers(1, 45)))).strftime("%Y-%m-%d")
        if cid in first_order_by_customer else ""
    )
    products_dirty = _dirty_products(products)
    orders_dirty = _dirty_orders(orders)

    customers_clean.to_csv(os.path.join(CLEAN_DIR, "customers.csv"), index=False)
    products_clean.to_csv(os.path.join(CLEAN_DIR, "products.csv"), index=False)
    orders_clean.to_csv(os.path.join(CLEAN_DIR, "orders.csv"), index=False)

    customers_dirty.to_csv(os.path.join(DATA_DIR, "customers.csv"), index=False)
    products_dirty.to_csv(os.path.join(DATA_DIR, "products.csv"), index=False)
    orders_dirty.to_csv(os.path.join(DATA_DIR, "orders.csv"), index=False)

    print("Bloom & Bean multi-table dataset generated")
    print(f"  customers: {len(customers_dirty):>6} rows -> {os.path.join(DATA_DIR, 'customers.csv')}")
    print(f"  products : {len(products_dirty):>6} rows -> {os.path.join(DATA_DIR, 'products.csv')}")
    print(f"  orders   : {len(orders_dirty):>6} rows -> {os.path.join(DATA_DIR, 'orders.csv')}")
    print(f"  orders span: {orders_clean['order_date'].min()} -> {orders_clean['order_date'].max()}")
    print(f"  unique orders: {orders_clean['order_id'].nunique()} | customers: {customers_clean['customer_id'].nunique()} | products: {products_clean['product_id'].nunique()}")
    print(f"  revenue total: {orders_clean['total_price'].sum():,.0f} | profit total: {orders_clean['profit'].sum():,.0f}")
    print(f"\n  Upload all 3 dirty files together to Data Cleaning: {DATA_DIR}")


if __name__ == "__main__":
    main()
