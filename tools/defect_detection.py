"""Deterministic data-quality defect detection.

Every check here is pure pandas — no LLM, no sampling luck, no prompt. It runs
before the planner and produces two things:

  * ``DefectFinding`` objects: what is wrong, how many values, with evidence.
  * a *complete baseline cleaning plan* via ``plan_from_defects`` — typed steps
    from ``tools.cleaning_ops.REGISTRY``, already in a safe execution order.

That second point is the important one. The LLM planner is no longer the thing
that decides whether a defect exists; it reviews and adjusts a plan that was
derived deterministically. If the LLM is unavailable, rate-limited, or returns
nonsense, the baseline plan still cleans the table correctly.

Why this exists as a separate module from ``tools/profiler_tools.py``: the old
detection was gated on ``pandas.api.types.is_string_dtype``, which is False for
any column mixing strings with numbers — so placeholder and type-mismatch
detection silently skipped exactly the dirtiest columns in a real file. It also
required a numeric-looking ratio strictly below 1.0, so a column where *every*
value was a numeric string ("1001", "2024") was declared perfectly clean and
shipped to Postgres as text. Both classes are covered here and regression-
tested in ``tests/test_defect_detection.py``.
"""

from __future__ import annotations

import re
from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from models.cleaning_ops import CleaningStep
from tools.cleaning_ops import (
    COMMON_SENTINELS,
    _as_text,
    category_key,
    infer_dayfirst,
    null_token_mask,
    order_steps,
    parse_datetime_series,
    parse_numeric_series,
)

Severity = Literal["blocking", "high", "medium", "low", "info"]

# Name hints, anchored on word boundaries. The old profiler substring-matched
# bare "id" and "ord" anywhere in a name, so `paid`, `valid`, `width`, `word`
# and `record` were all classified as identifiers, and `birthday`/`downtime` as
# dates. The boundary set includes spaces and hyphens because real spreadsheet
# exports are full of "Order ID" and "Customer-Name", not just snake_case.
_B = r"[\s_\-.]"
_ID_NAME_RE = re.compile(rf"(^|{_B})(id|ids|code|key|no|num|number|sku|ref|uuid|guid)($|{_B})", re.I)
_DATE_NAME_RE = re.compile(
    rf"(^|{_B})(date|datetime|timestamp|time|day|month|year|dob)($|{_B})", re.I
)
_NON_NEGATIVE_NAME_RE = re.compile(
    rf"(^|{_B})(qty|quantity|count|amount|price|cost|total|revenue|sales|units|stock|age|discount)($|{_B})",
    re.I,
)
_BOOL_NAME_RE = re.compile(
    rf"(^|{_B})(is|has|was|flag|active|churn|paid|returned|cancelled)($|{_B})", re.I
)

_FORMATTED_NUMERIC_RE = re.compile(r"^[^\d]*[\d][\d.,\s]*[^\d]*$")
_CURRENCY_RE = re.compile(r"[$€£¥₹﷼]|\b(usd|eur|gbp|egp|sar|aed|kwd)\b|ج\.م|ر\.س|د\.إ", re.I)

_SAMPLE = 5



class DefectFinding(BaseModel):
    """One concrete, measured data-quality defect."""

    kind: str
    column: str = ""
    severity: Severity = "medium"
    count: int = 0
    detail: str = ""
    evidence: list = Field(default_factory=list)
    suggested_op: str = ""
    suggested_columns: list[str] = Field(default_factory=list)
    suggested_params: dict[str, Any] = Field(default_factory=dict)
    auto_fixable: bool = True


def _evidence(values: pd.Series, limit: int = _SAMPLE) -> list:
    out = []
    for v in values.dropna().unique()[:limit]:
        out.append(v.item() if hasattr(v, "item") else (str(v) if not isinstance(v, (int, float, bool, str)) else v))
    return out


# ══════════════════════════════════════════════════════════════════════
# Column-level detectors
# ══════════════════════════════════════════════════════════════════════


def _detect_placeholders(name: str, series: pd.Series) -> DefectFinding | None:
    mask = null_token_mask(series)
    count = int(mask.sum())
    if not count:
        return None
    return DefectFinding(
        kind="placeholder_values", column=name, severity="high", count=count,
        detail=f"{count} value(s) are placeholders for missing data, not real values",
        evidence=_evidence(_as_text(series)[mask]),
        suggested_op="standardize_null_placeholders", suggested_columns=[name],
    )


def _detect_sentinels(name: str, series: pd.Series) -> DefectFinding | None:
    numeric = pd.to_numeric(_as_text(series), errors="coerce")
    live = numeric.dropna()
    if len(live) < 5:
        return None
    hits = {}
    for sentinel in COMMON_SENTINELS:
        n = int((live == sentinel).sum())
        # A sentinel is a repeated, isolated spike far from the rest of the
        # distribution — not simply any occurrence of the number.
        if n >= 2 and n / len(live) < 0.5:
            rest = live[live != sentinel]
            if rest.empty:
                continue
            spread = rest.std() or 1.0
            if abs(sentinel - rest.median()) > 5 * spread:
                hits[sentinel] = n
    if not hits:
        return None
    total = sum(hits.values())
    return DefectFinding(
        kind="numeric_sentinel", column=name, severity="high", count=total,
        detail=(
            f"{total} value(s) look like missing-data sentinels "
            f"({', '.join(str(int(k)) for k in hits)}) rather than real measurements"
        ),
        evidence=[int(k) for k in hits],
        suggested_op="replace_sentinels", suggested_columns=[name],
        suggested_params={"values": [float(k) for k in hits]},
    )


def _detect_numeric_as_text(name: str, series: pd.Series) -> DefectFinding | None:
    """Numbers stored as text — including the case where *every* value parses.

    The old check required a numeric-looking ratio strictly below 1.0, so a
    fully numeric text column was never flagged. That is the guaranteed output
    of Arabic digit normalization (٢٠٢٤ -> "2024" stays a string) and of most
    spreadsheet and database-text sources.
    """
    if pd.api.types.is_numeric_dtype(series) or pd.api.types.is_bool_dtype(series):
        return None
    if pd.api.types.is_datetime64_any_dtype(series):
        return None
    # Judge the column as it will be *after* placeholder standardisation, which
    # is always an earlier step in the plan. Otherwise a money column holding
    # one "UNKNOWN" among three prices reads as 75% numeric, falls under the
    # threshold, and never gets converted at all.
    live = _as_text(series)[~null_token_mask(series)].dropna()
    if live.empty or _DATE_NAME_RE.search(name):
        return None

    parsed = parse_numeric_series(live)
    ratio = float(parsed.notna().mean())
    if ratio < 0.8:
        return None

    raw = live.astype(str)
    formatted = bool(raw.map(lambda v: bool(_CURRENCY_RE.search(v)) or "%" in v).any())
    # Distinguish "text that is a number" from a genuine code/identifier: an id
    # column of numeric strings should stay text, not silently become a float.
    if _ID_NAME_RE.search(name) and not formatted:
        return None

    unparseable = live[parsed.isna()]
    detail = f"{ratio:.0%} of values are numbers stored as text"
    if formatted:
        detail += " with currency/percent formatting"
    if len(unparseable):
        detail += f"; {len(unparseable)} value(s) will not parse and become NULL"
    return DefectFinding(
        kind="numeric_stored_as_text", column=name,
        severity="high" if ratio == 1.0 or formatted else "medium",
        count=int(parsed.notna().sum()), detail=detail,
        evidence=_evidence(raw),
        suggested_op="parse_numeric", suggested_columns=[name],
    )


def _detect_date_as_text(name: str, series: pd.Series) -> DefectFinding | None:
    if pd.api.types.is_datetime64_any_dtype(series):
        return None
    # As with numeric detection: placeholders are nulled by an earlier step, so
    # they must not dilute the parse rate here.
    live = _as_text(series)[~null_token_mask(series)].dropna()
    if live.empty:
        return None

    name_says_date = bool(_DATE_NAME_RE.search(name))
    if not name_says_date and pd.api.types.is_numeric_dtype(series):
        return None

    parsed = parse_datetime_series(live)
    ratio = float(parsed.notna().mean())
    if ratio < (0.6 if name_says_date else 0.9):
        return None
    if not name_says_date and live.astype(str).map(lambda v: bool(re.search(r"[/\-:]", v))).mean() < 0.8:
        return None

    ambiguous = infer_dayfirst(series) is None and live.astype(str).map(
        lambda v: bool(re.match(r"^\s*\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}\s*$", v))
    ).any()
    detail = f"{ratio:.0%} of values are dates stored as text"
    if ambiguous:
        detail += (
            " — and the day/month order is ambiguous (no value has a component above 12), "
            "so the format must be confirmed rather than guessed"
        )
    unparseable = int(parsed.isna().sum())
    if unparseable:
        detail += f"; {unparseable} value(s) will not parse and become NULL"

    return DefectFinding(
        kind="date_stored_as_text", column=name,
        severity="high" if ambiguous else "medium",
        count=int(parsed.notna().sum()), detail=detail,
        evidence=_evidence(live.astype(str)),
        suggested_op="parse_datetime", suggested_columns=[name],
        auto_fixable=not ambiguous,
    )


def _detect_boolean_as_text(name: str, series: pd.Series) -> DefectFinding | None:
    if pd.api.types.is_bool_dtype(series):
        return None
    live = _as_text(series).dropna()
    if live.empty:
        return None
    tokens = {str(v).strip().casefold() for v in live.unique()}
    if len(tokens) > 4 or not tokens:
        return None
    truthy = {"true", "t", "yes", "y", "1", "1.0", "نعم"}
    falsy = {"false", "f", "no", "n", "0", "0.0", "لا"}
    if not tokens <= (truthy | falsy):
        return None
    if not (tokens & truthy and tokens & falsy) and not _BOOL_NAME_RE.search(name):
        return None
    if tokens <= {"0", "1", "0.0", "1.0"} and not _BOOL_NAME_RE.search(name):
        return None  # a 0/1 numeric column is not necessarily a flag
    return DefectFinding(
        kind="boolean_stored_as_text", column=name, severity="low", count=len(live),
        detail=f"values are yes/no flags stored as {sorted(tokens)} rather than a real boolean",
        evidence=sorted(tokens),
        suggested_op="parse_boolean", suggested_columns=[name],
    )


def _detect_untrimmed(name: str, series: pd.Series) -> DefectFinding | None:
    live = series.dropna()
    strings = live[live.map(lambda v: isinstance(v, str))]
    if strings.empty:
        return None
    dirty = strings[strings.map(lambda v: v != re.sub(r"\s+", " ", v).strip())]
    if dirty.empty:
        return None
    return DefectFinding(
        kind="untrimmed_whitespace", column=name, severity="medium", count=int(len(dirty)),
        detail=f"{len(dirty)} value(s) carry stray leading/trailing or repeated whitespace",
        evidence=[repr(v) for v in dirty.unique()[:_SAMPLE]],
        suggested_op="trim_whitespace", suggested_columns=[name],
    )


def _detect_case_variants(name: str, series: pd.Series) -> DefectFinding | None:
    """Categories that differ only by case/spacing/punctuation — every
    ``groupby`` downstream silently splits them into separate rows."""
    live = series.dropna()
    strings = live[live.map(lambda v: isinstance(v, str))]
    if strings.empty or strings.nunique() > 200:
        return None
    groups: dict[str, set[str]] = {}
    for value in strings.unique():
        groups.setdefault(category_key(value), set()).add(value)
    collisions = {k: v for k, v in groups.items() if len(v) > 1}
    if not collisions:
        return None
    affected = int(strings.isin({v for group in collisions.values() for v in group}).sum())
    evidence = [sorted(group) for group in list(collisions.values())[:3]]
    return DefectFinding(
        kind="inconsistent_categories", column=name, severity="high", count=affected,
        detail=(
            f"{len(collisions)} categor(y/ies) are spelled more than one way "
            f"(e.g. {evidence[0]}) — these split apart in every grouping and total"
        ),
        evidence=evidence,
        suggested_op="harmonize_categories", suggested_columns=[name],
    )


def _detect_missing(name: str, series: pd.Series, *, is_key: bool) -> DefectFinding | None:
    count = int(series.isna().sum())
    if not count:
        return None
    pct = count / max(len(series), 1) * 100
    if is_key:
        return DefectFinding(
            kind="missing_in_key", column=name, severity="blocking", count=count,
            detail=(
                f"{count} value(s) ({pct:.1f}%) are missing in a join key — these rows "
                "cannot be matched to the related table and must be resolved, not filled"
            ),
            suggested_op="flag_missing", suggested_columns=[name], auto_fixable=False,
        )
    if pct > 50:
        return DefectFinding(
            kind="mostly_missing", column=name, severity="high", count=count,
            detail=(
                f"{pct:.1f}% of values are missing — filling this many would manufacture "
                "the column rather than clean it; flag it and decide deliberately"
            ),
            suggested_op="flag_missing", suggested_columns=[name], auto_fixable=False,
        )
    strategy = "median" if pd.api.types.is_numeric_dtype(series) else "mode"
    return DefectFinding(
        kind="missing_values", column=name, severity="medium", count=count,
        detail=f"{count} missing value(s) ({pct:.1f}%)",
        suggested_op="impute", suggested_columns=[name],
        suggested_params={"strategy": strategy},
    )


def _detect_negatives(name: str, series: pd.Series) -> DefectFinding | None:
    if not _NON_NEGATIVE_NAME_RE.search(name):
        return None
    numeric = pd.to_numeric(_as_text(series), errors="coerce")
    negative = numeric < 0
    count = int(negative.sum())
    if not count:
        return None
    return DefectFinding(
        kind="impossible_negative", column=name, severity="high", count=count,
        detail=(
            f"{count} negative value(s) in a quantity/price column — usually a data-entry "
            "or sign error, but it can be a legitimate refund, so flag rather than rewrite"
        ),
        evidence=_evidence(numeric[negative]),
        suggested_op="flag_range_violation", suggested_columns=[name],
        suggested_params={"min": 0.0},
    )


def _detect_future_dates(name: str, series: pd.Series) -> DefectFinding | None:
    if not pd.api.types.is_datetime64_any_dtype(series):
        return None
    live = series.dropna()
    if live.empty:
        return None
    future = live > pd.Timestamp.now()
    count = int(future.sum())
    if not count:
        return None
    return DefectFinding(
        kind="future_date", column=name, severity="medium", count=count,
        detail=f"{count} date(s) are in the future, which a transaction record should not contain",
        evidence=[str(v) for v in live[future].unique()[:_SAMPLE]],
        suggested_op="", suggested_columns=[name], auto_fixable=False,
    )


def _detect_float_integers(name: str, series: pd.Series) -> DefectFinding | None:
    """Whole numbers stored as floats — ids rendered as ``1001.0``, mapped to
    DOUBLE PRECISION in the DDL, and no longer matching the other side of a
    join. The previous pipeline created this on every integer column
    unconditionally, including clean ones."""
    if not pd.api.types.is_float_dtype(series):
        return None
    live = series.dropna()
    if live.empty or not bool(np.isfinite(live).all()) or not bool((live % 1 == 0).all()):
        return None
    if not (_ID_NAME_RE.search(name) or live.max() > 1000):
        return None
    return DefectFinding(
        kind="integer_stored_as_float", column=name, severity="low", count=int(len(live)),
        detail="whole numbers are stored as decimals (1001 shows as 1001.0)",
        evidence=_evidence(live),
        suggested_op="cast_integer", suggested_columns=[name],
    )


def _detect_mixed_types(name: str, series: pd.Series) -> DefectFinding | None:
    live = series.dropna()
    if live.empty or not pd.api.types.is_object_dtype(series):
        return None
    kinds = {type(v).__name__ for v in live}
    simple = {k for k in kinds if k in {"str", "int", "float", "bool", "Timestamp"}}
    if len(simple) < 2:
        return None
    return DefectFinding(
        kind="mixed_types", column=name, severity="medium", count=int(len(live)),
        detail=f"column mixes {sorted(simple)} in one column, so comparisons and sorts are unreliable",
        evidence=_evidence(live),
        suggested_op="", suggested_columns=[name], auto_fixable=False,
    )


def _detect_outliers(name: str, series: pd.Series) -> DefectFinding | None:
    if not pd.api.types.is_numeric_dtype(series) or pd.api.types.is_bool_dtype(series):
        return None
    live = series.dropna()
    if len(live) < 8:
        return None
    q1, q3 = live.quantile(0.25), live.quantile(0.75)
    iqr = q3 - q1
    if not iqr:
        return None
    outliers = live[(live < q1 - 3 * iqr) | (live > q3 + 3 * iqr)]
    if outliers.empty:
        return None
    return DefectFinding(
        kind="extreme_values", column=name, severity="info", count=int(len(outliers)),
        detail=(
            f"{len(outliers)} value(s) sit far outside the usual range — often a legitimate "
            "large order, so these are flagged for review and never rewritten"
        ),
        evidence=_evidence(outliers),
        suggested_op="flag_outliers", suggested_columns=[name],
        suggested_params={"method": "iqr", "factor": 3.0},
    )


# ══════════════════════════════════════════════════════════════════════
# Table-level detectors
# ══════════════════════════════════════════════════════════════════════


def _detect_duplicate_rows(df: pd.DataFrame) -> DefectFinding | None:
    count = int(df.duplicated().sum())
    if not count:
        return None
    return DefectFinding(
        kind="duplicate_rows", severity="high", count=count,
        detail=f"{count} row(s) are exact duplicates of an earlier row",
        suggested_op="drop_duplicate_rows",
    )


def _detect_duplicate_column_names(df: pd.DataFrame) -> DefectFinding | None:
    names = [str(c) for c in df.columns]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if not dupes:
        return None
    return DefectFinding(
        kind="duplicate_column_names", severity="blocking", count=len(dupes),
        detail=f"column name(s) {dupes} appear more than once, so column lookups are ambiguous",
        evidence=dupes, suggested_op="", auto_fixable=False,
    )


def _detect_blank_column_names(df: pd.DataFrame) -> DefectFinding | None:
    blanks = [str(c) for c in df.columns if not str(c).strip() or str(c).lower().startswith("unnamed:")]
    if not blanks:
        return None
    return DefectFinding(
        kind="unnamed_columns", severity="medium", count=len(blanks),
        detail=f"{len(blanks)} column(s) have no real name, usually a spreadsheet export artefact",
        evidence=blanks, suggested_op="", auto_fixable=False,
    )


def _detect_empty_and_constant(df: pd.DataFrame) -> list[DefectFinding]:
    out: list[DefectFinding] = []
    empty = [str(c) for c in df.columns if df[c].isna().all()]
    if empty:
        out.append(DefectFinding(
            kind="empty_column", severity="medium", count=len(empty),
            detail=f"column(s) {empty} contain no data at all",
            evidence=empty, suggested_op="drop_column", suggested_columns=empty,
        ))
    constant = [
        str(c) for c in df.columns
        if c not in empty and len(df) > 1 and df[c].dropna().nunique() == 1
    ]
    if constant:
        out.append(DefectFinding(
            kind="constant_column", severity="low", count=len(constant),
            detail=f"column(s) {constant} hold the same value in every row, carrying no information",
            evidence=constant, suggested_op="drop_column", suggested_columns=constant,
            auto_fixable=False,
        ))
    return out


# ── Duplicate keys vs. the table's grain ───────────────────────────────────
#
# A repeated identifier is only a defect when nothing explains the repeat, and
# the commonest business table in this system explains it perfectly: in a
# line-item table one `order_id` covers several rows *by design*. The order's
# own attributes (date, customer, channel) are identical across those rows while
# the line's attributes (product, quantity, price) differ. That is the table's
# grain, not a broken key.
#
# Reading it as a defect is the single largest source of wrong steps in a
# generated plan — on a 5,639-row sales export it flagged 2,344 rows, 42% of the
# table, as duplicates of each other. A genuinely duplicated *record* looks the
# opposite way round: the copies agree almost everywhere and differ in one
# incidental field, because they are the same entity entered twice.

# Below this share of distinct values a column is the "many" side of a
# relationship — a foreign key. Foreign keys repeat; that is their entire
# purpose, and declared join columns land here too.
_KEY_UNIQUENESS_FLOOR = 0.5
# Constant across at least this share of duplicate groups → a header attribute.
_HEADER_CONSTANT_SHARE = 0.90
# Differs within at least this share of duplicate groups → a line attribute.
_LINE_VARYING_SHARE = 0.50
# A line-item row differs across the whole line, not in one stray field.
_LINE_COLUMN_FLOOR = 2
_LINE_COLUMN_SHARE = 1 / 3


def _canonical(series: pd.Series) -> pd.Series:
    """Values reduced to what they *mean*, for comparison only.

    Grain detection has to work on the table as uploaded, before any cleaning
    step has run. Uncanonicalised, injected dirt hides the structure: one
    "Cairo " among the "Cairo"s makes a header column look like it varies, and
    the table stops looking like a line-item table precisely when it is dirty.
    """
    text = _as_text(series).mask(null_token_mask(series))
    return text.map(lambda v: category_key(v) if isinstance(v, str) else v)


def _candidate_keys(df: pd.DataFrame, declared: set[str]) -> list[str]:
    """Columns that could plausibly be a unique key for *this* table.

    Declared join columns are held to the same uniqueness test as inferred ones.
    An approved relationship says two columns join, not that either side is
    unique — `orders.customer_id` is a join column with 350 distinct values
    across 5,140 rows, and checking it for duplicates reports the foreign key
    working correctly as a defect.
    """
    out = []
    for raw in df.columns:
        name = str(raw)
        if not (_ID_NAME_RE.search(name) or name in declared):
            continue
        live = df[raw].dropna()
        if live.empty:
            continue
        if live.nunique() > max(1, len(live) * _KEY_UNIQUENESS_FLOOR):
            out.append(name)
    return out


def _grain_columns(df: pd.DataFrame, col: str) -> tuple[list[str], list[str]]:
    """``(header_columns, line_columns)`` for the duplicate groups of *col*.

    Header columns hold one value per group; line columns differ inside it.
    """
    others = [str(c) for c in df.columns if str(c) != col]
    if not others:
        return [], []
    frame = pd.DataFrame({c: _canonical(df[c]) for c in others})
    frame["__key__"] = _canonical(df[col])
    frame = frame[frame["__key__"].notna()]
    sizes = frame.groupby("__key__").size()
    repeated = sizes[sizes > 1].index
    if repeated.empty:
        return [], []
    groups = frame[frame["__key__"].isin(repeated)].groupby("__key__")
    header, line = [], []
    for c in others:
        distinct = groups[c].nunique(dropna=False)
        if float((distinct <= 1).mean()) >= _HEADER_CONSTANT_SHARE:
            header.append(c)
        if float((distinct > 1).mean()) >= _LINE_VARYING_SHARE:
            line.append(c)
    return header, line


def _is_line_item_grain(header: list[str], line: list[str], other_count: int) -> bool:
    """True when the repeats are the table's grain rather than duplicated records.

    Both halves are required. Header columns alone would accept two identical
    customer rows; line columns alone would accept a two-row toy table whose
    only other column happens to differ. Together they describe the one-to-many
    shape and nothing else.
    """
    if not header or len(line) < _LINE_COLUMN_FLOOR:
        return False
    return len(line) / max(other_count, 1) >= _LINE_COLUMN_SHARE


def _detect_duplicate_keys(df: pd.DataFrame, key_columns: set[str]) -> list[DefectFinding]:
    out: list[DefectFinding] = []
    declared = {str(c) for c in key_columns}
    for col in sorted(_candidate_keys(df, declared)):
        live = df[col].dropna()
        count = int(live.duplicated().sum())
        if not count:
            continue
        header, line = _grain_columns(df, col)
        others = len([c for c in df.columns if str(c) != col])
        if _is_line_item_grain(header, line, others):
            # Reported, never planned: the reader deserves to know the row grain
            # before they interpret a row count, but there is nothing to fix.
            out.append(DefectFinding(
                kind="line_item_grain", column=str(col), severity="info", count=count,
                detail=(
                    f"{count} row(s) repeat a '{col}' value because this table is at line-item "
                    f"grain: {', '.join(header[:3])} stay the same within one {col} while "
                    f"{', '.join(line[:3])} differ from line to line — the table's grain, "
                    "not duplication"
                ),
                evidence=_evidence(live[live.duplicated()]),
                suggested_op="", auto_fixable=False,
            ))
            continue
        out.append(DefectFinding(
            kind="duplicate_business_key", column=str(col), severity="high", count=count,
            detail=(
                f"{count} row(s) repeat an existing '{col}' value while the rest of the row "
                "differs — these are not exact duplicates and deleting one silently loses data"
            ),
            evidence=_evidence(live[live.duplicated()]),
            suggested_op="flag_duplicate_keys", suggested_columns=[str(col)],
            auto_fixable=False,
        ))
    return out


def _detect_arithmetic_violations(df: pd.DataFrame) -> list[DefectFinding]:
    """Find ``c = a * b`` / ``c = a + b`` identities that hold for most rows,
    and report the rows where they fail. The old profiler detected these
    identities and then passed them to a planner prompt that never mentioned
    them — the violating rows, which are real data errors, went unreported."""
    # Parse through currency/percent formatting first: a "$10.00" price column
    # is numerically a price, and excluding it here would hide exactly the
    # total-vs-quantity-x-price check this exists to run.
    parsed_all = {
        str(c): parse_numeric_series(df[c])
        for c in df.columns
        if not pd.api.types.is_datetime64_any_dtype(df[c])
        and not pd.api.types.is_bool_dtype(df[c])
    }
    # A column with some unparseable values is still a numeric column here —
    # the identity check below only ever compares rows where all three sides
    # parse, so admitting it costs nothing and excluding it hides real errors.
    numeric_cols = [c for c, s in parsed_all.items() if s.notna().mean() >= 0.8]
    if len(numeric_cols) < 3:
        return []

    parsed = {c: parsed_all[c] for c in numeric_cols}
    out: list[DefectFinding] = []
    seen: set[str] = set()

    for operator, symbol in (("*", "×"), ("+", "+")):
        for result in numeric_cols:
            if result in seen:
                continue
            c = parsed[result]
            for left in numeric_cols:
                for right in numeric_cols:
                    if len({left, right, result}) < 3 or left >= right:
                        continue
                    a, b = parsed[left], parsed[right]
                    live = a.notna() & b.notna() & c.notna() & (c.abs() > 0)
                    if int(live.sum()) < 8:
                        continue
                    expected = a[live] * b[live] if operator == "*" else a[live] + b[live]
                    matches = ((expected - c[live]).abs() / c[live].abs() <= 0.01)
                    rate = float(matches.mean())
                    if not 0.75 <= rate < 1.0:
                        continue
                    bad = int((~matches).sum())
                    seen.add(result)
                    out.append(DefectFinding(
                        kind="arithmetic_violation", column=result, severity="high", count=bad,
                        detail=(
                            f"'{result}' equals {left} {symbol} {right} for {rate:.0%} of rows, "
                            f"but {bad} row(s) disagree — those rows contain a real error in one "
                            "of the three columns"
                        ),
                        evidence=[f"{result} = {left} {symbol} {right}"],
                        suggested_op="flag_arithmetic_violation",
                        suggested_params={
                            "left": left, "right": right, "result": result,
                            "operator": operator, "tolerance": 0.01,
                        },
                        auto_fixable=False,
                    ))
                    break
                if result in seen:
                    break
    return out


# ══════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════


def detect_defects(
    df: pd.DataFrame, *, key_columns: set[str] | None = None
) -> list[DefectFinding]:
    """Run every deterministic check and return the findings, most severe first."""
    keys = key_columns or set()
    findings: list[DefectFinding] = []

    # Duplicate column names have to be resolved before anything else can be
    # said about the table: `df[name]` returns a DataFrame rather than a
    # Series, so every per-column check below would be analysing the wrong
    # thing (or raising).
    duplicate_names = _detect_duplicate_column_names(df)
    if duplicate_names:
        return [duplicate_names]

    for finding in (
        _detect_blank_column_names(df),
        _detect_duplicate_rows(df),
    ):
        if finding:
            findings.append(finding)
    findings.extend(_detect_empty_and_constant(df))
    findings.extend(_detect_duplicate_keys(df, keys))
    findings.extend(_detect_arithmetic_violations(df))

    # A column with no data at all has exactly one defect worth reporting.
    # Running the per-column detectors over it as well would pile "100%
    # missing", "mixed types" and so on onto the same empty column.
    empty_cols = {
        c for f in findings if f.kind == "empty_column" for c in f.suggested_columns
    }
    for raw_name in df.columns:
        name = str(raw_name)
        if name in empty_cols:
            continue
        series = df[raw_name]
        if isinstance(series, pd.DataFrame):  # duplicate column names
            continue
        for detector in (
            _detect_placeholders, _detect_untrimmed, _detect_mixed_types,
            _detect_numeric_as_text, _detect_date_as_text, _detect_boolean_as_text,
            _detect_case_variants, _detect_sentinels, _detect_negatives,
            _detect_future_dates, _detect_float_integers, _detect_outliers,
        ):
            finding = detector(name, series)
            if finding:
                findings.append(finding)
        missing = _detect_missing(name, series, is_key=name in keys)
        if missing:
            findings.append(missing)

    order = {"blocking": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    findings.sort(key=lambda f: (order.get(f.severity, 9), f.column, f.kind))
    return findings


def is_clean(findings: list[DefectFinding]) -> bool:
    """True when nothing worth acting on was found.

    ``info`` findings do not make a table dirty. A large order is usually a real
    large order, and an ``order_id`` that repeats down a line-item table is the
    table's grain — treating either as a defect forces a cleaning plan onto data
    that needs none. Both are still reported, so the reader sees them; neither
    becomes a step, which is why :func:`plan_from_defects` skips the same
    findings this function ignores.
    """
    return not any(f.severity != "info" for f in findings)


# Operators whose parameters are fully determined by the measurement that
# produced them. There is exactly one right way to read "2024-03-01" as a date
# or to strip a trailing space, so a model reviewing these can only agree,
# mangle a parameter, or drop a step that was already correct.
JUDGEMENT_FREE_OPS = frozenset({
    "standardize_null_placeholders",
    "trim_whitespace",
    "parse_numeric",
    "parse_datetime",
    "parse_boolean",
    "cast_integer",
})


def needs_review(findings: list[DefectFinding]) -> bool:
    """True when a model's judgement could actually improve the plan.

    False for a table whose only findings are lossless type normalisation — a
    CSV has no types, so a perfectly clean export still arrives with its dates
    and amounts as text. Those steps are not a judgement call, and sending them
    to a planner spends an LLM call and a slice of a per-minute token budget for
    the chance of coming back with *fewer* correct steps than it was given.

    Findings with no suggested operator (mixed types, future dates) do count as
    needing review: nothing can be applied automatically, which is exactly when
    an opinion is worth having.
    """
    return any(
        f.severity != "info" and f.suggested_op not in JUDGEMENT_FREE_OPS
        for f in findings
    )


def plan_from_defects(findings: list[DefectFinding]) -> list[CleaningStep]:
    """Turn findings into a typed, correctly-ordered baseline cleaning plan.

    Only ``auto_fixable`` findings whose suggested operator actually changes
    data become steps; the rest are reported to the user for a decision. Every
    step here is deterministic, so this plan is what runs when the LLM planner
    is unavailable or returns something unusable.

    ``info`` findings never become steps. Acting on them broke the one invariant
    a reader relies on — a table reported as clean would still come back with a
    plan, because every numeric column has some large values and each one added
    an ``<column>_is_outlier`` column to data nobody had said was wrong. What
    ``is_clean`` ignores, this ignores.
    """
    steps: list[CleaningStep] = []
    for index, finding in enumerate(findings):
        if not finding.suggested_op or finding.severity == "info":
            continue
        # Non-auto-fixable findings still get a *flagging* step (which only ever
        # adds an indicator column); anything that rewrites data needs consent.
        if not finding.auto_fixable and not finding.suggested_op.startswith("flag_"):
            continue
        steps.append(CleaningStep(
            id=f"d{index + 1}",
            op=finding.suggested_op,
            columns=finding.suggested_columns or ([finding.column] if finding.column else []),
            params=dict(finding.suggested_params),
            description=describe_step(finding),
            source="detector",
        ))

    # A column that is about to be dropped needs no other work done to it.
    dropped = {c for s in steps if s.op == "drop_column" for c in s.columns}
    if dropped:
        steps = [
            s for s in steps
            if s.op == "drop_column" or not s.columns or not set(s.columns) <= dropped
        ]

    return order_steps(steps)


def describe_step(finding: DefectFinding) -> str:
    """Business-language sentence for a plan step, derived from the finding."""
    where = f"'{finding.column}'" if finding.column else "the table"
    templates = {
        "standardize_null_placeholders": f"Treat the {finding.count} placeholder value(s) in {where} (UNKNOWN, N/A, …) as genuinely missing.",
        "replace_sentinels": f"Treat the {finding.count} sentinel value(s) in {where} as missing rather than real measurements.",
        "trim_whitespace": f"Remove stray spacing from {finding.count} value(s) in {where}.",
        "parse_numeric": f"Convert {where} from text to real numbers so it can be summed and averaged.",
        "parse_datetime": f"Convert {where} from text to real dates so it can be used on a timeline.",
        "parse_boolean": f"Convert {where} to a real yes/no column.",
        "cast_integer": f"Store {where} as whole numbers instead of decimals.",
        "harmonize_categories": f"Merge the differently-spelled versions of the same category in {where}.",
        "drop_duplicate_rows": f"Remove {finding.count} row(s) that are exact duplicates.",
        "drop_column": f"Remove {', '.join(finding.suggested_columns)}, which contain no usable information.",
        "flag_duplicate_keys": f"Mark the {finding.count} row(s) that repeat a {where} value, without deleting them.",
        "flag_range_violation": f"Mark the {finding.count} impossible value(s) in {where} for review.",
        "flag_arithmetic_violation": f"Mark the {finding.count} row(s) where {finding.evidence[0] if finding.evidence else 'the calculation'} does not hold.",
        "flag_outliers": f"Mark the {finding.count} unusually large value(s) in {where} for review, without changing them.",
        "flag_missing": f"Mark which rows are missing {where} so later analysis can see it.",
        "impute": f"Fill the {finding.count} missing value(s) in {where} using the column's {finding.suggested_params.get('strategy', 'median')}.",
    }
    return templates.get(finding.suggested_op, finding.detail)
