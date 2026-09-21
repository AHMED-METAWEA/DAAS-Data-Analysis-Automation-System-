"""
Generate a realistic e-commerce dataset for a fictional business — "Bloom & Bean",
an online coffee roastery & equipment store — designed to exercise EVERY agent:

  • Data Cleaning  → moderate, realistic dirtiness (missing, placeholders, type
                     mismatches, outliers, duplicates, malformed dates, casing).
  • Analytics/KPIs → revenue, orders, customers, products, categories, profit.
  • Visualization  → many metrics × dimensions for auto-dashboards.
  • Forecasting    → 24 months of daily history with growth trend + weekly &
                     holiday seasonality + injected anomalies (Black Friday spike,
                     a supply-outage dip) so model back-testing has signal.
  • Marketing/RFM  → a fixed customer base with repeat purchases and deliberate
                     Recency/Frequency/Monetary archetypes (Champions … Lost).

Outputs (in data/):
  bloom_and_bean_sales.csv        — dirty, realistic (UPLOAD THIS to Data Cleaning)
  bloom_and_bean_sales_clean.csv  — clean reference (for direct testing / diffing)

Usage:
    python generate_business_data.py
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd

SEED = 7
rng = np.random.default_rng(SEED)

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
DIRTY_PATH = os.path.join(DATA_DIR, "bloom_and_bean_sales.csv")
CLEAN_PATH = os.path.join(DATA_DIR, "bloom_and_bean_sales_clean.csv")

START = pd.Timestamp("2023-06-01")
END = pd.Timestamp("2025-05-31")
N_CUSTOMERS = 420

# ── Catalogue ────────────────────────────────────────────────────────────────
# (product, category, base_price, unit_cost)
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
SUBSCRIPTION_PRODUCTS = [p for p in PRODUCTS if p[1] == "Subscription"]
NON_SUB_PRODUCTS = [p for p in PRODUCTS if p[1] != "Subscription"]

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
# prob, order-count range, quantity bonus, subscription likelihood,
# and the active window (fraction of the 2-year timeline they purchase within).
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
    """Sampling weight per day: growth trend × weekly × holiday seasonality × noise,
    with a Black-Friday spike and a one-week supply-outage dip each year."""
    n = len(days)
    t = np.arange(n)

    trend = 1.0 + 0.0009 * t                       # ~ +65% over two years
    dow = days.dayofweek.to_numpy()
    weekly = np.ones(n)
    weekly[np.isin(dow, [4, 5])] = 1.30            # Fri/Sat uplift
    weekly[dow == 6] = 1.12                         # Sun
    weekly[dow == 0] = 0.92                         # Mon dip

    month = days.month.to_numpy()
    holiday = np.where(np.isin(month, [11, 12]), 1.45, 1.0)   # Q4 gifting
    holiday = np.where(np.isin(month, [7, 8]), 0.85, holiday)  # summer lull

    noise = rng.normal(1.0, 0.08, n).clip(0.4, None)
    weight = trend * weekly * holiday * noise

    # Black Friday spike (4th Friday of November) each year
    for yr in days.year.unique():
        novs = days[(days.year == yr) & (days.month == 11) & (days.dayofweek == 4)]
        if len(novs) >= 4:
            bf = novs[3]
            weight[days.get_loc(bf)] *= 4.0
            # Cyber-Monday tail
            cm_loc = days.get_loc(bf) + 3
            if cm_loc < n:
                weight[cm_loc] *= 2.2

    # Supply-outage dip: one quiet week each spring
    for yr in days.year.unique():
        outage = np.asarray((days.year == yr) & (days.month == 3)
                            & (days.day >= 10) & (days.day <= 16))
        weight[outage] *= 0.15

    return weight.clip(min=0.05)


def _make_customers() -> pd.DataFrame:
    names = ["Champion", "Loyal", "Potential", "New", "AtRisk", "Hibernating", "Lost", "OneTime"]
    probs = [ARCHETYPES[n]["p"] for n in names]
    assigned = rng.choice(names, size=N_CUSTOMERS, p=probs)
    rows = []
    for i, arch in enumerate(assigned, start=1):
        first, last = rng.choice(FIRST), rng.choice(LAST)
        full = f"{first} {last}"
        email = f"{full.lower().replace(' ', '.')}{rng.integers(1, 999)}@" \
                f"{rng.choice(['gmail.com', 'outlook.com', 'yahoo.com', 'bloombean.co'])}"
        rows.append({
            "customer_id": f"CUST-{2000 + i}",
            "customer_name": full,
            "email": email,
            "region": rng.choice(REGIONS),
            "country": rng.choice(COUNTRIES),
            "archetype": arch,
        })
    return pd.DataFrame(rows)


def _sample_day_indices(n: int, lo_frac: float, hi_frac: float,
                        prob: np.ndarray, n_days: int) -> np.ndarray:
    lo = int(lo_frac * (n_days - 1))
    hi = int(hi_frac * (n_days - 1))
    idx = np.arange(lo, hi + 1)
    p = prob[lo:hi + 1]
    p = p / p.sum()
    return rng.choice(idx, size=n, replace=True, p=p)


def generate() -> pd.DataFrame:
    days = pd.date_range(START, END, freq="D")
    n_days = len(days)
    prob = _daily_demand(days)
    customers = _make_customers()

    rows = []
    order_seq = 100000
    for _, cust in customers.iterrows():
        spec = ARCHETYPES[cust["archetype"]]
        n_orders = int(rng.integers(spec["orders"][0], spec["orders"][1] + 1))
        day_idx = _sample_day_indices(n_orders, spec["lo"], spec["hi"], prob, n_days)

        for di in sorted(day_idx):
            order_seq += 1
            order_id = f"ORD-{order_seq}"
            order_date = days[di]
            ship_date = order_date + pd.Timedelta(days=int(rng.integers(1, 8)))
            channel = rng.choice(CHANNELS)
            payment = rng.choice(PAYMENTS)
            n_lines = int(rng.choice([1, 2, 3], p=[0.5, 0.3, 0.2]))

            for _ in range(n_lines):
                if rng.random() < spec["sub"] and SUBSCRIPTION_PRODUCTS:
                    product, category, base_price, unit_cost = SUBSCRIPTION_PRODUCTS[
                        rng.integers(0, len(SUBSCRIPTION_PRODUCTS))]
                else:
                    product, category, base_price, unit_cost = NON_SUB_PRODUCTS[
                        rng.integers(0, len(NON_SUB_PRODUCTS))]

                qty = int(rng.integers(1, 4) + spec["qty_bonus"])
                if category in ("Equipment",):
                    qty = 1
                unit_price = round(base_price * rng.uniform(0.98, 1.06), 2)
                discount = int(rng.choice(DISCOUNTS))
                total_price = round(qty * unit_price * (1 - discount / 100), 2)
                cost = round(qty * unit_cost, 2)
                profit = round(total_price - cost, 2)

                rows.append({
                    "order_id": order_id,
                    "order_date": order_date.strftime("%Y-%m-%d"),
                    "ship_date": ship_date.strftime("%Y-%m-%d"),
                    "customer_id": cust["customer_id"],
                    "customer_name": cust["customer_name"],
                    "email": cust["email"],
                    "product": product,
                    "category": category,
                    "quantity": qty,
                    "unit_price": unit_price,
                    "discount_pct": discount,
                    "total_price": total_price,
                    "cost": cost,
                    "profit": profit,
                    "region": cust["region"],
                    "country": cust["country"],
                    "marketing_channel": channel,
                    "payment_method": payment,
                })

    df = pd.DataFrame(rows).sort_values("order_date").reset_index(drop=True)
    return df


# ── Dirtiness injection (operates on a copy) ─────────────────────────────────

PLACEHOLDERS = ["", "Unknown", "N/A", "-", "ERROR", "?"]


def _dirty(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    n = len(df)

    # Columns that will receive string junk must allow object values.
    for col in ["quantity", "unit_price", "total_price", "region", "category",
                "payment_method", "marketing_channel", "email", "order_date", "ship_date"]:
        df[col] = df[col].astype(object)

    def pick(k):
        return rng.choice(n, size=k, replace=False)

    # 1. Missing values / placeholders in non-critical fields (~3%)
    for col in ["region", "payment_method", "marketing_channel", "email", "category"]:
        for idx in pick(int(n * 0.03)):
            df.at[idx, col] = rng.choice(PLACEHOLDERS)

    # 2. Type mismatches in numeric columns (~1%)
    for idx in pick(int(n * 0.01)):
        df.at[idx, "quantity"] = rng.choice(["two", "three", "N/A", "TBD"])
    for idx in pick(int(n * 0.01)):
        df.at[idx, "unit_price"] = rng.choice(["free", "TBD", "-", "N/A"])

    # 3. Outliers (a handful of absurd prices / quantities)
    for idx in pick(8):
        df.at[idx, "unit_price"] = round(float(rng.uniform(5000, 99999)), 2)
    for idx in pick(8):
        df.at[idx, "quantity"] = int(rng.integers(500, 9999))

    # 4. Duplicate rows (~1%)
    dups = df.iloc[pick(int(n * 0.01))].copy()
    df = pd.concat([df, dups], ignore_index=True)

    # 5. Malformed dates
    bad_dates = ["13/25/2024", "2024/31/06", "Jan 15 2024", "15-01-2024",
                 "2024.03.20", "not_a_date", "02-30-2024", "2025-13-01"]
    for i, idx in enumerate(pick(len(bad_dates))):
        col = "order_date" if i % 2 == 0 else "ship_date"
        df.at[idx, col] = bad_dates[i]

    # 6. Inconsistent casing / whitespace
    for idx in pick(int(n * 0.015)):
        val = str(df.at[idx, "region"])
        df.at[idx, "region"] = rng.choice([val.upper(), val.lower(), f"  {val} ", f" {val.lower()}"])

    return df.sample(frac=1, random_state=SEED).reset_index(drop=True)


def main() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    clean = generate()
    dirty = _dirty(clean)

    clean.to_csv(CLEAN_PATH, index=False)
    dirty.to_csv(DIRTY_PATH, index=False)

    daily_rev = clean.assign(_d=pd.to_datetime(clean["order_date"])).groupby("_d")["total_price"].sum()
    print("Bloom & Bean dataset generated")
    print(f"  clean : {len(clean):>6} rows  -> {CLEAN_PATH}")
    print(f"  dirty : {len(dirty):>6} rows  -> {DIRTY_PATH}")
    print(f"  orders: {clean['order_id'].nunique():>6} | customers: {clean['customer_id'].nunique()}")
    print(f"  span  : {clean['order_date'].min()} -> {clean['order_date'].max()} ({len(daily_rev)} active days)")
    print(f"  revenue total: {clean['total_price'].sum():,.0f} | profit total: {clean['profit'].sum():,.0f}")


if __name__ == "__main__":
    main()
