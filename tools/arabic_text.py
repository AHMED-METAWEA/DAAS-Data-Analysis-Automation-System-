"""
Arabic-aware text utilities for the cleaning pipeline.

Everything here is deterministic and dependency-free. The goal is not full
Arabic NLP but the transformations that make Arabic business data *analysable*:

  - detection      : is a value / column Arabic text?
  - normalization  : unify the orthographic variants that split identical
                     categories apart (diacritics, tatweel, alef/ya/ta-marbuta
                     variants, Arabic-Indic digits, punctuation, whitespace)
  - placeholders   : Arabic equivalents of "unknown"/"n/a" so the profiler
                     counts them as missing
  - stopwords      : a compact stopword list for free-text analysis

Normalization is intentionally *conservative*: it never changes the meaning of
a value, only its representation, so it is safe to apply in-place to
categorical/text columns before profiling and analysis.
"""

from __future__ import annotations

import re

import pandas as pd

# ── Detection ──────────────────────────────────────────────────────────────

# Arabic blocks: basic (0600-06FF), supplement (0750-077F), extended-A
# (08A0-08FF), presentation forms A/B (FB50-FDFF, FE70-FEFF).
_ARABIC_RE = re.compile("[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]")

# Tashkeel (diacritics) + Quranic annotation marks.
_DIACRITICS_RE = re.compile("[ؐ-ًؚ-ٰٟۖ-ۭ]")

_TATWEEL = "ـ"

# Arabic-Indic (٠-٩) and Eastern Arabic-Indic (۰-۹) digits → Western.
_DIGIT_MAP = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")

# Arabic punctuation → ASCII equivalents (decimal/thousands separators matter
# for numeric coercion downstream).
_PUNCT_MAP = str.maketrans({"،": ",", "؛": ";", "؟": "?", "٫": ".", "٬": ","})

# Common Arabic placeholder strings meaning "missing / unknown".
ARABIC_PLACEHOLDERS: set[str] = {
    "غير معروف",     # unknown
    "غير محدد",      # unspecified
    "غير متوفر",     # not available
    "غير متاح",      # not available
    "لا يوجد",       # none / does not exist
    "لايوجد",
    "مجهول",         # unknown
    "خطأ",           # error
    "بدون",          # without
    "فارغ",          # empty
}

# Compact modern-standard-Arabic stopword list (function words only).
ARABIC_STOPWORDS: set[str] = {
    "في", "من", "الى", "إلى", "على", "عن", "مع", "هذا", "هذه", "ذلك",
    "تلك", "التي", "الذي", "الذين", "ما", "لا", "لم", "لن", "ان", "أن",
    "إن", "كان", "كانت", "يكون", "هو", "هي", "هم", "نحن", "انت", "أنت",
    "او", "أو", "و", "ثم", "قد", "كل", "بعض", "غير", "بين", "بعد",
    "قبل", "عند", "حتى", "اذا", "إذا", "كما", "لكن", "الا", "إلا",
}


def is_arabic_text(value: object) -> bool:
    """True when the value contains at least one Arabic letter."""
    return isinstance(value, str) and bool(_ARABIC_RE.search(value))


def arabic_ratio(series: pd.Series, sample_size: int = 500) -> float:
    """Fraction of non-null values in ``series`` that contain Arabic text.

    Samples large columns for speed; returns 0.0 for non-text columns.
    """
    if not (series.dtype == object or pd.api.types.is_string_dtype(series)):
        return 0.0
    values = series.dropna()
    if values.empty:
        return 0.0
    if len(values) > sample_size:
        values = values.sample(sample_size, random_state=0)
    hits = sum(1 for v in values if is_arabic_text(v))
    return round(hits / len(values), 3)


# ── Normalization ──────────────────────────────────────────────────────────


def normalize_arabic(text: str) -> str:
    """Normalize one Arabic string. Safe (meaning-preserving) operations only:

    - strip diacritics and tatweel (كتـــاب → كتاب, مُحَمَّد → محمد)
    - unify alef variants (أ إ آ ٱ → ا) and alef-maqsura (ى → ي)
    - unify ta-marbuta spelled as ha at word end is NOT applied (changes words);
      only the unambiguous variants above are folded
    - convert Arabic-Indic digits to Western digits (٢٠٢٤ → 2024)
    - map Arabic punctuation to ASCII (٫ → . for decimals)
    - collapse whitespace
    """
    if not isinstance(text, str):
        return text
    out = _DIACRITICS_RE.sub("", text)
    out = out.replace(_TATWEEL, "")
    out = re.sub("[أإآٱ]", "ا", out)
    out = out.replace("ى", "ي")
    out = out.translate(_DIGIT_MAP).translate(_PUNCT_MAP)
    out = re.sub(r"\s+", " ", out).strip()
    return out


def remove_stopwords(text: str) -> str:
    """Remove Arabic stopwords (for keyword/frequency analysis of free text)."""
    if not isinstance(text, str):
        return text
    tokens = text.split()
    kept = [t for t in tokens if t not in ARABIC_STOPWORDS]
    return " ".join(kept) if kept else text


def normalize_series(series: pd.Series) -> pd.Series:
    """Vectorised normalization of a text series (non-strings pass through)."""
    return series.map(lambda v: normalize_arabic(v) if isinstance(v, str) else v)


def normalize_arabic_dataframe(
    df: pd.DataFrame, min_ratio: float = 0.05
) -> tuple[pd.DataFrame, dict[str, float]]:
    """Normalize every text column that contains Arabic content.

    Returns ``(new_df, {column: arabic_ratio})`` for the columns touched.
    The original DataFrame is not modified.
    """
    touched: dict[str, float] = {}
    out = df.copy()
    for col in out.columns:
        s = out[col]
        if not (s.dtype == object or pd.api.types.is_string_dtype(s)):
            continue
        ratio = arabic_ratio(s)
        if ratio >= min_ratio:
            normalized = normalize_series(s)
            if not normalized.equals(s):
                out[col] = normalized
            touched[col] = ratio
    return out, touched
