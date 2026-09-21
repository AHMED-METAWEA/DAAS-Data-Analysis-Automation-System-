"""Guardrail tests for the insight report templates.

These lock in the anti-hallucination contract of the prompts themselves, so a
future edit can't silently reintroduce a fabricated-confidence score or an
impossible human-in-the-loop instruction into a one-shot generation path.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts" / "insights"
_TEMPLATES = ["decision_brief", "non_technical", "executive", "detailed"]


def _load(stem: str) -> str:
    return (_PROMPT_DIR / f"{stem}.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("stem", _TEMPLATES)
def test_template_exists_and_nonempty(stem: str) -> None:
    assert _load(stem).strip()


@pytest.mark.parametrize("stem", _TEMPLATES)
def test_template_forbids_fabricated_numeric_confidence(stem: str) -> None:
    """No template may instruct the model to emit a 0–1 confidence NUMBER — the
    grounding checker skips sub-1000 plain numbers, so such a value would be an
    unverifiable fabrication that sails past verification."""
    text = _load(stem).lower()
    # A directive to produce a numeric confidence like "confidence level (0 to 1)"
    # or "confidence 0.7". Word-based certainty ("fairly confident") is fine.
    bad_patterns = [
        r"confidence\s+level\s*\(?\s*0\s*(?:to|-|–)\s*1",
        r"confidence\s*score.*0\s*(?:to|-|–)\s*1",
        r"confidence\s*[:=]\s*0\.\d",
    ]
    for pat in bad_patterns:
        assert not re.search(pat, text), f"{stem}: forbids numeric confidence ({pat})"


def test_detailed_template_has_no_dead_human_in_the_loop() -> None:
    """The detailed template runs through a single-shot `complete()` call, so an
    instruction to STOP and ask clarifying questions can never be honoured — it
    would only corrupt the output. It must be gone."""
    text = _load("detailed").lower()
    assert "stop and ask" not in text
    assert "must stop" not in text


@pytest.mark.parametrize("stem", _TEMPLATES)
def test_template_carries_grounding_language(stem: str) -> None:
    """Every template must explicitly forbid inventing numbers."""
    text = _load(stem).lower()
    assert any(
        phrase in text
        for phrase in ("never invent", "do not invent", "don't invent",
                       "only numbers", "not available in the data",
                       "base conclusions strictly", "honest about uncertainty",
                       "backed by evidence", "connects to evidence")
    ), f"{stem}: missing explicit grounding instruction"
