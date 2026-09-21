"""LangGraph StateGraph for the cleaning stage.

    coder → executor → validator ─┬─ END
                ↑                 │
                └── retry ────────┘

Two changes from the original shape:

* The **coder is a no-op** for a plan made entirely of typed operators, which
  is the normal case. Cleaning is deterministic; the LLM is only invoked for
  free-text steps.

* **Validation failure now feeds the repair loop.** Previously only an
  execution *crash* triggered a retry: code that ran successfully but corrupted
  the data ended the graph immediately and the table was marked failed with no
  second attempt. The validator now returns structured violations, and those
  are handed back to the coder as repair feedback naming the exact rule and
  column that broke.

Retrying only helps when generated code was involved. A plan of typed
operators that fails invariants indicates an operator bug, not a bad LLM
sample, so it goes straight to END rather than burning retries on a rerun that
would be byte-for-byte identical.
"""

from __future__ import annotations

from agents.cleaning.coder import coder_node
from agents.cleaning.executor import executor_node, parse_plan_steps
from agents.cleaning.validator import validator_node
from core.state import GraphState
from langgraph.graph import END, StateGraph

MAX_RETRIES = 2


def _plan_has_free_text(state: GraphState) -> bool:
    return any(not s.is_typed for s in parse_plan_steps(state.get("cleaning_plan")))


def _route_after_executor(state: GraphState) -> str:
    result = state.get("execution_result", {}) or {}
    retry_count = state.get("retry_count", 0)
    if result.get("success", False):
        return "validator"
    if retry_count < MAX_RETRIES and _plan_has_free_text(state):
        return "coder"
    return "validator"


def _route_after_validator(state: GraphState) -> str:
    report = state.get("validation_report", {}) or {}
    if report.get("passed"):
        return END
    retry_count = state.get("retry_count", 0)
    if retry_count < MAX_RETRIES and _plan_has_free_text(state) and report.get("repair_feedback"):
        return "coder"
    return END


def _prepare_retry(state: GraphState) -> dict:
    """Carry the validator's violations into the coder's repair prompt."""
    report = state.get("validation_report", {}) or {}
    return {
        "retry_count": state.get("retry_count", 0) + 1,
        "last_error": report.get("repair_feedback", "") or state.get("last_error", ""),
    }


def build_cleaning_graph():
    """Construct and compile the cleaning StateGraph."""
    graph = StateGraph(GraphState)

    graph.add_node("coder", coder_node)
    graph.add_node("executor", executor_node)
    graph.add_node("validator", validator_node)
    graph.add_node("prepare_retry", _prepare_retry)

    graph.set_entry_point("coder")
    graph.add_edge("coder", "executor")

    graph.add_conditional_edges(
        "executor", _route_after_executor, {"coder": "coder", "validator": "validator"},
    )
    graph.add_conditional_edges(
        "validator", _route_after_validator, {"coder": "prepare_retry", END: END},
    )
    graph.add_edge("prepare_retry", "coder")

    return graph.compile()
