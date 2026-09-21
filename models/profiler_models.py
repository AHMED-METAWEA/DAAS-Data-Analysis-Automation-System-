"""
Pydantic v2 models for the Data Profiler.

These models define the contract between the Profiler node and all
downstream agents in the cleaning pipeline. Every LLM node receives
DatasetProfile.model_dump_json() as its structured input.
"""

from pydantic import BaseModel, Field


class ColumnProfile(BaseModel):
    """Metadata profile for a single DataFrame column."""

    name: str = Field(description="Column name")
    dtype: str = Field(description="Pandas dtype as string")
    inferred_type: str = Field(
        description="Inferred semantic type: numeric, categorical, text, date, id, boolean, or unknown"
    )
    missing_count: int = Field(description="Number of missing/null/placeholder values")
    missing_pct: float = Field(description="Percentage of missing values (0-100)")
    placeholder_count: int = Field(
        default=0,
        description="Count of known placeholder strings treated as missing (UNKNOWN, ERROR, N/A, etc.)",
    )
    unique_count: int = Field(description="Number of unique non-null values")
    sample_values: list = Field(description="Up to 5 sample values from the column")
    type_mismatch_flag: bool = Field(
        description="True if the column contains values inconsistent with its inferred dtype"
    )
    outlier_flag: bool = Field(
        description="True if the column contains statistical outliers (IQR method)"
    )
    numeric_stats: dict | None = Field(
        default=None,
        description="Numeric statistics (min, max, mean, std, skewness) or None if non-numeric",
    )
    arabic_ratio: float = Field(
        default=0.0,
        description="Fraction (0-1) of values containing Arabic text; >0 means the "
                    "column was Arabic-normalized (diacritics/tatweel stripped, "
                    "alef variants unified, Arabic-Indic digits converted)",
    )
    is_relationship_key: bool = Field(
        default=False,
        description="True if this column participates in an approved cross-table "
                    "relationship (see relationships/review.py) — the cleaning planner "
                    "should prefer imputation/flagging over dropping rows or changing "
                    "representation for these columns, since another table's join "
                    "depends on the values staying matchable.",
    )


class DatasetProfile(BaseModel):
    """Full metadata profile for an entire DataFrame."""

    total_rows: int = Field(description="Total number of rows")
    total_cols: int = Field(description="Total number of columns")
    duplicate_rows_count: int = Field(description="Number of fully duplicated rows")
    columns: list[ColumnProfile] = Field(description="Per-column profiles")
    relationships: list = Field(
        default=[],
        description="Detected column relationships (e.g., arithmetic, hierarchical)",
    )
    defects: list = Field(
        default_factory=list,
        description="Deterministic data-quality findings from tools.defect_detection — "
                    "what is actually wrong with this table, with counts and evidence. "
                    "This, not the LLM's reading of the column stats, is what decides "
                    "whether the table needs cleaning and what the baseline plan is.",
    )
