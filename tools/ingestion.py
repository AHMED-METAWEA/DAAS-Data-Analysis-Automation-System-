"""
Multi-format, multi-file ingestion layer.

Two responsibilities, both deterministic (no LLM):

1. ``load_tabular_file`` — read a single uploaded file of any supported
   format (CSV / TSV / TXT, Excel, JSON / JSON-lines, Parquet) into a
   DataFrame, with encoding detection (UTF-8, UTF-8-BOM, CP1256 for Arabic,
   Latin-1 fallback) and delimiter sniffing for text formats.

2. ``plan_combination`` / ``combine_datasets`` — given several loaded
   datasets, decide how they relate and produce ONE primary DataFrame for
   the downstream pipeline:

     - identical column sets            → vertical append (+ ``_source_file``)
     - shared key joining a dimension
       table onto a fact table          → left join
     - unrelated                        → keep the chosen primary dataset

   The plan is returned as data so the UI can show it for human review
   before it is executed. Downstream agents always receive a single
   DataFrame, so nothing after ingestion needs to change.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

import pandas as pd

SUPPORTED_EXTENSIONS = {
    ".csv", ".tsv", ".txt", ".xlsx", ".xls", ".json", ".jsonl", ".parquet",
}

# Encodings tried in order for text formats. cp1256 covers legacy Arabic
# exports; latin-1 never fails and acts as the last resort.
_TEXT_ENCODINGS = ("utf-8-sig", "utf-8", "cp1256", "latin-1")


# ── Single-file loading ────────────────────────────────────────────────────


def _decode_bytes(raw: bytes) -> str:
    for enc in _TEXT_ENCODINGS:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def _sniff_delimiter(text: str) -> str:
    sample = text[:65536]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def load_tabular_file(file: BinaryIO | str | Path, name: str = "") -> pd.DataFrame:
    """Load one file of any supported format into a DataFrame.

    ``file`` may be a path or a binary file-like object (e.g. a Streamlit
    ``UploadedFile``). ``name`` is used for extension detection when the
    object has no ``name`` attribute.
    """
    fname = name or getattr(file, "name", str(file))
    ext = Path(fname).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Unsupported file type '{ext}' for {fname}. "
            f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )

    if isinstance(file, (str, Path)):
        raw = Path(file).read_bytes()
    else:
        file.seek(0)
        raw = file.read()

    if ext == ".parquet":
        return pd.read_parquet(io.BytesIO(raw))
    if ext in (".xlsx", ".xls"):
        return pd.read_excel(io.BytesIO(raw))
    if ext in (".json", ".jsonl"):
        return _load_json(raw)

    text = _decode_bytes(raw)
    sep = "\t" if ext == ".tsv" else _sniff_delimiter(text)
    df = pd.read_csv(io.StringIO(text), sep=sep)
    if df.shape[1] == 1 and sep != ",":
        # Sniffer guessed wrong on a single-column result — retry with comma.
        df = pd.read_csv(io.StringIO(text))
    return df


def _load_json(raw: bytes) -> pd.DataFrame:
    text = _decode_bytes(raw).strip()
    # JSON-lines: one object per line.
    if text.startswith("{") and "\n" in text:
        try:
            return pd.read_json(io.StringIO(text), lines=True)
        except ValueError:
            pass
    data = json.loads(text)
    if isinstance(data, dict):
        # Common API shape: {"data": [...]} / {"records": [...]}
        for key in ("data", "records", "rows", "items", "results"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
    return pd.json_normalize(data)


# ── Multi-file combination ─────────────────────────────────────────────────


@dataclass
class Dataset:
    """One loaded file, ready for combination."""

    name: str
    df: pd.DataFrame


@dataclass
class CombinationPlan:
    """A reviewable description of how the datasets will be combined.

    ``strategy`` is one of:
      - ``single``   — only one file, use as-is
      - ``append``   — same schema, stack rows
      - ``join``     — fact table left-joined with dimension tables on keys
      - ``separate`` — no reliable relationship; use the primary only
    """

    strategy: str
    primary: str                       # name of the fact/primary dataset
    description: str                   # human-readable summary for HITL review
    joins: list[dict] = field(default_factory=list)   # [{dimension, key}]
    warnings: list[str] = field(default_factory=list)


def _normalized_columns(df: pd.DataFrame) -> set[str]:
    return {str(c).strip().lower() for c in df.columns}


def _key_candidates(fact: pd.DataFrame, dim: pd.DataFrame) -> str | None:
    """Find a column shared by both frames that uniquely identifies ``dim`` rows.

    Classic star-schema test: the key is (near-)unique in the dimension table
    and its values actually appear in the fact table.
    """
    fact_cols = {str(c).strip().lower(): c for c in fact.columns}
    best: tuple[float, str] | None = None
    for col in dim.columns:
        low = str(col).strip().lower()
        if low not in fact_cols:
            continue
        dim_vals = dim[col].dropna()
        if dim_vals.empty:
            continue
        uniqueness = dim_vals.nunique() / len(dim_vals)
        if uniqueness < 0.95:
            continue
        fact_vals = fact[fact_cols[low]].dropna()
        if fact_vals.empty:
            continue
        coverage = fact_vals.isin(set(dim_vals)).mean()
        if coverage < 0.5:
            continue
        score = uniqueness + coverage
        if best is None or score > best[0]:
            best = (score, col)
    return best[1] if best else None


def plan_combination(
    datasets: list[Dataset], primary: str | None = None
) -> CombinationPlan:
    """Decide how the uploaded files relate. Pure analysis — nothing mutated.

    ``primary`` optionally forces which file is treated as the fact/primary
    table (used when the human reviewer overrides the automatic choice).
    """
    if not datasets:
        raise ValueError("No datasets provided.")
    if len(datasets) == 1:
        d = datasets[0]
        return CombinationPlan(
            strategy="single", primary=d.name,
            description=f"Single file '{d.name}' "
                        f"({len(d.df)} rows × {len(d.df.columns)} columns).",
        )

    # Same schema across all files → append.
    first_cols = _normalized_columns(datasets[0].df)
    if all(_normalized_columns(d.df) == first_cols for d in datasets[1:]):
        total = sum(len(d.df) for d in datasets)
        return CombinationPlan(
            strategy="append", primary=datasets[0].name,
            description=(
                f"All {len(datasets)} files share the same columns — rows will be "
                f"stacked into one table ({total} rows). A '_source_file' column "
                "records where each row came from."
            ),
        )

    # Otherwise: largest table (or the user's choice) is the fact table;
    # try to join the rest.
    ordered = sorted(datasets, key=lambda d: len(d.df), reverse=True)
    if primary:
        ordered.sort(key=lambda d: d.name != primary)
    fact, others = ordered[0], ordered[1:]
    joins: list[dict] = []
    warnings: list[str] = []
    for dim in others:
        key = _key_candidates(fact.df, dim.df)
        if key:
            joins.append({"dimension": dim.name, "key": key})
        else:
            warnings.append(
                f"'{dim.name}' shares no reliable key with '{fact.name}' and "
                "will be ignored unless you choose it as the primary file."
            )

    if joins:
        join_desc = ", ".join(f"'{j['dimension']}' on '{j['key']}'" for j in joins)
        return CombinationPlan(
            strategy="join", primary=fact.name, joins=joins, warnings=warnings,
            description=(
                f"'{fact.name}' ({len(fact.df)} rows) is the main transaction table; "
                f"joining {join_desc} to enrich it."
            ),
        )

    return CombinationPlan(
        strategy="separate", primary=fact.name, warnings=warnings,
        description=(
            "No shared schema or join key detected. The largest file "
            f"'{fact.name}' will be used; the others are ignored."
        ),
    )


def combine_datasets(
    datasets: list[Dataset], plan: CombinationPlan
) -> tuple[pd.DataFrame, list[str]]:
    """Execute a combination plan. Returns (combined_df, log_lines)."""
    by_name = {d.name: d for d in datasets}
    log: list[str] = []

    if plan.strategy in ("single", "separate"):
        df = by_name[plan.primary].df.copy()
        log.append(f"Using '{plan.primary}' ({len(df)} rows).")
        return df, log

    if plan.strategy == "append":
        frames = []
        # Align on the first file's column casing so case-only mismatches append.
        canon = {str(c).strip().lower(): c for c in datasets[0].df.columns}
        for d in datasets:
            f = d.df.copy()
            f.columns = [canon.get(str(c).strip().lower(), c) for c in f.columns]
            f["_source_file"] = d.name
            frames.append(f)
        df = pd.concat(frames, ignore_index=True)
        log.append(
            f"Appended {len(datasets)} files → {len(df)} rows "
            f"({', '.join(d.name for d in datasets)})."
        )
        return df, log

    if plan.strategy == "join":
        df = by_name[plan.primary].df.copy()
        for j in plan.joins:
            dim = by_name[j["dimension"]]
            key = j["key"]
            # Resolve the fact-side column that matches the dimension key.
            fact_key = next(
                (c for c in df.columns
                 if str(c).strip().lower() == str(key).strip().lower()),
                key,
            )
            dim_df = dim.df.drop_duplicates(subset=[key])
            overlap = [
                c for c in dim_df.columns
                if c != key and str(c).strip().lower()
                in {str(x).strip().lower() for x in df.columns}
            ]
            if overlap:
                dim_df = dim_df.drop(columns=overlap)
                log.append(
                    f"Dropped overlapping columns {overlap} from '{dim.name}' before join."
                )
            before_cols = len(df.columns)
            df = df.merge(dim_df, left_on=fact_key, right_on=key, how="left")
            if key in df.columns and key != fact_key:
                df = df.drop(columns=[key])
            log.append(
                f"Joined '{dim.name}' on '{key}' "
                f"(+{len(df.columns) - before_cols} columns)."
            )
        return df, log

    raise ValueError(f"Unknown combination strategy: {plan.strategy}")


def ingest_files(files: list, names: list[str] | None = None) -> tuple[
    pd.DataFrame, CombinationPlan, list[str]
]:
    """Convenience wrapper: load → plan → combine in one call."""
    datasets = []
    for i, f in enumerate(files):
        name = (names[i] if names else "") or getattr(f, "name", f"file_{i}")
        datasets.append(Dataset(name=name, df=load_tabular_file(f, name)))
    plan = plan_combination(datasets)
    df, log = combine_datasets(datasets, plan)
    return df, plan, log
