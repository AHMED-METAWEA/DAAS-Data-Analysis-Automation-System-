---
name: professionalization-roadmap
description: The agreed 4-feature professionalization plan for DAAS and its status
metadata:
  type: project
---

Agreed 2026-06-05 to build four upgrades (user selected all four; priority =
analytical value). Status:

1. **DONE** — Real forecast backtesting + honest model selection. Added
   `agents/forecasting/tools/models.py` (Naive, Seasonal Naive, Drift, Moving
   Average, Linear Trend, Holt-Winters[optional], Prophet) and `backtest.py`
   (rolling-origin CV, `select_model`, `skill_vs_naive`). `ForecastEngine.run`
   now back-tests candidates and reports out-of-sample metrics; `model_override`
   is wired through pipeline→state→nodes; UI shows a leaderboard + skill score.
   Replaced the previous fake "Auto" (always Prophet, in-sample metrics).
   Tests: `tests/test_forecast_backtest.py`. Also fixed a pandas-3 bug where
   text columns (now `string` dtype, not `object`) weren't detected in
   `schema_intel.py` / `quality.py`.

1b. **DONE** — Churn Prediction agent (`agents/churn/`): schema-aware feature
   engineering (`features.py`), windowed labelling + GradientBoosting with a
   recency-heuristic fallback (`model.py`), orchestrator payload with risk tiers
   + revenue-at-risk + top at-risk customers (`engine.py`), LLM retention-plan
   layer (`agent.py` + `prompts/churn/strategy.md`), PG persistence
   (`storage.py`), UI `pages/6_Churn.py` (registered in nav), `DEFAULT_CHURN_MODEL`
   + sidebar selector. Tests: `tests/test_churn.py`. On Bloom & Bean: AUC 0.926,
   recency is the top driver, surfaces high-value lapsing customers. Avoids
   circular labelling via observation/outcome window split. Also added a
   realistic test dataset generator `generate_business_data.py` →
   `data/bloom_and_bean_sales*.csv`, and boosted revenue-column detection in
   `schema_intel.py` (prefer total/revenue over unit_price/cost).

1c. **DONE** — Output polish / professional reporting layer
   (`agents/reporting/`). One shared presentation for all four narrative agents
   (Insights, Marketing, Churn, Forecasting): run-metadata header + KPI strip +
   **grounding badge** + HTML(print-to-PDF)/Markdown export.
   - `grounding.py` — deterministic faithfulness check: extracts currency /
     percent / large-number figures from a report and verifies each against the
     computed payload (handles fraction↔percent, `$1.2M` suffixes, rounding;
     ignores years + small structural counts). Returns coverage + unmatched list.
     This is the v1 of roadmap item B3, no extra LLM call/cost.
   - `export.py` — dependency-free `markdown_to_html` + styled, self-contained
     `build_html_report` (roadmap C5 v1).
   - `render.py` — `render_report(...)` used by pages 3/4/5/6.
   - `agents/forecasting/report.py` — assembles the per-metric forecast output
     into one exportable, groundable markdown narrative (no new LLM call).
   Tests: `tests/test_reporting.py` (12, all green via venv python; pytest not
   installed in the venv so run with a plain driver or `pip install pytest`).

2. **TODO** — Harden the code sandbox. `tools/sandbox.py` + `agents/cleaning/
   executor.py` use `exec()` with restricted builtins, which is trivially
   escapable (e.g. `().__class__.__bases__[0].__subclasses__()`). Move to
   subprocess isolation (timeout/memory/no-net) or whitelisted tool-calling.

3. **TODO** — Supervisor router agent: one NL entry point that classifies intent
   and dispatches to clean/viz/forecast/marketing (LangGraph supervisor pattern).

4. **TODO** — Token/cost tracking + tracing across all Groq/LangGraph calls
   (wrapper that logs `usage`; per-run cost panel; optional Langfuse/LangSmith).

Update (2026-07-01): the forecast graph is now
validate→prepare→forecast→**interpret**→END (`agents/forecasting/graph.py`), so
the LLM business narrative *is* wired — the earlier "narrative absent" note is
stale. The interpreted output is also assembled into an exportable report by
`agents/forecasting/report.py` (see item 1c).

Other noted-but-unscheduled gaps: `discover_metrics_node` / `store_node` still
not wired into the graph (metric discovery + storage run from the pipeline
instead); broad `except: pass` blocks; graphs rebuilt every Streamlit rerun
(wrap with `@st.cache_resource`). See [[daas-project]].
