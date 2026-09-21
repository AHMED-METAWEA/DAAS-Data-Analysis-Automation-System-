from __future__ import annotations

from agents import constants
from agents.constants import AVAILABLE_MODELS, DEFAULT_INSIGHTS_MODEL

DECOMMISSIONED = {
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "mixtral-8x7b-32768",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "qwen/qwen3-32b",
}


def test_default_insights_model_is_gpt_oss_120b() -> None:
    assert DEFAULT_INSIGHTS_MODEL == "openai/gpt-oss-120b"
    assert DEFAULT_INSIGHTS_MODEL in AVAILABLE_MODELS


def test_every_agent_default_is_available() -> None:
    defaults = {
        name: value
        for name, value in vars(constants).items()
        if name.startswith("DEFAULT_") and name.endswith("_MODEL")
    }
    assert len(defaults) == 11
    for name, value in defaults.items():
        assert value in AVAILABLE_MODELS, f"{name}={value} is not a selectable model"


def test_no_decommissioned_models_are_offered() -> None:
    assert DECOMMISSIONED.isdisjoint(AVAILABLE_MODELS)
