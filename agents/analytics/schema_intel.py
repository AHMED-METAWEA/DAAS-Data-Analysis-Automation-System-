"""
Schema Intelligence Layer — heuristically detect column roles from a
DataFrame without any user input.

Detection targets:
  - time / date column
  - revenue / price / monetary columns
  - quantity column
  - product identifier
  - customer identifier
  - transaction / order identifier
  - category / segment columns
"""

from __future__ import annotations

import pandas as pd

_TIME_KEYWORDS = [
    "date", "time", "timestamp", "datetime", "day", "month", "year",
    "order_date", "created_at", "updated_at", "transaction_date",
    "ship_date", "invoice_date", "period",
]
_REVENUE_KEYWORDS = [
    "revenue", "sales", "total", "amount", "price", "spend", "value",
    "gmv", "net", "gross", "profit", "income", "cost", "fee", "payment",
    "total_spent", "unit_price", "line_total", "subtotal",
]
_QUANTITY_KEYWORDS = [
    "quantity", "qty", "count", "num", "number", "units", "volume",
    "items", "pcs",
]
_PRODUCT_KEYWORDS = [
    "product", "item", "sku", "stock_code", "catalog", "article",
    "good", "merchandise", "variant",
]
_CUSTOMER_KEYWORDS = [
    "customer", "client", "user", "member", "buyer", "account",
    "contact", "email", "phone", "loyalty",
]
_ORDER_KEYWORDS = [
    "order", "transaction", "invoice", "receipt", "ticket", "reference",
    "cart", "checkout", "purchase",
]
_CATEGORY_KEYWORDS = [
    "category", "segment", "group", "class", "type", "department",
    "division", "family", "subcategory", "channel",
]


def _is_text_like(series: pd.Series) -> bool:
    """True for object / string / categorical columns (pandas 2.x and 3.x).

    pandas 3.0 stores plain text as a dedicated ``string`` dtype rather than
    ``object``, so a bare ``dtype == object`` check misses text columns.
    """
    dtype = series.dtype
    return (
        dtype == object  # noqa: E721 - numpy dtype equality, not a type() comparison
        or pd.api.types.is_string_dtype(series)
        or isinstance(dtype, pd.CategoricalDtype)
    )


def _col_match(name: str, keywords: list[str]) -> bool:
    lowered = name.lower().replace("_", "").replace("-", "").replace(" ", "")
    for kw in keywords:
        pat = kw.lower().replace("_", "")
        if pat in lowered:
            return True
    return False


def _score_column(name: str, keywords: list[str]) -> int:
    lowered = name.lower().replace("_", "").replace("-", "").replace(" ", "")
    score = 0
    for kw in keywords:
        pat = kw.lower().replace("_", "")
        if pat in lowered:
            score += 1
            if lowered == pat or lowered.startswith(pat) or lowered.endswith(pat):
                score += 2
    return score


def detect_time_column(df: pd.DataFrame) -> str | None:
    candidates = []
    for col in df.columns:
        score = _score_column(col, _TIME_KEYWORDS)
        if score > 0:
            candidates.append((score, col))
        elif pd.api.types.is_datetime64_any_dtype(df[col]):
            candidates.append((3, col))

    if not candidates:
        for col in df.columns:
            if pd.api.types.is_datetime64_any_dtype(df[col]):
                candidates.append((1, col))
            elif df[col].dtype == "object":
                sample = df[col].dropna().head(20)
                if sample.empty:
                    continue
                try:
                    pd.to_datetime(sample)
                    candidates.append((1, col))
                except (ValueError, TypeError):
                    pass

    if not candidates:
        return None
    candidates.sort(key=lambda x: -x[0])
    return candidates[0][1]


# Column names that represent *gross revenue* (the line/order total) and should
# outrank per-unit or non-revenue money columns (unit_price, cost, profit) when
# choosing the primary revenue column. Boost only — nothing is excluded.
_STRONG_REVENUE = [
    "revenue", "sales", "gmv", "total", "amount", "grand_total",
    "line_total", "subtotal", "net_total", "order_value", "total_spent",
]


def _monetary_candidates(df: pd.DataFrame) -> list[tuple[int, str]]:
    """Scored monetary candidates, best first. Score 0 = numeric-type fallback
    (no money-like name), which the validator treats as low-confidence."""
    candidates = []
    for col in df.columns:
        score = _score_column(col, _REVENUE_KEYWORDS)
        if score > 0 and pd.api.types.is_numeric_dtype(df[col]):
            # Prefer true revenue/total columns over unit_price / cost / profit.
            if _col_match(col, _STRONG_REVENUE):
                score += 6
            candidates.append((score, col))
    if not candidates:
        for col in df.columns:
            if pd.api.types.is_numeric_dtype(df[col]) and col.lower() not in ("id", "index"):
                candidates.append((0, col))
    candidates.sort(key=lambda x: -x[0])
    return candidates


def detect_monetary_columns(df: pd.DataFrame) -> list[str]:
    return [c for _, c in _monetary_candidates(df)]


def detect_quantity_column(df: pd.DataFrame) -> str | None:
    candidates = []
    for col in df.columns:
        score = _score_column(col, _QUANTITY_KEYWORDS)
        if score > 0 and pd.api.types.is_numeric_dtype(df[col]):
            candidates.append((score, col))
    if not candidates:
        for col in df.columns:
            if pd.api.types.is_integer_dtype(df[col]) and col.lower() not in ("id", "index"):
                candidates.append((0, col))
    candidates.sort(key=lambda x: -x[0])
    return candidates[0][1] if candidates else None


def detect_product_column(df: pd.DataFrame) -> str | None:
    candidates = []
    for col in df.columns:
        score = _score_column(col, _PRODUCT_KEYWORDS)
        if score > 0:
            candidates.append((score, col))
    candidates.sort(key=lambda x: -x[0])
    return candidates[0][1] if candidates else None


def detect_customer_column(df: pd.DataFrame) -> str | None:
    candidates = []
    for col in df.columns:
        score = _score_column(col, _CUSTOMER_KEYWORDS)
        if score > 0:
            candidates.append((score, col))
    candidates.sort(key=lambda x: -x[0])
    return candidates[0][1] if candidates else None


def detect_order_column(df: pd.DataFrame) -> str | None:
    candidates = []
    for col in df.columns:
        # An order identifier is never a timestamp. Without this guard a column
        # named "order_date" (which shares the "order" token) outranks the real
        # "order_id", and AOV/order-count would then group by date, not order.
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            continue
        score = _score_column(col, _ORDER_KEYWORDS)
        if score > 0:
            # A name that reads at least as strongly as a date ("order_date",
            # "invoice_date") is a timestamp, not the order id — skip it.
            if _score_column(col, _TIME_KEYWORDS) >= score:
                continue
            candidates.append((score, col))
    candidates.sort(key=lambda x: -x[0])
    return candidates[0][1] if candidates else None


def detect_category_columns(df: pd.DataFrame) -> list[str]:
    candidates = []
    for col in df.columns:
        score = _score_column(col, _CATEGORY_KEYWORDS)
        if score > 0 and _is_text_like(df[col]):
            candidates.append((score, col))
    candidates.sort(key=lambda x: -x[0])
    return [c[1] for c in candidates]


def _name_confidence(col: str | None, keywords: list[str]) -> str:
    """How strongly a column's NAME implies a role: high / medium / low / none."""
    if not col:
        return "none"
    score = _score_column(col, keywords)
    if score >= 3:
        return "high"
    if score >= 1:
        return "medium"
    return "low"  # detected positionally / by dtype, not by name


def validate_schema(df: pd.DataFrame, roles: dict) -> tuple[dict, list[str]]:
    """Sanity-check the auto-detected roles and surface any ambiguity.

    Returns ``(confidence, warnings)``. ``confidence`` is a per-role
    high/medium/low/none label; ``warnings`` are plain-language cautions the
    report and UI can show so a wrong auto-detection is *visible* rather than
    silently trusted. This is the guardrail around the zero-config schema
    heuristics — it never blocks, it discloses.
    """
    n = len(df)
    monetary = roles["monetary_columns"]
    revenue_col = monetary[0] if monetary else None
    time_col = roles["time_column"]
    customer_col = roles["customer_column"]
    order_col = roles["order_column"]
    quantity_col = roles["quantity_column"]

    confidence = {
        "time_column": (
            "high" if (time_col and (pd.api.types.is_datetime64_any_dtype(df[time_col])
                                     or _score_column(time_col, _TIME_KEYWORDS) >= 3))
            else _name_confidence(time_col, _TIME_KEYWORDS)
        ),
        "revenue_column": (
            "low" if (revenue_col and _score_column(revenue_col, _REVENUE_KEYWORDS) == 0)
            else _name_confidence(revenue_col, _REVENUE_KEYWORDS)
        ),
        "quantity_column": (
            "low" if (quantity_col and _score_column(quantity_col, _QUANTITY_KEYWORDS) == 0)
            else _name_confidence(quantity_col, _QUANTITY_KEYWORDS)
        ),
        "customer_column": _name_confidence(customer_col, _CUSTOMER_KEYWORDS),
        "order_column": _name_confidence(order_col, _ORDER_KEYWORDS),
        "product_column": _name_confidence(roles["product_column"], _PRODUCT_KEYWORDS),
    }

    warnings: list[str] = []

    # ── Revenue: the highest-stakes detection ───────────────────────────────
    if not revenue_col:
        warnings.append(
            "No revenue/monetary column detected — revenue-based KPIs (revenue, "
            "AOV, revenue-at-risk) are unavailable."
        )
    else:
        if confidence["revenue_column"] == "low":
            warnings.append(
                f"Revenue column was auto-detected by data type as '{revenue_col}' "
                "(no money-like name) — verify it holds sales amounts before "
                "trusting revenue figures."
            )
        col = pd.to_numeric(df[revenue_col], errors="coerce").dropna()
        if len(col) and float((col < 0).mean()) > 0.5:
            warnings.append(
                f"'{revenue_col}' is mostly negative — unusual for revenue "
                "(refunds/adjustments?); revenue totals may be misleading."
            )
        # Ambiguity: a second money-named column scores just as high.
        scored = [c for c in _monetary_candidates(df) if c[0] > 0]
        if len(scored) >= 2 and scored[0][0] == scored[1][0]:
            warnings.append(
                f"Multiple columns could be revenue ('{scored[0][1]}', "
                f"'{scored[1][1]}'); using '{scored[0][1]}'. Confirm this is the "
                "sales amount."
            )

    # ── Customer id sanity: unique-per-row means grouping is meaningless ─────
    if customer_col and n > 1:
        nun = int(df[customer_col].nunique(dropna=True))
        if nun == n:
            warnings.append(
                f"Customer column '{customer_col}' has a distinct value in every "
                "row — it may be a row/transaction id rather than a customer id; "
                "repeat-rate and churn metrics would then be unreliable."
            )

    # ── Missing time: silently disables a lot downstream ────────────────────
    if not time_col:
        warnings.append(
            "No date/time column detected — growth, seasonality, forecasting "
            "and churn analysis are unavailable."
        )

    return confidence, warnings


def build_schema_summary(df: pd.DataFrame) -> dict:
    roles = {
        "time_column": detect_time_column(df),
        "monetary_columns": detect_monetary_columns(df),
        "quantity_column": detect_quantity_column(df),
        "product_column": detect_product_column(df),
        "customer_column": detect_customer_column(df),
        "order_column": detect_order_column(df),
        "category_columns": detect_category_columns(df),
        "row_count": len(df),
        "column_count": len(df.columns),
    }
    confidence, warnings = validate_schema(df, roles)
    # Additive keys only — existing consumers read the role fields unchanged.
    roles["detection_confidence"] = confidence
    roles["warnings"] = warnings
    return roles
