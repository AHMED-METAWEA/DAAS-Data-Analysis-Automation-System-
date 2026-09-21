from __future__ import annotations

import pandas as pd

from tools.arabic_text import (
    ARABIC_STOPWORDS,
    arabic_ratio,
    is_arabic_text,
    normalize_arabic,
    normalize_arabic_dataframe,
    remove_stopwords,
)
from tools.profiler_tools import (
    PLACEHOLDER_VALUES,
    build_dataset_profile,
    replace_placeholders_with_nan,
)

# ── Detection ────────────────────────────────────────────────────────────────


def test_is_arabic_text() -> None:
    assert is_arabic_text("القاهرة")
    assert is_arabic_text("Cairo القاهرة mixed")
    assert not is_arabic_text("Cairo")
    assert not is_arabic_text(123)
    assert not is_arabic_text(None)


def test_arabic_ratio() -> None:
    s = pd.Series(["القاهرة", "جدة", "Dubai", None])
    assert arabic_ratio(s) == round(2 / 3, 3)
    assert arabic_ratio(pd.Series([1, 2, 3])) == 0.0


# ── Normalization ────────────────────────────────────────────────────────────


def test_normalize_diacritics_and_tatweel() -> None:
    assert normalize_arabic("مُحَمَّد") == "محمد"
    assert normalize_arabic("كتـــاب") == "كتاب"


def test_normalize_alef_and_ya_variants() -> None:
    assert normalize_arabic("أحمد") == "احمد"
    assert normalize_arabic("إسلام") == "اسلام"
    assert normalize_arabic("آمال") == "امال"
    assert normalize_arabic("مصطفى") == "مصطفي"


def test_normalize_arabic_digits_and_decimal() -> None:
    assert normalize_arabic("٢٠٢٤") == "2024"
    assert normalize_arabic("١٢٫٥") == "12.5"
    assert normalize_arabic("۱۲۳") == "123"


def test_normalize_collapses_whitespace() -> None:
    assert normalize_arabic("  القاهرة   الكبرى ") == "القاهرة الكبري"


def test_remove_stopwords() -> None:
    assert "في" in ARABIC_STOPWORDS
    assert remove_stopwords("المبيعات في القاهرة") == "المبيعات القاهرة"
    # All-stopword text returns the original rather than an empty string.
    assert remove_stopwords("في من") == "في من"


# ── DataFrame integration ────────────────────────────────────────────────────


def test_normalize_arabic_dataframe_touches_only_arabic_columns() -> None:
    df = pd.DataFrame({
        "city": ["أسوان", "القاهرة", "مُحَمَّد آباد"],
        "name": ["Alice", "Bob", "Carol"],
        "amount": [1.0, 2.0, 3.0],
    })
    out, touched = normalize_arabic_dataframe(df)
    assert "city" in touched and "name" not in touched
    assert out["city"].tolist() == ["اسوان", "القاهرة", "محمد اباد"]
    # Original untouched (no mutation).
    assert df["city"].iloc[0] == "أسوان"


def test_arabic_placeholders_become_nan() -> None:
    df = pd.DataFrame({
        "status": ["نشط", "غير معروف", "لا يوجد", "active"],
    })
    out = replace_placeholders_with_nan(df)
    assert out["status"].isna().sum() == 2


def test_arabic_placeholder_normalized_form_in_set() -> None:
    # "خطأ" normalizes to "خطا" — both must count as placeholders.
    assert "خطأ" in PLACEHOLDER_VALUES
    assert "خطا" in PLACEHOLDER_VALUES


def test_profile_reports_arabic_ratio() -> None:
    df = pd.DataFrame({
        "city": ["القاهرة", "جدة", "دبي", "الرياض"],
        "amount": [1, 2, 3, 4],
    })
    profile = build_dataset_profile(df)
    by_name = {c.name: c for c in profile.columns}
    assert by_name["city"].arabic_ratio == 1.0
    assert by_name["amount"].arabic_ratio == 0.0


def test_arabic_digit_column_becomes_numeric_after_normalization() -> None:
    df = pd.DataFrame({"السنة": ["٢٠٢٢", "٢٠٢٣", "٢٠٢٤"]})
    out, touched = normalize_arabic_dataframe(df)
    assert "السنة" in touched
    coerced = pd.to_numeric(out["السنة"], errors="coerce")
    assert coerced.notna().all()
