"""Groq model catalogue and per-agent defaults.

Groq shut off ``llama-3.3-70b-versatile`` and ``llama-3.1-8b-instant`` on
2026-08-16; ``mixtral-8x7b-32768``, ``meta-llama/llama-4-scout-17b-16e-instruct``
and ``qwen/qwen3-32b`` are gone too. The list below is what the ``/models``
endpoint actually returns for this account.
"""

AVAILABLE_MODELS = [
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    # Stronger at reasoning, but ~4x/5x the price, a 16K output ceiling, and it
    # emits `<think>...</think>` inline in `content` instead of a separate
    # `reasoning` field. Agents here parse JSON out of raw text, so this model
    # needs `reasoning_effort="none"` or it will break them.
    "qwen/qwen3.6-27b",
]

DEFAULT_PLANNER_MODEL = "openai/gpt-oss-120b"
DEFAULT_CODER_MODEL = "openai/gpt-oss-120b"
DEFAULT_VIZ_MODEL = "openai/gpt-oss-120b"
DEFAULT_DASHBOARD_MODEL = "openai/gpt-oss-120b"
DEFAULT_INSIGHTS_MODEL = "openai/gpt-oss-120b"
DEFAULT_FORECAST_MODEL = "openai/gpt-oss-120b"
DEFAULT_MARKETING_MODEL = "openai/gpt-oss-120b"
DEFAULT_CHURN_MODEL = "openai/gpt-oss-120b"
DEFAULT_SCHEMA_DISCOVERY_MODEL = "openai/gpt-oss-120b"
DEFAULT_COPILOT_MODEL = "openai/gpt-oss-120b"
DEFAULT_EXPLAIN_CHART_MODEL = "openai/gpt-oss-120b"

DEFAULT_HORIZONS = [7, 30, 90]
