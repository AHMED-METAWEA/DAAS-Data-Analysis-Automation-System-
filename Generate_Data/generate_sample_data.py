"""
Generate a realistic dirty sales CSV for testing the cleaning pipeline.

Run this script directly to create data/sample.csv:
    python generate_sample_data.py
"""

import csv
import os
import random
from datetime import datetime, timedelta

random.seed(42)

# ── Configuration ──────────────────────────────────────────────────────

NUM_ROWS = 200
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "data", "sample.csv")

PRODUCTS = [
    ("Wireless Mouse", "Electronics", 25.99),
    ("USB-C Hub", "Electronics", 49.99),
    ("Mechanical Keyboard", "Electronics", 89.99),
    ("27\" Monitor", "Electronics", 349.99),
    ("Laptop Stand", "Accessories", 35.00),
    ("Webcam HD", "Electronics", 59.99),
    ("Desk Lamp", "Office", 22.50),
    ("Notebook Pack (5)", "Office", 12.99),
    ("Ergonomic Chair", "Furniture", 299.99),
    ("Standing Desk", "Furniture", 549.99),
    ("Phone Case", "Accessories", 15.99),
    ("Screen Protector", "Accessories", 9.99),
    ("Bluetooth Speaker", "Electronics", 39.99),
    ("Portable Charger", "Electronics", 29.99),
    ("Cable Organizer", "Accessories", 8.49),
]

REGIONS = ["North", "South", "East", "West", "Central"]
PAYMENT_METHODS = ["Credit Card", "PayPal", "Bank Transfer", "Cash on Delivery"]

FIRST_NAMES = [
    "Ahmed", "Sara", "Mohamed", "Fatima", "Omar", "Layla", "Youssef", "Nour",
    "Ali", "Hana", "Hassan", "Mona", "Khaled", "Dina", "Tarek", "Reem",
    "Amir", "Salma", "Karim", "Yasmin", "John", "Emma", "David", "Sophie",
    "James", "Olivia", "Robert", "Ava", "William", "Mia",
]

LAST_NAMES = [
    "Ibrahim", "Hassan", "Ali", "Mohamed", "Ahmed", "Salem", "Nasser",
    "Farouk", "Mansour", "Khalil", "Smith", "Johnson", "Williams", "Brown",
    "Jones", "Garcia", "Miller", "Davis", "Wilson", "Taylor",
]

# ── Helpers ────────────────────────────────────────────────────────────


def random_date(start: datetime, end: datetime) -> datetime:
    delta = end - start
    return start + timedelta(seconds=random.randint(0, int(delta.total_seconds())))


def random_email(name: str) -> str:
    domains = ["gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "company.co"]
    clean = name.lower().replace(" ", ".")
    return f"{clean}{random.randint(1, 999)}@{random.choice(domains)}"


# ── Generate rows ─────────────────────────────────────────────────────

def generate_rows():
    rows = []
    start_date = datetime(2024, 1, 1)
    end_date = datetime(2024, 12, 31)

    for i in range(1, NUM_ROWS + 1):
        product, category, base_price = random.choice(PRODUCTS)
        first = random.choice(FIRST_NAMES)
        last = random.choice(LAST_NAMES)
        name = f"{first} {last}"
        email = random_email(name)
        qty = random.randint(1, 10)
        unit_price = round(base_price * random.uniform(0.9, 1.1), 2)
        total = round(qty * unit_price, 2)
        order_date = random_date(start_date, end_date)
        ship_date = order_date + timedelta(days=random.randint(1, 14))
        region = random.choice(REGIONS)
        payment = random.choice(PAYMENT_METHODS)

        row = {
            "order_id": f"ORD-{1000 + i}",
            "customer_name": name,
            "email": email,
            "product": product,
            "category": category,
            "quantity": str(qty),
            "unit_price": str(unit_price),
            "total_price": str(total),
            "order_date": order_date.strftime("%Y-%m-%d"),
            "ship_date": ship_date.strftime("%Y-%m-%d"),
            "region": region,
            "payment_method": payment,
        }
        rows.append(row)

    # ── Inject intentional data quality issues ─────────────────────────

    # 1. Missing values (~8% of cells scattered)
    fields_to_null = ["customer_name", "email", "quantity", "unit_price",
                      "total_price", "region", "payment_method", "category"]
    for _ in range(int(NUM_ROWS * 0.08) * len(fields_to_null) // 4):
        idx = random.randint(0, NUM_ROWS - 1)
        field = random.choice(fields_to_null)
        rows[idx][field] = ""

    # 2. Wrong types in numeric columns (strings where numbers expected)
    type_error_indices = random.sample(range(NUM_ROWS), 8)
    for idx in type_error_indices[:4]:
        rows[idx]["quantity"] = random.choice(["five", "N/A", "??", "three"])
    for idx in type_error_indices[4:]:
        rows[idx]["unit_price"] = random.choice(["free", "TBD", "-", "N/A"])

    # 3. Outliers in numeric columns
    outlier_indices = random.sample(range(NUM_ROWS), 6)
    for idx in outlier_indices[:3]:
        rows[idx]["unit_price"] = str(round(random.uniform(5000, 99999), 2))
        rows[idx]["total_price"] = str(
            round(float(rows[idx]["unit_price"]) * random.randint(1, 5), 2)
        )
    for idx in outlier_indices[3:]:
        rows[idx]["quantity"] = str(random.randint(500, 9999))
        try:
            price = float(rows[idx]["unit_price"])
            rows[idx]["total_price"] = str(round(price * int(rows[idx]["quantity"]), 2))
        except ValueError:
            rows[idx]["total_price"] = "99999.99"

    # 4. Duplicate rows (copy 5 existing rows)
    dup_indices = random.sample(range(NUM_ROWS), 5)
    for idx in dup_indices:
        rows.append(dict(rows[idx]))

    # 5. Malformed dates
    bad_date_indices = random.sample(range(NUM_ROWS), 8)
    bad_formats = [
        "13/25/2024",       # month/day swapped and invalid
        "2024/31/06",       # wrong separator
        "Jan 15 2024",      # text month
        "15-01-2024",       # DD-MM-YYYY
        "2024.03.20",       # dot separator
        "not_a_date",       # garbage
        "02-30-2024",       # impossible date
        "2024-13-01",       # month 13
    ]
    for i, idx in enumerate(bad_date_indices):
        if i < len(bad_formats):
            if i % 2 == 0:
                rows[idx]["order_date"] = bad_formats[i]
            else:
                rows[idx]["ship_date"] = bad_formats[i]

    # 6. Inconsistent casing / whitespace
    case_indices = random.sample(range(NUM_ROWS), 6)
    for idx in case_indices:
        region = rows[idx]["region"]
        rows[idx]["region"] = random.choice([
            region.upper(), region.lower(), f"  {region}  ", f" {region.lower()} "
        ])

    # Shuffle to mix issues throughout
    random.shuffle(rows)

    return rows


# ── Write CSV ──────────────────────────────────────────────────────────

def main():
    rows = generate_rows()
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)

    fieldnames = [
        "order_id", "customer_name", "email", "product", "category",
        "quantity", "unit_price", "total_price", "order_date", "ship_date",
        "region", "payment_method",
    ]

    with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Generated {len(rows)} rows -> {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
