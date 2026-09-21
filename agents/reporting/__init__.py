"""Shared reporting layer: grounding checks, HTML/PDF export, and a common
Streamlit report presentation used by every narrative-producing agent."""

from .export import build_html_report, markdown_to_html
from .grounding import GroundingResult, NumericClaim, check_grounding

__all__ = [
    "GroundingResult",
    "NumericClaim",
    "check_grounding",
    "build_html_report",
    "markdown_to_html",
]
