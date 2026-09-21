# DAAS (Data Analysis Automation System) — Multi-Agent Business Intelligence Platform

DAAS is an AI-powered, **multi-agent, database-centric data analytics platform**
for small and medium businesses (with an e-commerce / retail focus). A non-technical
business owner creates a **Project**, brings in data from any of three sources — one or
more raw files (CSV/Excel/JSON/Parquet), a **Google Sheet**, or a **link to an existing
database** — and the system detects the relationships between tables, cleans and
reconciles them, stores them in a dedicated Postgres schema, and then **analyses,
visualises, forecasts, builds marketing strategy from, predicts customer churn on, and
keeps a per-customer history of** the result — each step driven by a specialised agent
with a human-in-the-loop checkpoint. Arabic business data is detected and normalized
automatically.

It is built on **LangGraph** (agent orchestration), a **FastAPI** backend + **Next.js**
frontend (real JWT auth, no mock data anywhere in the UI), **PostgreSQL** (schema-per-project
storage), a **multi-provider LLM client** (Groq → Anthropic → OpenAI → OpenRouter fallback),
and the **pandas / scikit-learn / Prophet** scientific stack.

> This README is the single source of truth for the project's architecture and is written
> so it can be handed to any developer or AI assistant to understand and extend the system.
>
> **In a hurry, or not a developer?** Read [`PROJECT_BRIEF.md`](PROJECT_BRIEF.md) instead —
> a ten-minute, plain-language explanation of the whole system, with a map of which of the
> other documents to open next. Diagrams live in
> [`ARCHITECTURE_DIAGRAMS.md`](ARCHITECTURE_DIAGRAMS.md).

---

## Table of Contents
1. [What it does](#what-it-does)
2. [Core design principles](#core-design-principles)
3. [Tech stack](#tech-stack)
4. [System architecture](#system-architecture)
5. [Multi-source ingestion & storage](#multi-source-ingestion--storage)
6. [The ten agents](#the-ten-agents)
7. [Copilot — the cross-agent assistant](#copilot--the-cross-agent-assistant)
8. [End-to-end workflow & data flow](#end-to-end-workflow--data-flow)
9. [Project structure](#project-structure)
10. [Shared/core components](#sharedcore-components)
11. [Setup & installation](#setup--installation)
12. [Running the project](#running-the-project)
13. [Configuration](#configuration)
14. [Datasets](#datasets)
15. [How to add a new agent (worked example)](#how-to-add-a-new-agent-worked-example)
16. [Testing](#testing)
17. [Design notes & known limitations](#design-notes--known-limitations)
18. [Recent enhancements](#recent-enhancements)

---

## What it does

| Stage | Component | Output |
|-------|-------|--------|
| 0 | **Project + Ingestion** | A Project backed by its own Postgres schema, populated from files, a Google Sheet, or a linked external database — schema discovery, relationship review, cleaning, and reconciliation all converge here before anything downstream runs |
| 1 | **Data Cleaning** | A profiled, cleaned (and, for multi-table projects, reconciled) dataset with a human-approved plan of typed operators, a measured per-step audit trail, and an invariant check proving nothing changed that the plan didn't authorize |
| 2 | **Analytics** (engine) | Deterministic KPIs, time-series intelligence, data-quality report |
| 3 | **Visualization** | Chat-to-chart + an auto-generated multi-chart dashboard |
| 4 | **Business Insights** | A written, audience-aware business report |
| 5 | **Forecasting** | Multi-model, back-tested sales/metric forecasts with confidence |
| 6 | **Marketing** | RFM customer segments → strategy, campaigns, and ad copy |
| 7 | **Churn Prediction** | Per-customer churn probability + retention strategy |
| 8 | **Root Cause** | The smallest slice of the business that explains the largest part of a change — searched across every dimension combination, with the statistics to say whether it is a cause or a lead |
| 9 | **Autonomous Monitoring** | The platform runs itself on a schedule and pushes a ranked briefing to email / Telegram / WhatsApp, in English or Arabic, instead of waiting to be opened |
| 10 | **CRM — customer state** | The first *stateful* layer: a per-customer, per-snapshot record that is written once and never recomputed, so the platform can answer "was this customer riskier last month?" — plus the prioritised call list that ranks by value at risk rather than by risk alone |

The platform is intentionally **domain-aware** (e-commerce): it auto-detects revenue,
quantity, customer, order, product, date, and category columns so it works on most sales
exports without configuration. All nine downstream agents are completely unaware of where
the data came from or how many tables it started as — they always receive one flat pandas
DataFrame from the **Data Manager** (see below), which is the single seam the whole
multi-source design is built around.

---

## Core design principles

1. **Deterministic math first, LLM narrates second.** All numbers (KPIs, RFM, forecasts,
   churn probabilities) are computed in pure pandas / scikit-learn / Prophet. The LLM is
   given those computed numbers and asked only to *explain* them. This prevents the model
   from hallucinating figures — a critical property for a business tool.
2. **The LLM decides, deterministic code acts.** The same split governs *data cleaning*: the
   planner selects and parameterises typed operators from a fixed registry
   (`tools/cleaning_ops.py`), and tested code executes them. The model never writes the code
   that touches the user's data, so cleaning is deterministic, re-runnable, and unit-testable.
3. **Effects are measured, never self-reported.** Every cleaning operator is a pure
   `(df, columns, params) -> df`; the framework diffs the frame before and after and computes
   the audit trail itself. A transformation log therefore cannot claim a change that did not
   happen — the same property the reporting layer's grounding check gives narratives.
4. **Authorization over resemblance.** A cleaning result is accepted only when *every*
   observed difference is attributable to something the plan authorized
   (`agents/cleaning/invariants.py`). "The output still looks reasonable" is not a check;
   "no data changed that the plan did not name" is.
5. **Human-in-the-loop (HITL).** The cleaning plan is reviewed and editable before anything
   runs; results are inspected before they flow downstream.
6. **Self-correcting code generation.** When the fallback Coder produces code that fails —
   whether it crashed or broke an invariant — the specific reason (rule + column, not just a
   traceback) is fed back through a repair prompt and it retries.
7. **Degrade to correct, never to broken.** Each LLM step has a deterministic floor: an
   unreachable, rate-limited or nonsensical model costs the model's *judgement*, not the
   cleaning. A table is never dropped from a project because a provider was down.
8. **Per-agent model selection.** Each agent can use a different LLM, chosen in Settings and
   persisted per-user.
9. **Graceful degradation.** Optional dependencies (PostgreSQL, statsmodels) and optional
   columns are all handled with safe fallbacks.
10. **Separation of concerns.** Each agent is a self-contained package: deterministic engine,
    optional LLM layer, optional persistence, and a UI page.
11. **Verifiable output.** Every generated report is passed through a **grounding check**
    (`agents/reporting/grounding.py`) that traces each figure in the narrative back to a
    computed value and flags anything it cannot match — a measurable guard against
    hallucinated numbers, surfaced as a badge in the UI and in the export.
12. **Portable deliverables.** Insights, Marketing, Churn and Forecast reports export to a
    styled, self-contained, print-ready HTML document (open → *Save as PDF*) and to raw
    Markdown, so the output is board-ready — with zero extra dependencies.
13. **Convergent ingestion, one storage model.** Files, a Google Sheet, or a linked external
    database all funnel through the same Schema Discovery → Relationship Review → Cleaning →
    Reconciliation → Storage pipeline, so nothing downstream needs to know or care where the
    data came from.
14. **Trust hierarchy for relationships.** Existing database foreign-key constraints are
    trusted directly (confidence 1.0) ahead of heuristic detection, which in turn only
    escalates genuinely ambiguous candidates to an LLM — keeping both accuracy and token
    cost under control.
15. **Provider-agnostic LLM calls.** Every agent calls one `tools.llm_client.complete()`
    function; Groq is tried first, then Anthropic, OpenAI, and OpenRouter, so a single
    provider outage or invalid key doesn't take the whole app down.

---

## Tech stack

| Concern | Technology |
|---------|------------|
| Agent orchestration | **LangGraph** (`StateGraph`, conditional edges, retries) |
| Backend API | **FastAPI** (`backend/app/`) — one router per domain under `/api/v1`, run with **uvicorn** |
| Auth | Real email/password login — **pyjwt** access/refresh tokens + **bcrypt** password hashing (`backend/app/core/security.py`) |
| Frontend | **Next.js 16** (App Router) + **React 19** + **TypeScript**, **NextAuth** (Credentials provider bridging to the FastAPI backend), **@tanstack/react-query** for all server state, **Tailwind CSS v4** + **shadcn/ui**, **Zustand** for client-only UI state, **next-themes** for a real light/dark toggle (OKLCH CSS custom properties in `globals.css`, charts re-themed separately since Plotly can't read `oklch()`) |
| LLM inference | Multi-provider (`tools/llm_client.py`): **Groq** (primary) → **Anthropic** → **OpenAI** → **OpenRouter** (fallback chain, configurable order) |
| Internationalization | **next-intl** — 20 message namespaces per locale (`frontend/src/messages/{en,ar}/`), a client `LocaleProvider` that also flips `<html dir>` to `rtl`, and Tailwind logical properties (`ms-`/`me-`, `text-start`/`text-end`) so one stylesheet serves both directions |
| Interactive ERD | **@xyflow/react** (React Flow) — the relationship graph on the Data page: one node per table, PK/FK role marked per column, an edge per proposed or accepted relationship |
| Data | **pandas**, **numpy** |
| ML / stats | **scikit-learn** (churn), **shap** (per-prediction churn explainability), **Prophet** + **statsmodels** (forecasting), **scipy** |
| Charts | **Plotly** (rendered in the frontend via `react-plotly.js`) |
| Storage | **PostgreSQL** (Docker, schema-per-project) via **SQLAlchemy** + **Alembic** (platform tables: users, projects, relationships, connection_configs, reports, api_keys) |
| Caching | **cachetools** (TTL-cached semantic views in the Data Manager) |
| Google Sheets | **gspread** + **google-auth** (service-account read access) |
| Credential encryption | **cryptography** (Fernet — encrypts stored external-DB connection credentials) |
| Config | **python-dotenv** (`.env`) |
| Tests | **pytest** (hermetic by default; `@pytest.mark.integration` tests need the live Postgres and are auto-skipped otherwise) |
| Lint/format | **ruff** (Python), **eslint** + **tsc --noEmit** (frontend) |

Environment used in development: **Python 3.11**, pandas 3.x, numpy 2.x,
scikit-learn 1.9 (plus `openpyxl` / `pyarrow` for Excel & Parquet ingestion), **Node.js 20+**,
Docker Desktop for the Postgres container.

---

## System architecture

```
  ┌────────────────────────────┐        HTTPS / JSON        ┌───────────────────────────────┐
  │   frontend/ (Next.js 16)   │ ─────────────────────────► │ backend/app/ (FastAPI)        │
  │   App Router · React Query │                            │ /api/v1/{auth,projects,       │
  │   NextAuth (JWT session)   │ ◄───────────────────────── │ ingestion,pipeline,dashboard, │
  │   Zustand (UI-only state)  │        JSON responses      │ insights,forecasting,         │
  │   Tailwind v4 + shadcn/ui  │                            │ marketing,churn,crm,settings, │
  └────────────────────────────┘                            │ reports,account,ask}          │
                                                            └───────────────┬───────────────┘
                                                                            │ imports directly
                                                                            ▼
                                            agents/ · db/ · tools/ · ingestion/ · schema_discovery/ ·
                                            relationships/ · reconciliation/ · integrity/ ·
                                            data_manager/ · graphs/ · core/   (UNCHANGED — the same
                                            packages `pytest` exercises; the backend is a thin,
                                            request/response wrapper around them, not a rewrite)
                                                                              │
                                                                              ▼
                                                      db.loader (DDL + bulk COPY)
                                                                              │
                                                                              ▼
                                                Postgres, schema-per-project (project_<slug>)
                                                                              │
                                                                              ▼
                                    db.views (semantic views: analytics / forecast / marketing / customer_360)
                                                                              │
                                                                              ▼
                                      data_manager.DataManager (TTL-cached) ──► backend/app/services/views.py
                                                                                (resolve_view) ──► every router,
                                                                                as ONE flat pandas DataFrame

   Shared services:  core/state.py (GraphState) · tools/sandbox.py (safe exec) ·
                     tools/llm_client.py (multi-provider LLM) · tools/profiler_tools.py ·
                     db/ (platform tables, DDL, loader, views, reflect, connection_configs) ·
                     agents/analytics/* (schema intelligence, KPIs, time-series, quality) ·
                     backend/app/services/pipeline_sessions.py (in-memory HITL cleaning-session
                     store — the API's equivalent of Streamlit's old st.session_state, single-
                     process by design; see Design notes & known limitations)
```

**Auth model:** email/password registration and login issue a JWT access token (short-lived)
and refresh token (long-lived) — `backend/app/core/security.py` (pyjwt + bcrypt). The Next.js
frontend's NextAuth Credentials provider calls the FastAPI `/auth/login` endpoint and carries
the access token inside the NextAuth session; every `react-query` hook attaches it as a
`Bearer` header via `frontend/src/lib/api/use-api.ts`. Every project-scoped FastAPI route
depends on `get_owned_project`, which 403s if the authenticated user doesn't own the project.

**Four independent LangGraph state machines** exist:
- **Cleaning** (`core/state.py: GraphState`) — profiler → planner (planning graph), then
  coder → executor → validator (cleaning graph), where a validator rejection loops back to
  the coder; looped once per table for multi-table projects by
  `agents/cleaning/multi_table.py` (the graph itself is untouched).
- **Forecasting** (`agents/forecasting/forecast_models.py: ForecastState`) — validate →
  prepare → forecast.
- **Visualization** (`agents/visualization/viz_models.py: VizState`) — retrieve_schema →
  coder → executor.
- Schema Discovery and Relationship Review are plain deterministic + LLM-escalation
  pipelines (no graph) — see the next section.

The Analytics, Insights, Marketing, and Churn agents are plain Python pipelines (no graph)
that call deterministic engines and then an LLM layer. The CRM layer (`agents/crm/`) has no
LLM at all: it is a four-module domain package whose compute half never touches the database
and whose persistence half never computes — see [CRM_ARCHITECTURE.md](CRM_ARCHITECTURE.md).

---

## Multi-source ingestion & storage

Every project converges on the same pipeline no matter which of the three sources it
started from:

```
Create Project
     │
     ▼
Choose Data Source ──► Path A: Files  |  Path B: Existing Database  |  Path C: Google Sheet
     │                     │                    │                          │
     │              tools.ingestion +    ingestion/db_link.py       ingestion/gsheets.py
     │              ingestion/multi_table  ReadOnlyConnector          service-account auth
     │              (each file/table kept  + sqlalchemy.inspect       (gspread), one
     │               separate — NOT        introspection              DataFrame per tab
     │               collapsed into one)
     │                     │                    │                          │
     └─────────────────────┴────────────────────┴──────────────────────────┘
                                       │
                                       ▼
                    schema_discovery/ — heuristic FK/PK scoring (name
                    similarity, dtype compatibility, uniqueness, coverage),
                    LLM escalation only for candidates < 0.85 confidence.
                    Path B's real FK constraints are trusted directly
                    (confidence 1.0) ahead of heuristics/LLM.
                                       │
                                       ▼
                    relationships/review.py — confidence-gated HITL:
                    ≥0.85 auto-approved · 0.5–0.85 explicit approval,
                    pre-filled with the best guess · <0.5 manual mapping,
                    not pre-approved. The user can also define a relationship
                    the scan never proposed at all — any table/column pair,
                    added as its own approved candidate (frontend
                    ManualRelationshipForm). Persisted to the `relationships`
                    platform table the same way either way.
                                       │
                                       ▼
                    agents/cleaning/multi_table.py — loops the existing,
                    unmodified single-table planner/cleaning LangGraph once
                    per table (approved-relationship key columns are
                    threaded in so the planner treats join keys
                    conservatively). One table failing doesn't block others.
                                       │
                                       ▼
                    reconciliation/ — numeric-aware key normalization
                    (`1001` / `1001.0` / `"1001"` all collapse to one value)
                    across every approved relationship's key columns, then
                    an orphan-rate before/after check with a tolerance gate
                    and a sample of unmatched values for HITL override.
                                       │
                                       ▼
                    db/ddl.py + db/loader.py — pandas dtype → PostgreSQL
                    type mapping, real PRIMARY KEY / FOREIGN KEY DDL from
                    the approved relationship graph, topologically ordered
                    (dimension tables before the fact tables that reference
                    them), bulk-loaded via `COPY` into a per-project schema
                    (`project_<slug>`) in one shared Postgres instance.
                                       │
                                       ▼
                    db/views.py — one semantic view per agent (analytics /
                    forecast / marketing / customer_360): the fact table
                    (whichever table is the "many" side of the most
                    relationships) left-joined with its directly-related
                    dimension tables, in SQL, returning ONE flat DataFrame.
                                       │
                                       ▼
                    data_manager/manager.py — a `cachetools.TTLCache`
                    (5 min default) in front of the views. The ONLY
                    component that talks to Postgres for agent-data reads.
                                       │
                                       ▼
          backend/app/services/views.py: resolve_view() ──► every FastAPI router:
                                Dashboard · Insights · Forecasting · Marketing · Churn
```

**Design choices worth knowing:**
- **One backend process, the agent packages untouched.** `backend/app/` is a FastAPI service
  that imports `agents.*`, `db.*`, `tools.*`, etc. exactly as they are — run `uvicorn` from the
  repo root so those packages resolve as top-level imports, the same way `pytest` already
  expects. Nothing in `agents/`/`db/`/`tools/` was rewritten to build the API; each router is a
  thin request → engine-call → response mapping.
- **Semantic views only join one hop.** A view joins the chosen fact table with tables
  *directly* related to it; it does not chase multi-hop chains (e.g. `order_items → orders →
  customers` would pull in `orders`' columns but not `customers`' unless `customers` is
  directly related to the fact table too). Keep star-schema-shaped data (one fact table,
  several dimensions directly referencing it) for the cleanest result.
- **`ReadOnlyConnector` (Path B) enforces SELECT-only at the code level** — it cannot force
  the *database role itself* to be read-only, so use a genuinely read-only credential.
- **Google Sheets uses a GCP service account**, not interactive OAuth — share the target
  sheet with the service account's `client_email`.
- **Sync is on-demand**, not continuous, for both Path B and Path C — click "Sync Now" /
  "Refresh from Sheet" to pull the latest data.
- **Credential encryption (`db/connection_configs.py`)** is Fernet-based (`FERNET_KEY` in
  `.env`) — proportionate for a single-dev tool, not a real KMS: there's no key rotation, and
  losing the key means re-entering credentials.

---

## The ten agents

### 1. Data Cleaning  (`agents/cleaning/`, `graphs/`, `backend/app/api/v1/pipeline.py`, frontend `data` section)
A 5-node LangGraph pipeline split into two stages around a human checkpoint — the
front door for all three ingestion paths (see
[Multi-source ingestion & storage](#multi-source-ingestion--storage) above).

**Ingestion** runs before the graph: `tools/ingestion.load_tabular_file` parses any
supported format (CSV/TSV/Excel/JSON/Parquet) with encoding fallback (UTF-8 / UTF-8-BOM /
CP1256 / Latin-1) and delimiter sniffing; `ingestion/multi_table.py` keeps every file as
its own table (no collapsing). **Every table gets its own independently reviewable plan** —
the Data Workspace's table sidebar drives which table is being profiled/planned/cleaned
(`frontend/src/components/sections/data-workspace.tsx`: `CleanStage`/`ProfileStage` key
their state and hooks off `activeTable`, not just whichever table happens to be primary).
"Clean remaining tables automatically" is still available as a one-click shortcut for
tables the user doesn't want to review individually — it runs the same graph, just without
a human pausing on each plan. Only after "Save to Project" does data reach the other six
agents. *(`tools/ingestion.plan_combination`/`combine_datasets` — the old append/star-join
collapse-to-one-DataFrame behavior — still exists and is still tested, but the main
upload flow no longer calls it.)*

**The LLM plans; deterministic tested code executes.** Cleaning is not generated Python.
`tools/cleaning_ops.py` holds a registry of **21 typed operators** — each a pure
`(df, columns, params) -> df` function with a declared parameter schema — and the planner's
job is to *select and parameterise* them. It cannot invent an operator, and a step with an
unknown operator or a malformed parameter is rejected before anything runs.

| Node | File | LLM? | Role |
|------|------|------|------|
| Profiler | `profiler.py` | No | **Normalizes Arabic text** (`tools/arabic_text.py`: diacritics/tatweel stripped, alef variants unified, Arabic-Indic digits → Western); builds a `DatasetProfile`; then runs **`tools/defect_detection.py`** — 19 deterministic checks covering 22 defect kinds, producing measured `DefectFinding`s (placeholders in mixed-dtype columns, numbers-as-text incl. currency/percent, dates-as-text incl. Excel serials and DD/MM ambiguity, category spelling variants, `-999` sentinels, duplicate business keys, sign violations, `total = qty × price` violations, empty/constant/duplicate-named columns). Deliberately does **not** modify the data — every change is an explicit, authorized plan step |
| Planner | `planner.py` | Sometimes | `plan_from_defects()` derives a **complete typed baseline plan** from the findings, correctly ordered. The LLM then *reviews* it — drop a step, adjust a parameter, add one from the catalog. If the table has no defects the LLM is never called; if the LLM is unreachable, returns junk, or names an operator that doesn't exist, the baseline plan runs instead. The worst case is a deterministic clean, never a bad one |
| *(human review)* | frontend `data` section | — | Per-step review: delete any step, or write your own — adding one triggers a grounded LLM opinion (`POST .../plan/opinion`, `planner.py::review_manual_step`). Each step shows its operator name, or a "Custom step" badge for free-text |
| Coder | `coder.py` | Rarely | **Fallback only.** Runs solely for free-text steps no operator covers (typically a hand-typed instruction). A fully typed plan never reaches an LLM here. On retry it receives the *invariant violations* — the exact rule and column that broke — not just a traceback |
| Executor | `executor.py` | No | Applies the typed operators via `tools/cleaning_ops.apply_plan()`, then any generated code for free-text steps. Operators never report their own effects: `apply_step` **diffs the frame before and after and measures** the ledger (cells changed, nulls filled/introduced, rows removed, dtype changes, before/after samples) |
| Validator | `validator.py` | No | Runs `agents/cleaning/invariants.py` — every observed difference must be attributable to something the plan authorized. Emits structured violations + warnings and the repair feedback the Coder retries against |

Graphs: `graphs/planner_graph.py` (profiler→planner) and `graphs/cleaning_graph.py`
(coder→executor→validator, with **validation failure feeding the repair loop**, up to 2
retries). A typed plan that fails invariants goes straight to END rather than burning
retries — that would indicate an operator bug, not a bad LLM sample.

**Why the invariant gate exists.** The previous validator checked three things: at least one
row survived, the total null count didn't rise, and no column's null count rose. Measured
against deliberately corrupting `clean_data` functions, **all seven of these passed it** —
a revenue column multiplied by 100, overwritten with its own mean, or deleted outright; 45%
of rows deleted; a join key case-folded; every row duplicated; a column shuffled against the
wrong rows. None of them changes a null count. The model is now *authorization*: operators
declare statically what class of change they may cause (`may_remove_rows`,
`may_rewrite_values`, `may_fill_nulls`, `may_introduce_nulls`, `may_add_columns`,
`may_drop_columns`, `may_change_dtype`), and the check is **cell-level** — naming a column in
an `impute` step authorizes filling its blanks, not rewriting the values already in it.

Three properties worth stating explicitly:

- **Nothing is fabricated to keep a metric flat.** Unparseable input becomes NULL and is
  reported. The old prompts instructed the model to `ffill`/`bfill` coerced `NaT`s *"so the
  validation check doesn't flag a regression"* — that is inventing timestamps to satisfy a
  check, and forecasting then ran on them.
- **The system owns execution order, not the model.** `order_steps()` sorts every plan
  regardless of origin. De-duplication must precede imputation: imputing first turns
  `[1.0, NULL, 1.0]` into three identical rows, manufacturing a duplicate that was never in
  the source, which the dedupe step then deletes — destroying a genuinely distinct record.
- **Join keys are structurally protected.** `KEY_SAFE_OPS` skips any representation-changing
  operator on a relationship key, and the `join_key_modified` invariant rejects the change
  even from generated code. Cleaning can no longer introduce the key drift that used to
  surface as a `ForeignKeyViolation` at save time.

`core/state.py`'s `cleaning_plan` is `list[dict]` of `{id, op, columns, params, description,
source}`; `source` is `detector` (deterministic baseline), `generated` (LLM-refined),
`manual` (user-written), or `fallback`. Prompts live in `prompts/` — `planner_prompt.txt`
(operator catalog injected at call time), `coder_prompt.txt` and `repair_prompt.txt` (both
fallback-only now), and `planner_step_opinion_prompt.txt`.

### 2. Analytics  (`agents/analytics/`) — deterministic engine, no UI page of its own
The analytical backbone reused by Insights, Marketing, and Churn.
- `schema_intel.py` — heuristically detects column roles (time, monetary, quantity,
  product, customer, order, category) from names + dtypes. **`build_schema_summary(df)`** is
  the key entry point.
- `kpi_engine.py` — revenue, orders, AOV, revenue-per-customer, top/bottom products,
  new-vs-returning customers, monthly growth, category breakdowns.
- `timeseries.py` — daily/weekly aggregation, rolling means, trend slope, anomaly detection
  (z-score + IQR), spikes/drops, structural breaks.
- `quality.py` — data-quality checks (missing, duplicates, type consistency).
- `engine.py` — `run_analytics(df)` orchestrates all of the above into one payload.

### 3. Visualization  (`agents/visualization/`, `backend/app/api/v1/{dashboard,charts}.py`, frontend `visualization` section)
- `builder.py` — `build_executive_dashboard(df)`: a **deterministic** board-quality dashboard
  (KPI cards + curated charts — revenue trend, category & product leaders, customer mix,
  monthly/day-of-week seasonality, order-value distribution) computed straight from the
  schema/KPI engine, so it is always correct and consistent. Uses a corrected revenue series
  (price × quantity when only a unit price exists) and non-overlapping customer segmentation.
- `theme.py` — the registered `smart_analyst` Plotly template (palette, typography, gridlines)
  + `style_figure()` so **every** chart, deterministic or AI-written, shares one house style,
  plus number-formatting helpers.
- `export.py` — `build_dashboard_html()`: bundles the whole dashboard into one self-contained,
  interactive, print-to-PDF HTML file.
- `viz_graph.py` — chat-to-chart: user asks in natural language → LLM writes Plotly code →
  executed in the sandbox → figure rendered (themed). Retries on error.
- `dashboard.py` — `generate_dashboard()` asks the LLM to identify KPIs and emit 5–7 diverse
  charts in one pass, with professional-quality rules + automatic error-repair retries.
- `explain.py` — `explain_chart(figure, ...)`: available on *every* chart the frontend
  renders (not just this agent's own), via the shared `PlotlyChart` component's "explain
  this chart" button. Grounded in `describe_figure()`'s structured summary of the actual
  trace data — never the chart's generating code or a live DataFrame — so it can't describe
  a value that isn't really there. See [Copilot](#copilot--the-cross-agent-assistant).
- The frontend renders the KPI band + chart grid via `react-plotly.js`, plus an "AI Chart
  Studio" prompt box wired to `viz_graph`. *(Live date/category slicers are not implemented
  in the current frontend — re-running the dashboard/AI chart endpoints is the only way to
  refresh it today.)*
- `viz_models.py` — `VizState` TypedDict.

### 4. Business Insights  (`agents/insights/`, `backend/app/api/v1/insights.py`, frontend `insights` section)
**The model never types a digit.** This is the strongest guarantee in the platform, and it is
worth being precise about why it is stronger than a grounding check. A checker is a *guard*:
the model writes a number and afterwards you try to prove it came from the data. That can only
ever catch fabrication, and it catches it probabilistically — a wrong figure landing near some
value in a large payload slips through. This pipeline inverts the flow instead.

- `decision_metrics.py` — the deterministic layer that turns *descriptive* analytics into
  *decision-grade* evidence. The KPI engine answers "what are the totals?"; a business owner
  needs the next three questions: is it better or worse than before and by how much (period
  comparison), what actually drove the change — more orders or bigger baskets (the revenue
  bridge, because the two imply completely different actions) — and where the money is
  concentrated, leaking, or at stake. Two invariants make its output safe to publish verbatim:
  period revenue is summed from the *same* per-row `line_revenue` series as the headline (so
  parts reconcile with the whole), and any figure depending on an assumed column identity
  (`profit = revenue − cost`, `revenue = gross × (1 − discount)`) is published **only when that
  identity is checked against the actual rows** — when it fails the section is omitted with a
  stated reason rather than printing a plausible-looking wrong number.
- `figures.py` — the **citation registry**. Every number the report is allowed to contain is
  computed here first and registered with an exact `value`, the `display` string it must be
  printed as, the `formula` and inputs it came from, and a stable `key`. The model is told to
  write `{{revenue_total}}` where a figure belongs, and `render()` substitutes the registered
  display strings server-side after generation. **A cited number is not "checked" — it is never
  produced by the model at all, so it cannot be wrong.** Figures carry a quality flag (`exact`
  = measured from rows, `scenario` = exact arithmetic under a stated assumption).
- `evidence.py` — decides *what matters* before the model decides how to say it. A model handed
  120 equally-presented figures gives the weekday pattern the same weight as a 23% revenue
  collapse. Prioritisation is a business judgement and it is deterministic: rank by money at
  stake, tag each item (THREAT / LEAK / RISK / OPPORTUNITY / STRENGTH / CAVEAT), and hand the
  model an already-ordered brief so its job narrows to writing it well. The thresholds are
  named constants, not inline magic, so the editorial judgement is visible and arguable. **The
  monitoring agent's alert ranking reuses this same engine** — which is why a scheduled alert
  and an insight in the report cannot disagree about what matters.
- `strict_verify.py` — closes the other direction: anything the model typed as raw digits
  anyway is checked against the registry, a closed allow-list of ~100 curated figures, rather
  than against every numeric leaf in a nested payload. That difference is the whole point — the
  general checker gives a wrong number thousands of chances to coincidentally verify; here a
  typed number must equal a figure a human decided was meaningful, in one of its legitimate
  rounded forms, or it is reported unverified **with the sentence it appeared in**, so the
  correction pass fixes that exact claim instead of rewriting blind. A currency symbol counts as
  a claim too: the dataset records amounts, never which currency they are in.
- `template_selector.py` — picks the report template (`non_technical`, `executive`, `detailed`)
  from business context + analytics payload. Templates live in `prompts/insights/`.
- `agent.py` — `generate_insights()` assembles the chosen template + the ranked evidence + a
  slimmed reference payload, calls the LLM, normalises malformed citation tokens (models drop a
  brace, and models add a second `%` to a figure that already renders its own), substitutes the
  registry, and fails on any brace-wrapped markup surviving into the report — leftover markup
  is a defect, never cosmetic.
- The browser surface for all of this is `frontend/src/components/sections/insights-audit.tsx`:
  where each figure came from, why the findings are in that order, and what the data provably
  could not answer. A claim the reader cannot check is just a nicer-sounding claim.

### 5. Forecasting  (`agents/forecasting/`, `backend/app/api/v1/forecasting.py`, frontend `forecasting` section)
A LangGraph pipeline with a **real, back-tested model selector**, an **honest ensemble**, and
honest confidence — every number shown is out-of-sample, never in-sample or invented.
- `pipeline.py` — `ForecastPipeline.run(..., granularity=...)` builds state and invokes the
  graph; persists results if configured.
- `graph.py` / `nodes.py` — validate → prepare → forecast → interpret. `forecast_node`
  forecasts each target metric in parallel, at the chosen **granularity** (daily/weekly/
  monthly): additive metrics (revenue, quantity) are summed per period, rates averaged, and
  day-based horizons are converted to periods.
- `validation.py` / `metric_discovery.py` — date detection, frequency, history sufficiency,
  and metric ranking that prioritises the true revenue column.
- `tools/models.py` — model library: Naive, Seasonal Naive, Drift, Moving Average,
  Linear Trend, Holt-Winters, **Theta** (M3 winner), **ETS**, **ARIMA** and **SARIMA**
  (statsmodels), Prophet. `arima()` fits a curated grid of `(p,d,q)` candidates and keeps
  the lowest-AIC result instead of a fixed `(1,1,1)`; `sarima()` does the same for a
  seasonal-order grid at the series' natural period (7/52/12), via `SARIMAX`, falling back
  to plain `arima()` if every seasonal fit fails — a custom curated-grid + AIC search, not a
  `pmdarima` dependency.
- **Scaled errors, not MAPE, drive selection** (`tools/evaluation.py`). MAPE's best-known
  pathology is exploding (or undefined) when actuals pass near zero, which silently corrupts
  ranking; both scaled metrics are computed instead and MAPE is kept only as the familiar
  "%" figure. **Which** scaled metric ranks a series is decided by what the series measures
  (`objective_for`): additive metrics (revenue, units) are published as a horizon **total**,
  a total is a sum of means, and the mean minimises *squared* error — so those rank on
  **RMSSE** (the M5 metric). Rates (AOV, discount %) are published as a typical level and
  rank on **MASE**. Getting this backwards is not subtle: ranking a right-skewed revenue
  series on MASE selects for the conditional median and under-states every horizon total by a
  consistent margin, which is exactly what produced 22–36% shortfalls before the fix.
- **The ranked score also carries horizon-total error.** `_primary_error()` inflates the
  per-period scaled error by the relative error on the horizon total (`_AGG_WEIGHT`), because
  both are shipped — a flat mean model otherwise wins on per-period squared error precisely
  by ignoring the trend that determines the 30-day total.
- `tools/backtest.py` — **rolling-origin cross-validation**, `select_model`, `skill_vs_naive`;
  seasonal series are reliably back-testable. The fold count is **adaptive**
  (`_adaptive_folds`) — 3 folds on short histories up to 6 on long ones. Three properties the
  module is explicitly responsible for, each a past source of error:
  - **Causality.** Everything a fold does to its training window — anomaly repair, transform
    selection, the fit — sees only data before that fold's cut point, and scoring always uses
    the **raw** held-out actuals so a model is never rewarded for a period that was smoothed
    away.
  - **Robust ranking.** Candidates are ranked by their *median rank across folds*, not by
    pooled error, so one catastrophic period cannot hand the run to whichever model happened
    to undershoot it.
  - **Parsimony.** With 3–6 folds a few percent of error difference is noise, so a
    **one-standard-error rule** (`_one_se_pick`) keeps the simplest model statistically
    indistinguishable from the nominal winner — this is what stops selection over-fitting.
- **Ensemble candidate** — `select_model` also back-tests an inverse-error-weighted blend of
  the top 3 individually-back-tested models (`backtest_ensemble`, `inverse_error_weights`)
  as its own candidate, `"Ensemble"`, scored on the *same* held-out folds as every other
  model. It only wins the leaderboard — and only ever gets selected by `Auto` — when its
  out-of-sample error on that series' own ranking objective (above) genuinely beats every
  single model, and then only if it survives the one-standard-error rule; there is no
  hand-waving. Users can
  also force it directly from the model dropdown. Prediction intervals for an ensemble use
  the standard forecast-combination variance decomposition (Bates–Granger): total uncertainty
  = each component's own within-model uncertainty **plus** how much the components disagree
  with each other — not a naive average of bounds, which would understate risk.
- **Outlier-robust, without leaking.** Every candidate trains on an anomaly-repaired series
  rather than chasing one-off spikes — but the repair is fitted **per fold, on that fold's
  training window only**. Repairing the whole series once up front (the earlier behaviour)
  used a *centred* rolling median, which reaches forward in time and makes back-test scores
  better than the live forecast can reproduce.
- **Calibrated intervals, and a horizon total.** The published figure for an additive metric
  is the **horizon total** with an interval (`horizon_total`, `horizon_total_lower/upper`),
  because that is the number a business plans against. Intervals are **conformal** — derived
  from the winner's actual back-test residuals at every published step rather than from a
  model's own optimistic variance — and the achieved coverage is measured and reported
  (`measured_coverage`) so the interval can be checked instead of trusted.
- **A reliability verdict, not just a number** (`tools/diagnostics.py`). A run is labelled
  `reliable` / `indicative` / `unreliable` from skill against the *stronger* of the plain and
  seasonal naive baselines, predictability, fold count and interval coverage, and the label
  caps the confidence score — a forecast that fails its checks cannot present a high
  confidence whatever its error metrics say.
- `tools/engines.py` — `ForecastEngine.run(model, series, horizon)`: when `model="Auto"`,
  back-tests all candidates (including the Ensemble) and picks the winner; reports honest
  out-of-sample metrics, a selection reason (naming the blend and its weights when an
  ensemble wins), the leaderboard, and a skill score.
- Confidence = a principled, bounded transform of the back-test error rewarded for beating
  the naive baseline; it is **`None` ("not back-tested")** rather than a fabricated number
  when a series can't be cross-validated.
- `interpreter.py` — LLM business narrative (wired into the graph). `report.py` assembles the
  exportable, grounded forecast report.
- `schema.py` — `ForecastOutput` / `ForecastEvaluation` pydantic models.
- `storage.py` — PostgreSQL persistence of runs/forecasts/evaluations/history.

### 6. Marketing  (`agents/marketing/`, `backend/app/api/v1/marketing.py`, frontend `marketing` section)
- `segmentation.py` — **RFM** (Recency/Frequency/Monetary) segmentation into named segments
  (Champions … Lost) with per-segment stats and playbooks. Pure pandas.
- `engine.py` — `run_marketing_analytics(..., churn_payload=...)` = schema + KPIs + RFM +
  marketing KPIs (repeat rate, CLV proxy, churn-risk base) + channel/dimension performance
  + **an optional `churn` section fed by the Churn agent**: model-scored churn risk per RFM
  segment, expected revenue at risk, and exportable high-/medium-risk audience lists — so
  campaigns target model-scored customers instead of RFM proxies.
- `agent.py` — LLM layer: strategy report (markdown), campaign plan (JSON), ad copy (JSON).
  `slim_for_prompt()` strips internal `_`-prefixed keys (per-customer maps) before prompting.
- **Grounding check** — the `/run` report is now checked with the same
  `agents/reporting/grounding.py::check_grounding()` Insights uses, verified against the
  computed marketing payload *and* any forecast highlights that were injected into the
  prompt, surfaced as the same `GroundingBadge` + coverage label in the frontend. This
  checks *factual* figures (segment sizes, revenue, churn %) — forward-looking numbers the
  model is explicitly asked to originate (suggested budget-allocation %, a target repeat-rate
  lift) have no ground truth to check against and are expected to show as unverified; that's
  correct behavior, not a bug, since they're the model's recommendation, not a restated fact.
- `storage.py` — PostgreSQL persistence. Prompts in `prompts/marketing/`.

### 7. Churn Prediction  (`agents/churn/`, `backend/app/api/v1/churn.py`, frontend `churn` section)
Supervised churn modelling on transaction data — production pipeline.
- `features.py` — schema-aware per-customer features (recency, frequency, monetary, tenure,
  AOV, inter-purchase gap, product/category diversity, discount usage, **and momentum:
  orders in the last 30/60/90 days, recent-spend share**), all measured as-of a cutoff date.
- `model.py` — **windowed labelling** (label = "did NOT purchase in the next horizon") on a
  **multi-cutoff training panel** (up to 6 historical windows), validated **out-of-time** on
  the newest window — the honest production metric. `HistGradientBoostingClassifier`
  (scales to large data, native NaN, early stopping) with **probability calibration** on the
  holdout and **permutation importances**; graceful fallbacks (single-cutoff random split →
  recency heuristic) for short/tiny histories.
- `engine.py` — `run_churn_analysis()` → payload: model metrics (AUC, precision, recall,
  validation type, training cutoffs), feature importance, risk tiers, revenue-at-risk **and
  expected (probability-weighted) revenue-at-risk**, the top at-risk customers **ranked by
  expected value at risk (churn probability × spend)**, and a full `_customer_scores` map
  consumed by the Marketing agent (never sent to the LLM).
- **SHAP explainability** (`model.py`: `_shap_global_importance`, `explain_predictions`) —
  a second, complementary explainability technique alongside permutation importance:
  `shap.TreeExplainer` runs on the *base* `HistGradientBoostingClassifier`, never the
  probability-calibration wrapper around it (calibration is a monotonic rescaling on top —
  it doesn't change which features drive a prediction, only makes SHAP's scale harder to
  read). Global importance is computed once on the validation window; **per-customer top-3
  signed drivers** (`increases_risk` / `decreases_risk`) are computed only for the customers
  already being shown in `at_risk_customers` — not the whole customer base — so the cost
  scales with what's displayed, not with data size. Surfaced in the frontend as a second bar
  panel plus small driver chips per at-risk customer, and the retention-strategy prompt can
  cite one customer's actual drivers by name instead of only speaking in generalities.
- `agent.py` + `prompts/churn/strategy.md` — LLM retention strategy grounded in the payload
  (including the SHAP output above).
- `storage.py` — the original run-level persistence (defined; still not called — it only ever
  stored the displayed top-N, which is why it could not back a customer page). **Customer
  history is now persisted by the CRM layer instead**: `POST /churn/run` calls
  `agents.crm.service.record_churn_run()`, which folds the churn result already in hand into
  a full `customer_state` snapshot covering *every* customer, with no second model fit —
  see agent 10 below and [CRM_ARCHITECTURE.md](CRM_ARCHITECTURE.md).

---

### 8. Root Cause  (`agents/rootcause/`, `backend/app/api/v1/rootcause.py`, frontend `root-cause` section)
The Insights agent answers *what* moved and *which lever* moved it — "revenue fell 6,656,
and 4,514 of that came from order count, not basket size". The next question a human
analyst always asks is *where*: which product, which region, which channel, which
combination of the three. Answering it by hand means slicing the data along every
dimension and every pair of dimensions until something stands out. That is a search
problem with a well-defined objective, which means it can be done exactly, in code, and
proven.

**The objective.** Dimensions define a lattice of *slices* — conjunctions of
`dimension = value`. For a slice `S` and an additive measure `M`:

```
delta(S)    = M(current ∧ S) − M(prior ∧ S)
EP(S)       = delta(S) / delta(∅)                     "explanatory power"
expected(S) = M(prior ∧ S) × M(current) / M(prior)
excess(S)   = M(current ∧ S) − expected(S)
```

The gap between the last two lines is what separates this from a `groupby`. **EP** says how
much of the change a slice *accounts for* — a slice can top that list purely by being
large. **excess** says how much of it the business-wide trend does *not* explain: a segment
that shrank at exactly the company rate contributed a great deal and caused nothing. A root
cause has to score on both, and on a third axis — how small a part of the business it is.
Explaining 92% of a decline from 4% of the transactions is a finding; explaining 92% of it
from 89% of the transactions is a restatement of the total. `search._score` combines the
three, and every factor is separately visible in the output so the ranking can be argued
with rather than trusted.

**The combinatorial problem.** Exhaustive evaluation is `Π(cardinalityᵢ + 1)` slices —
eight dimensions averaging twelve values each is ~800 million. Four things make it
tractable, and **only the first three are exact**:

1. **Vectorised sibling evaluation.** Children are never evaluated one at a time. For a
   parent slice and a dimension, one `np.bincount` over the parent's rows produces the
   prior value, current value and row count of *every* child at once — O(rows in the
   parent), not O(rows × cardinality).
2. **Support pruning (exact).** Row count is monotone non-increasing down the lattice, so a
   slice below the support floor has no descendant above it.
3. **Magnitude-bound pruning (exact).** For `S' ⊆ S`, each period's value is bounded by that
   period's positive/negative mass inside `S`, giving
   `|delta(S')| ≤ max(pos_cur(S) − neg_pri(S), pos_pri(S) − neg_cur(S))`. A slice whose
   bound is under the materiality floor cannot contain a material finding, so the whole
   subtree is dropped unvisited. Distinct-count measures are monotone too, so the bound
   still holds.
4. **Beam (approximate — the only approximate step).** The beam is *split*, and getting
   this wrong is how a drill-down quietly fails. Ranking by the magnitude bound alone is
   ordering by slice *size*: the search then drills relentlessly into the biggest segments
   and never reaches the small sharp anomaly that is the entire point. Ranking by a node's
   own score alone is worse in the other direction — it chases whatever already looks odd
   and never opens the large unremarkable parent a real failure is hiding inside. So half
   the beam goes to the highest magnitude bounds and half to the highest current scores.

**Two guards against confidently wrong answers**, both of which a naive contribution
analysis fails:

- **A noise model.** A slice's value in a period is a random sum: an uncertain *number* of
  transactions, each of an uncertain *size*. Modelling only the sizes would call a segment
  with perfectly uniform prices noiseless when all of its variability is in how many orders
  arrived. The compound-Poisson variance covers both — `Var(Σx) ≈ Σx²` — and needs no extra
  pass. `z = |excess| / sqrt(Σx²(cur) + growth²·Σx²(pri))`.
- **A multiple-comparisons correction.** A 2σ bar applied independently to 300 slices
  produces roughly fifteen "findings" from data with no cause in it at all. That is not a
  corner case, it is what a drill-down does by default. The bar is raised by Bonferroni over
  the number of slices actually tested, and explanations are **labelled** by whether they
  clear it rather than silently dropped — a lead worth checking is still worth showing, it
  just must not be presented as a conclusion.

**The weekday trap.** Weekday is available as a derived dimension but is **off by default**,
for a correctness reason rather than a performance one. Every other dimension has the same
exposure in both periods: April and May both contain all four regions. Weekday does not —
April 2025 has five Tuesdays and May 2025 has four. A perfectly flat business therefore
shows *"Tuesday explains 92% of the decline"*, a calendar artefact wearing the costume of a
root cause, and a spectacularly convincing one. Enabling it is a deliberate choice, and the
engine reports the exact weekday imbalance alongside the result.

**Nothing is invented on the way out — and that is two gates, not one.** The narrative layer
reuses the insights pipeline's `FigureRegistry`: every number the model may state is
registered with its exact value and formula, and the model writes `{{exp1_loss}}` where a
figure belongs. A drill-down narrative that cites an unknown token or types an unbacked digit
is discarded in favour of the deterministic summary — which says less and is provably right.

That gate proves the *numbers*. It says nothing about the *prose*, and a draft can be
arithmetically perfect and still unusable: a live Arabic run came back with a Chinese
conjunction inside an otherwise correct sentence (`… يُشكل 171.4% من التغيير الكلي،尽管 …`).
So `alien_scripts()` applies the same principle to the writing system — the model may only
emit characters from a script that appears in the data it was given, and anything else it
invented mid-sentence. A Chinese supplier name quoted from the customer's own product table
passes; a Chinese conjunction the model reached for does not. Both gates fall back to the
deterministic summary, which exists in **both languages** for the same reason: falling back
to an English paragraph inside an Arabic briefing trades a wrong sentence for a foreign one,
which is not an improvement. All of this matters because this text is what gets pushed to
WhatsApp at 07:00.

**Published, not summarised.** `SearchStats` reports slices evaluated vs. the exhaustive
count, what each pruning rule removed (separating the exact rules from the beam), how many
slices were significance-tested, the corrected threshold, and every column that was *not*
searched with the reason. A drill-down that quietly stopped early would present a partial
answer as a complete one.

`GET/POST /api/v1/projects/{id}/root-cause` (+ `/options`, which builds the UI's controls
from what the dataset actually supports). The Insights page's ranked findings each carry a
**"Which segment?"** button that deep-links here with the metric preselected.

---

### 9. Autonomous Monitoring  (`monitoring/`, `notifications/`, `backend/app/api/v1/monitoring.py`, frontend `monitoring` section)
Everything else in the platform waits to be asked. A report is generated when someone opens
the page; a drill-down runs when someone clicks it. That puts a ceiling on how useful the
system can be, and the ceiling is not technical — it is that the owner has to remember to
look. This removes it.

A **Schedule** says when to look and what matters; `monitoring/runner.py` re-runs the
analytics, compares them against *recorded* history and stated targets, ranks what it finds,
drills into the largest finding with the Root Cause engine, and pushes a briefing to
wherever the owner actually reads things.

```
load data → analytics → decision metrics → figure registry → evidence
                                                 │
                     snapshot history ───────────┤
                     owner's targets ────────────┤
                                                 ▼
                                          ranked findings
                                                 │
                          cooldown + severity floor + cap
                                                 │
                          drill into the largest finding
                                                 │
                                   compose briefing (EN/AR)
                                                 │
                                deliver → record what arrived
```

**Ranking reuses the evidence engine.** `agents/insights/evidence.py` already turns an
analytics payload into findings that each carry an explicit **money at stake**, ordered so a
EGP 40,000 margin leak outranks a weekday curiosity — deterministically, with the editorial
judgement written down as named thresholds instead of left to a model's sense of drama.
Alerts inherit that ordering directly. Building a second alert-specific notion of importance
would have been the obvious move and the wrong one: the platform would then have two
different answers to "what matters most in this business", and a user who saw one on the
Insights page and the other on WhatsApp would be right to trust neither.

Three distinctions the money number alone cannot make, all of which are enforced in
`monitoring/rules.py`:

- **Events vs. exposures.** A THREAT or a LEAK is money moving. A RISK — "your top 10% of
  customers are half your revenue" — is a standing structural exposure whose money at stake
  is the size of the *exposure*, not of a loss. Ranked by share alone it scores CRITICAL, so
  the owner would be told their business is in crisis every single morning about a fact that
  has not changed since they started. Risks are capped at medium, and the briefing's headline
  total sums only events.
- **Operational vs. analytical.** If the last transaction is 400 days old, "revenue fell 60%"
  is a true statement about a period that ended long ago. Data-freshness findings sort above
  everything derived from that data, whatever the money involved.
- **Severity scaled to the business.** 40,000 is an emergency for a corner shop and a
  rounding error for a distributor, so severity is a share of period revenue, not a constant.

**Recorded history, not recomputation.** `MetricSnapshot` stores the headline metrics as
they stood at each run. "Revenue is down 12% on last week" needs *last week's recorded
number* — recomputing it from today's dataset answers a subtly different question every time
the data is re-cleaned, backfilled or re-joined, and the difference surfaces as phantom
alerts nobody can reproduce.

**A cooldown, because monitoring that repeats itself gets muted.** Each finding has a
fingerprint built from the rule and the thing it is about — never from a value — so the same
standing leak is recognised tomorrow at a different number and suppressed. Suppressed
findings are *recorded*, not dropped: "nothing found" and "four findings, all still inside
their cooldown" are different states, and the run history says which.

**Delivery — and why WhatsApp is first among equals.** One `Channel` interface, four
transports: **in-app** (always works, no credentials), **email** (SMTP, no third-party SDK),
**Telegram** (fastest to set up — @BotFather, one message, done), and **WhatsApp**. In Egypt
and the wider MENA SMB market WhatsApp is not one messaging option among several; it is where
business is conducted. An owner who will never sign into a BI dashboard reads WhatsApp within
minutes, in Arabic, on a phone. A three-line briefing arriving there — *"revenue fell 12%
yesterday, 92% of it from one product in one region, here is the link"* — is a different
product from the same analysis waiting behind a login for someone to remember it exists.

The WhatsApp channel supports **both** backends behind one interface: Meta's WhatsApp
Business Cloud API (official, what a real deployment uses, needs business verification) and
Twilio's sandbox (works in five minutes with a test number). That is not indecision — the
production answer and the answer that demonstrably works during a live demonstration are
different answers, and the gap between them is exactly what goes wrong on the day. The
channel also handles WhatsApp's **24-hour window**: free-form text is only permitted inside
a window opened by the recipient, and a scheduled 07:00 briefing is by definition outside it,
so an approved template is used when one is configured and the error message says plainly
which mode was used when it is not.

**Arabic is composed, not translated.** Each ranked finding has a stable `key`, and each key
has an Arabic sentence written *as Arabic business writing* (`monitoring/evidence_ar.py`)
carrying the identical `{{citation}}` tokens as its English counterpart. The registry
substitutes the same computed values into either. So the Arabic briefing is not a translation
of the English briefing — the two are siblings rendered from the same arithmetic, and neither
can drift from the numbers or from each other. A key with no Arabic wording falls back to
English rather than to a machine translation: a sentence in the wrong language is obvious and
harmless, a confidently mistranslated financial claim is neither.

Composing rather than translating also means the *substituted values* have to be Arabic, not
only the sentences around them — and this is where a translated-looking product gives itself
away. Live testing produced `أُنشئ في 09 Aug 2026` and `الإيراد ارتفع … في May 2025`: fluent
Arabic with an English month wedged into it, because `strftime` renders month names through
the process-global C locale and the measure and window labels were built in English before
anyone knew which language would read them. `tools/localize.py` holds the month tables, and
the labels are localised where they are *created* (`agents/rootcause/measures.py`) rather than
where they are displayed, so the figure registry, the API response and the picker in the UI
all agree without any of them re-translating anything.

**No model in the delivery path.** The briefing is assembled from templates over
already-computed figures. This text is pushed at 07:00 whether or not an LLM provider is
reachable, in credit, or having a good day; the root-cause narrative is included when one was
generated and verified earlier, and the briefing is shorter without it, never absent.

**It reads as a report, and shows its working.** Five sections: the lead, *Basis of this
report* (which baseline reading the percentages are measured against, coverage, record count,
currency), *The figures* (prior → current → change per finding, plus the two comparison
windows and their row counts), *Where it came from*, *Everything else flagged*, and *How this
report was produced*. That last section states only claims the system can actually keep —
that no figure was written by a language model, that the narrative was checked against those
same figures, **that the model's draft was rejected when it was**, how many candidate
explanations survive multiple-testing correction, and the line that does the real work:
*figures are measured, causes are inferred*. Stamping the whole thing "100% accurate" would
have covered a provable claim and an unprovable one with the same words.

Two renderings, one set of figures. WhatsApp and Telegram cap a message at 4096 characters,
and the full report exceeds that on a busy run — so truncation would eat the *evidence*
sections first, since they sit lowest, leaving conclusions with nothing backing them. Short
channels therefore get a purpose-built executive summary plus a link (`_compose_summary`),
while email and the in-app inbox get the whole report. A test pins that the summary can never
state a figure the report does not.

Every figure is rendered in the unit it is actually measured in (`monitoring/rules.py:
metric_unit`), and an unrecognised metric falls back to a plain number rather than money —
printing `EGP 31.40` for a gross-margin percentage is a factual error in the one section
whose entire purpose is to be checkable. Standing *exposures* are labelled rather than
printed as bare amounts beside events, because the headline total deliberately excludes them
and an unlabelled figure underneath it reads as an arithmetic mistake in the report.

**Scheduling.** APScheduler, hosted inside the API process by default — `uvicorn` alone is a
complete deployment, with no broker and no second service to remember to start. Celery beat
would have needed Redis, a beat process and a worker, all three alive for a single briefing to
send and all three able to be down without anyone noticing. The `Schedule` table is the source
of truth (not APScheduler's job store, which would be a second record of the same fact that
can disagree — the classic failure being a deleted schedule that keeps firing), reconciled
every 60s. `python -m monitoring.worker` runs the same scheduler standalone for deployments
that want it off the web tier; **running both is safe**, because every run takes a Postgres
advisory lock and a duplicate becomes a skip rather than a double delivery.

The reconciler and the per-schedule jobs live in **two disjoint id namespaces**
(`monitoring:reconcile` vs `monitor:<schedule_id>`), which is a fix wearing the shape of a
convention. Reconciliation removes every job under the schedule prefix that has no row behind
it; when the reconciler shared that prefix it deleted *itself* on its first tick. Nothing
looked broken — the scheduler stayed up and kept firing the jobs it already had — it simply
stopped noticing schedules created or edited afterwards, which surfaces days later as "my new
schedule never fired". A regression test now asserts the reconciler survives its own
reconcile.

**User control is the whole point.** Frequency (daily / weekdays / weekly / monthly / hourly /
raw cron), time, per-schedule IANA timezone, which analyses run, severity floor, cooldown
hours, max alerts per run, the owner's own numeric targets, delivery channels, briefing
language, quiet hours, and whether to send when nothing was found (off by default — a
notification that says "nothing happened" trains people to ignore the channel). **Preview**
runs the identical pipeline with delivery and history switched off and shows the exact
briefing that would have been sent; a preview on a different code path would be a preview of
something else.

`/api/v1/monitoring/*` — schedules, runs, alerts, channels, `channel-types` (the setup forms
are served from the backend so the form and the transport cannot drift apart), and
`/scheduler` for the scheduler's own health, because a monitoring feature whose health is
invisible has the problem it exists to solve.

---

### 10. CRM — customer state  (`agents/crm/`, `backend/app/api/v1/crm.py`, frontend `crm` section)
Every other engine here is a *computation*: you ask a question, it runs, a number reaches the
browser, and the number is gone. A CRM cannot work that way, because the question it exists
to answer — *"was this customer riskier last month than they are today?"* — cannot be
answered by recomputing. Recomputing tells you what today's model thinks about today's data,
which is a different question that happens to produce a similar-looking number. So this is
the platform's first genuinely **stateful** layer: a per-customer, per-snapshot record that
is written once and never recomputed.

Full design record: **[CRM_ARCHITECTURE.md](CRM_ARCHITECTURE.md)**. Build plan and sequencing:
`PILLAR_II_CRM_PLAN.md`.

**Four modules, and one rule that keeps them honest** — `snapshot.py` never touches the
database and `repository.py` never computes anything. That is what makes the compute testable
against a CSV with no Postgres running and the persistence testable against Postgres with no
dataset. It is also why this package does *not* extend `agents/churn/storage.py`, where DDL,
computation and transaction are interleaved in one function.
- `contracts.py` — the vocabulary, the `CustomerRecord` dataclass, and the PII boundary.
  Stdlib-only, so all three layers agree on one idea of what a customer is.
- `snapshot.py` — pure compute: `DataFrame → [CustomerRecord]`. Reuses the churn agent's
  `compute_customer_features` and the marketing agent's `compute_rfm` rather than
  reimplementing RFM arithmetic (`compute_rfm` gained an additive `_rfm_by_customer` map so
  there is exactly one quintile implementation in the codebase).
- `repository.py` — persistence only. Every public function is **total**: it returns a value
  for any input and raises nothing at the caller, because a CRM refresh runs as a side effect
  of a churn analysis and a storage failure must never turn a successful analysis into a 500.
- `service.py` — orchestration; the only module that both computes and persists, and it does
  neither itself.

**Two tables** (`db/crm_models.py`, migration `b3e91c47d208`). `crm_snapshots` is one refresh;
`customer_state` is one customer within it. The split exists for the same reason
`monitor_runs` is separate from `monitor_alerts`: without a run record, *"this project has no
at-risk customers"* and *"the refresh has been failing for six days"* are indistinguishable on
the page, and only one of them is good news. The snapshot therefore stores per-component
status (`rfm` / `churn` / `clv`, each `ok`/`skipped`/`pending` **with a reason**), the
resolved column grain, source row count and history span, and the lifecycle thresholds that
were in force.

**Provenance is the schema.** `customer_state` groups its columns as *identity* (PII),
*observed* (arithmetic over the source data), *derived* (deterministic rules), *predicted*
(model output) and *prioritisation* — computed in that order, and **a later stage never
overwrites an earlier one**. A model output can never silently replace a measured fact.

**The four contracts**, each pinned by a test:
1. **Persistence failure never fails analysis** — failures return
   `SnapshotResult(status="failed", reason=...)`, matching the platform's existing
   `{"status": "skipped", "reason": ...}` convention. `"partial"` is a first-class outcome:
   a snapshot with segments but no churn scores is useful and must not read as an error.
2. **Prompts see IDs, humans see names** — `PII_FIELDS` and `strip_pii()` (which recurses
   through nested dicts/lists and also drops the `_`-prefixed internal keys the rest of the
   codebase already uses). Names are joined back at render time in the browser. This
   generalises the convention `agents/marketing/segmentation.py` established, because an
   underscore prefix does not survive a dataclass serialised at three call sites.
3. **Idempotency by date** — `UNIQUE (project_id, customer_id, snapshot_date)`; re-running a
   captured date deletes and rewrites in one transaction, so a double-click cannot fork a
   customer's timeline and corrected data can still update the day. The snapshot date is the
   last date *in the data*, not the wall-clock date the refresh ran.
4. **Every ranked number states its basis** —
   `value_at_risk = churn_probability × (predicted_clv or monetary)`, with `value_basis`
   carrying which one all the way to the screen. Same principle: `portfolio_summary` returns
   **null, not zero**, when the data has no monetary column — `EGP 0.00` reads as a
   measurement, null reads as "not measurable here".

**Lifecycle stage, not a health score.** New → Growing → Established → Declining → Dormant →
Churned. A hand-weighted 0–100 composite collapses well-founded numbers into an arbitrary one
and has no answer to "why 0.3 on recency?". The thresholds here are derived from the data's
own **median inter-purchase interval** (`dormant = cadence × 1.5`, `churned = cadence × 3`),
because a hardcoded "90 days" is simultaneously too patient for a coffee shop and too
aggressive for an annual renewal. Momentum (Growing/Declining) compares a customer's last 90
days against the rate implied by their *own* history. The resolved thresholds are stored on
the snapshot so the rule is auditable, and when repeat buyers are too few to measure a cadence
it falls back to 60 days and labels the basis `default_no_repeat_buyers`.

**`/api/v1/projects/{id}/crm/*`** — `POST /refresh`, `GET /portfolio` (book-level aggregates
computed in SQL, not by fetching every customer into pandas), `GET /customers` (paginated,
filterable, sortable — sort keys are **whitelisted** because `ORDER BY` cannot be
parameterised, and NULLs sort last in both directions so the least-known customers never head
a list that ranks by what is known), `GET /customers/{customer_id}` (profile + full timeline),
`GET /snapshots`, and `GET /ranking-comparison`.

That last endpoint is the point of the whole layer. On `bloom_and_bean` — same snapshot, same
customers, two rankings — the top 10 by churn probability alone cover **EGP 432**; the top 10
by value at risk cover **EGP 22,127**, with **zero names in common**. Both lists are read from
stored state rather than recomputed, so they provably describe the same customers at the same
moment, which is the entire claim the comparison makes.

**The page** (`frontend/src/components/sections/crm.tsx`, route `(app)/crm/`, hooks in
`frontend/src/lib/queries/crm.ts`, copy in `messages/{en,ar}/crm.json`) — registered on the
same path as every other section: sidebar entry, command palette, deep-linkable route. A
refresh control (horizon + a "score churn" switch, because the observed-and-derived-only
refresh takes ~150 ms against ~8 s with a model fit), five portfolio KPIs, then four tabs,
each answering exactly one question:
- **Portfolio** — distribution by risk tier, lifecycle stage and RFM segment, plus a
  *provenance* panel that reports observed / derived / predicted coverage **separately**.
  That panel is contract 4 made visible: a customer with no CLV is rendered as uncounted,
  never as zero.
- **Customers** — the filterable, sortable, paginated book (the whitelisted sort keys above),
  with CSV export. A row opens the **Customer 360** dialog: RFM scores, SHAP drivers carried
  over from the churn run, and the customer's full snapshot timeline.
- **Prioritisation** — the ranking comparison side by side, with the value delta between the
  two orderings and the count of names a risk-only list would have missed.
- **Snapshots** — refresh history: which component produced values on each run, and whether
  the trigger was a manual refresh or a churn run.

The UI holds to two rules inherited from the contracts rather than invented for the screen:
**null is not zero** (`formatMoney(null)` renders an em-dash, so a project with no monetary
column never shows a currency figure nobody measured), and **PII stops at the browser** —
names render only inside the Customer 360 dialog, matching `contracts.strip_pii()`, which
keeps them out of anything bound for an LLM.

> **Before building the CLV agent (Stage 1), read `CRM_ARCHITECTURE.md` §6.** The day-one
> feasibility gate passed on repeat buyers (74.8%), history (730 days) and multi-product
> orders (47.1%) — but **Gamma-Gamma's independence assumption is violated** on the real
> data: `corr(repeat_txns, avg_order_value) = +0.485` Pearson / +0.507 Spearman, p ≈ 6e-22,
> monotone across frequency deciles, and *not* a multi-line-order artifact
> (`corr(x, lines/order) = −0.018`). Vanilla Gamma-Gamma would systematically under-predict
> exactly the high-frequency customers the CRM exists to protect — and since CLV feeds
> `value_at_risk`, that error lands in the headline ranking.

---

## Copilot — the cross-agent assistant

`agents/copilot/` (`planner.py`, `router.py`, `graph.py`, `tools.py`, `narrate.py`,
`state.py`), `backend/app/api/v1/copilot.py`,
`backend/app/services/{copilot_stream,copilot_sessions}.py`, frontend `copilot` section — a
single natural-language chat box that dispatches to whichever of the analytical agents above
actually answers the question, unifying them into one entry point instead of "pick the right
page yourself." It can run **several agents for one question** and streams its answer
**token-by-token** over Server-Sent Events.

Two endpoints share the same agent logic:
- `POST /copilot/ask` — blocking, single-tool. LangGraph supervisor
  `router → <one tool> → narrate → END` (`graph.py`). Kept for simple/programmatic callers.
- `POST /copilot/ask/stream` — what the UI uses. Server-Sent Events, and multi-step capable.

- **Multi-step planning** (`planner.py`) — one structured-output LLM call (`json_mode=True`)
  decomposes the message into an ordered list of 1–3 tool steps, or a single **clarify /
  follow_up**, validated against a server-side allow-list (`_normalize_plan`: dedupe, cap at
  3, clamp forecast horizon, `follow_up` only when there's a prior turn to explain). Most
  questions still resolve to a single step — the planner *is* the router then, same cost and
  latency. "Forecast next quarter's revenue **and** tell me who's about to churn **and**
  suggest a campaign" runs Forecasting → Churn → Marketing in order. (The blocking `/ask`
  still uses the single-route `router.py`.)
- `tools.py` — one node per route (`_run_visualization`, `_run_insights`,
  `_run_forecasting`, `_run_marketing`, `_run_churn`, `_run_general`), each calling the same
  deterministic engine + LLM layer documented for that agent above — Copilot doesn't
  duplicate any agent's logic, it just routes to it. `run_tool()` dispatches by name for the
  streaming multi-step executor, which runs each step directly (not through LangGraph) so it
  can emit live per-agent progress. `_run_general` answers open numeric lookups ("what's our
  total revenue?") via `backend/app/services/analysis_chat.py: run_grounded_chat()` — see
  [Shared/core components](#sharedcore-components) for the fabrication guard on that function.
- **Narration & synthesis** (`narrate.py`) — a single-tool turn is narrated by
  `plan_narration` (grounded ONLY on that tool's deterministic `raw_payload`, never another
  LLM's already-generated markdown); a multi-step turn is tied together by `plan_synthesis`
  over the **union** of every step's payload — same narrate-only-what-you're-shown discipline,
  and numbers can't cross between tools.
- **Streaming** (`backend/app/services/copilot_stream.py`) — a *sync* generator (Starlette
  drives it in a threadpool) yields SSE frames: `meta` (the whole plan, up front) → `step`
  (per-agent running/done) → `token`* → `done` (the answer plus one artifact per agent:
  figure/table/report/grounding). The narration/synthesis/explain calls are truly
  token-streamed via `tools/llm_client.py: stream_complete()` — same provider-fallback chain
  as `complete()`, except a *mid-stream* provider failure can't fall back (that would splice
  two different answers together). **Stream-everything caveat:** a `general` factual answer is
  verified/corrected *after* the LLM finishes (`run_grounded_chat`'s fabrication guard), so
  its raw draft tokens are never streamed — the already-verified answer is revealed with a
  genuine typewriter instead. No unverified token ever reaches the client.
- **Chat UX** (frontend `copilot` section) — a live multi-agent progress strip, one
  chart/table/report block per agent, and per-answer actions: **Stop** (aborts the stream
  mid-generation via `AbortController`), **Regenerate**, **Copy**, and **Save to Reports**;
  the empty state offers suggested prompts.
- **Follow-up ("explain more")** — `backend/app/services/copilot_sessions.py` caches each
  turn's route + `raw_payload` per conversation. A `follow_up`-routed message skips tool
  execution entirely and re-uses the *same cached payload* plus recent history for a longer,
  unconstrained explanation — no new data is fetched, so a chain of follow-ups keeps
  referring to the same underlying analysis until the user asks something genuinely new.
- **Explain-chart** (`agents/visualization/explain.py: explain_chart()`,
  `backend/app/api/v1/charts.py`) — available on *every* chart rendered through the shared
  `PlotlyChart` component (Insights, Forecasting, Visualization, and Copilot chat charts
  alike, via `frontend/src/components/shared/plotly-chart.tsx`'s "explain this chart"
  button), not just Copilot's own. Grounded the same way as everything else: the prompt is
  built from `describe_figure()`'s structured summary of the actual trace data, never the
  chart's generating code or a live DataFrame.

*(`backend/app/api/v1/assistant.py`'s standalone `/ask` route — calling `run_grounded_chat`
directly — still exists and is documented in the code, but no frontend page currently calls
it; Copilot's `general` route reaches the same underlying function instead. Worth either
wiring `/ask` to a UI or removing it — see [Design notes & known limitations](#design-notes--known-limitations).)*

---

## End-to-end workflow & data flow

```
Create/select a Project (frontend Projects page)
        │
        ▼
Choose a source — Upload Files / Google Sheet / Existing Database (Data Workspace)
        │
        ▼
[ingestion]  each file/tab/table kept separate → dict[str, DataFrame]
        │
        ▼
[schema discovery]  heuristic FK/PK scoring (+ LLM for ambiguous cases;
        │            DB constraints trusted directly for Path B)
        ▼  (HUMAN reviews/approves relationships — confidence-gated)
        │
[planner graph]  profiler (Arabic normalization + deterministic defect detection) → planner
        │            ──►  DatasetProfile + measured findings + a typed cleaning plan
        │        (detectors derive a complete baseline plan; the LLM only reviews it)
        │        (relationship-key columns flagged; key-unsafe operators are skipped)
        ▼  (HUMAN reviews / edits / approves the plan — any table, interactive)
        │
[cleaning graph] coder (fallback only) → executor (typed operators + measured ledger)
        │            → validator (authorization invariants) ──(violation, retry≤2)──┐
        │            ▲──────────────────────────────────────────────────────────────┘
        │        (looped per table, automatically, for the rest of a multi-table project;
        │         a table that fails falls back to a fully deterministic clean)
        ▼
[reconciliation]  normalize approved relationships' key columns identically
        │          across tables; gate on orphan-rate delta
        ▼  (HUMAN clicks "Save to Project")
        │
[db.loader]  DDL (real PK/FK) + bulk COPY  ──►  Postgres schema `project_<slug>`
        │
        ▼
[db.views]  per-agent semantic view (SQL join, fact + its dimensions)
        │
        ▼
[data_manager]  TTL-cached  ──►  backend/app/services/views.py: resolve_view()
        │
        ▼
  ┌─────┴────────┬──────────────┬──────────────┬──────────────┐
  ▼               ▼              ▼              ▼              ▼
Visualization  Insights      Forecasting    Marketing       Churn
(each runs its deterministic engine first, then optionally an LLM narration step)
```

Key mechanism: every downstream router calls `resolve_view(project_id, view_name)`, which
returns `DataManager.get_view(project_id, view_name)` — one flat pandas DataFrame read from
the active project's Postgres schema (cached for 5 minutes). **You must create a project,
bring in data, clean it, and save it first**; every other endpoint returns a clear
`400 This project has no saved data yet` error until then, and the frontend surfaces that as
an inline message rather than a blank/broken page.

---

## Project structure

```
.
├── main.py                     # CLI entry point (cleaning pipeline only, with HITL)
├── docker-compose.yml          # Postgres 16, port 5433, named volume
├── alembic.ini / alembic/      # Migrations for the platform tables ONLY (see db/base.py)
├── CHANGELOG_V3.2.md           # V3.2 production-refactor changelog
├── Generate_Data/
│   ├── generate_sample_data.py            # data/sample.csv (dirty electronics retail)
│   ├── generate_business_data.py          # data/bloom_and_bean_sales*.csv (flat, realistic)
│   ├── generate_multi_table_business_data.py  # data/multi_table_demo/ (3-table star schema)
│   └── generate_enterprise_data.py        # data/nile_retail/ (836k lines, 4 tables, ground truth)
├── requirements.txt
├── pyproject.toml              # ruff + pytest config
├── .env.example                # GROQ/ANTHROPIC/OPENAI/OPENROUTER keys, PG_*, FERNET_KEY, JWT_*
│
├── backend/                     # FastAPI app — imports agents/db/tools/etc. as top-level
│   │                             #   packages (run uvicorn from the repo root)
│   └── app/
│       ├── main.py               # FastAPI(), CORS, startup hook (ensure_platform_tables)
│       ├── core/
│       │   ├── config.py         # Settings dataclass (reads .env)
│       │   └── security.py       # bcrypt hashing, JWT encode/decode
│       ├── api/
│       │   ├── deps.py           # get_db, get_current_user, get_owned_project, and
│       │   │                      #   bind_usage_context — async on purpose, because a sync
│       │   │                      #   dependency sets ContextVars on a threadpool copy
│       │   └── v1/                # one router per domain, all mounted under /api/v1
│       │       ├── auth.py  account.py  projects.py  ingestion.py  pipeline.py
│       │       ├── dashboard.py  insights.py  forecasting.py  marketing.py  churn.py
│       │       ├── rootcause.py  monitoring.py  crm.py (customer state — the stateful layer)
│       │       ├── charts.py (explain-chart)  copilot.py (the live cross-agent assistant)
│       │       ├── settings.py  reports.py  assistant.py (/ask — orphaned, no UI calls it)
│       ├── schemas/               # pydantic request/response models, 1:1 with the routers above
│       └── services/               # json_safe, previews, views (resolve_view), analysis_chat
│                                    #   (shared grounded-chat helper), pipeline_sessions
│                                    #   (in-memory HITL cleaning-session store), copilot_stream
│                                    #   (SSE multi-step generator) + copilot_sessions (follow-up cache)
│                                    #   + usage_recorder (binds LLM calls to the user/project
│                                    #     that caused them, and persists them)
│
├── frontend/                    # Next.js 16 app (App Router)
│   └── src/
│       ├── app/
│       │   ├── (app)/            # authenticated route group — one folder per section (15)
│       │   │   ├── command-center/ projects/ data/ visualization/ insights/ forecasting/
│       │   │   ├── marketing/ churn/ copilot/ reports/ account/ settings/
│       │   │   └── root-cause/ monitoring/ crm/
│       │   ├── api/auth/[...nextauth]/route.ts   # NextAuth Credentials provider
│       │   ├── providers.tsx      # ThemeProvider + LocaleProvider + react-query client
│       │   ├── page.tsx           # public landing page
│       │   └── login/
│       ├── components/
│       │   ├── sections/          # one component per real section — data-workspace.tsx,
│       │   │                       #   relationship-erd.tsx (the React Flow schema graph the
│       │   │                       #   Data page embeds), visualization.tsx, insights.tsx +
│       │   │                       #   insights-audit.tsx (figure provenance / ranking /
│       │   │                       #   "what the data could not answer"), forecasting.tsx,
│       │   │                       #   marketing.tsx, churn.tsx, root-cause.tsx, monitoring.tsx,
│       │   │                       #   crm.tsx (four tabs + the Customer 360 dialog),
│       │   │                       #   copilot.tsx, reports.tsx, account.tsx, settings.tsx,
│       │   │                       #   projects.tsx, command-center.tsx, login.tsx
│       │   ├── landing/            # landing.tsx — the unauthenticated marketing page
│       │   ├── shared/             # app-shell (+ app-shell-client), command-palette,
│       │   │                       #   plotly-chart (incl. the "explain this chart" dialog),
│       │   │                       #   theme-toggle, language-toggle, save-report-button,
│       │   │                       #   session-sync, project-sync, section-route-sync, states,
│       │   │                       #   chat-message + ai-response-card + agent-indicator
│       │   │                       #   (Copilot), pipeline-progress, kpi-card, dashboard-blocks,
│       │   │                       #   providers-manager (the Settings model picker), layout
│       │   ├── theme-provider.tsx  # next-themes wrapper (light/dark, OKLCH tokens)
│       │   ├── locale-provider.tsx # next-intl wrapper + <html lang/dir> — see below
│       │   └── ui/                 # shadcn/ui primitives (+ sparkline, chart)
│       ├── messages/               # i18n bundles — 20 namespaces × 2 locales, one JSON pair
│       │   ├── en/  ar/            #   per section so translation passes never collide
│       │   └── index.ts            # aggregates both bundles for next-intl
│       ├── hooks/                  # use-mobile, use-toast
│       ├── lib/
│       │   ├── api/client.ts       # typed fetch wrapper (apiFetch) + use-api.ts (session token)
│       │   ├── queries/            # one file per domain — react-query hooks calling the backend
│       │   ├── store.ts            # Zustand — UI-only state (active section, active project id,
│       │   │                       #   cross-page artifacts like lastForecastOutputs)
│       │   ├── auth-options.ts     # NextAuth config
│       │   └── auth-secret.ts      # resolves NEXTAUTH_SECRET (see fresh-clone note in Setup)
│       └── middleware.ts           # redirects unauthenticated requests away from (app)
│
├── core/
│   └── state.py                # GraphState TypedDict (cleaning pipeline state)
├── graphs/
│   ├── planner_graph.py        # profiler → planner
│   └── cleaning_graph.py       # coder → executor → validator, with validation failure
│                                #   feeding the repair loop (up to 2 retries)
├── models/
│   ├── profiler_models.py      # ColumnProfile (incl. is_relationship_key) / DatasetProfile
│   │                            #   (+ `defects`: the measured findings)
│   └── cleaning_ops.py          # CleaningStep / OperatorSpec (the may_* authorization flags)
│                                #   / StepLedger / CleaningLedger — the cleaning contracts
│
├── db/                          # Postgres platform tables, DDL, storage, views — Stage 0/7/8/11
│   ├── base.py                  # Declarative base (Alembic-managed platform tables only)
│   ├── auth_models.py            # User, ApiKey
│   ├── platform_models.py       # Project, Relationship, ConnectionConfig
│   ├── report_models.py          # Report (saved Insights/Forecast/Marketing/Churn narratives)
│   ├── monitoring_models.py      # Schedule, MonitorRun, Alert, NotificationChannel,
│   │                              #   DeliveryLog, MetricSnapshot
│   ├── crm_models.py             # CrmSnapshot (one refresh) + CustomerState (one customer
│   │                              #   within it) — the platform's only stateful customer layer
│   ├── usage_models.py            # LlmUsageEvent — one row per provider *attempt* (a
│   │                              #   fallback to the second provider is two rows), tokens
│   │                              #   only, no cost column (see limitations)
│   ├── session.py / projects.py / reports.py / init_platform.py
│   ├── ddl.py                   # pandas dtype → PG type, PK/FK DDL, topological ordering,
│   │                             #   sanitize_identifier/sanitize_schema_columns
│   ├── loader.py                # CREATE SCHEMA + CREATE TABLE + bulk COPY load
│   ├── reflect.py                # MetaData().reflect() (plain SQLAlchemy Core, not automap)
│   ├── schema_evolution.py      # minimal ALTER TABLE ADD COLUMN diffing on re-upload
│   ├── views.py                 # per-agent semantic views (fact + joined dimensions)
│   └── connection_configs.py    # Fernet-encrypted credential storage (Path B / C)
├── data_manager/
│   └── manager.py               # DataManager — TTL-cached wrapper around db/views.py
├── ingestion/                    # Multi-source ingestion — Path A/B/C converge here
│   ├── multi_table.py           # Path A: files kept as separate tables (no collapsing)
│   ├── gsheets.py                # Path C: Google Sheets via a GCP service account
│   └── db_link.py                # Path B: ReadOnlyConnector, FK introspection, sampling
├── schema_discovery/              # Heuristic FK/PK scoring + LLM escalation
├── relationships/
│   └── review.py                 # Confidence-gated HITL bucketing + persistence
├── reconciliation/
│   ├── normalize.py               # numeric-aware key normalization
│   └── orphans.py                 # before/after orphan-rate checks + tolerance gate
│
├── tools/
│   ├── ingestion.py             # multi-format loader + legacy combination planner (unused
│   │                             #   by the main flow now, still tested)
│   ├── arabic_text.py            # Arabic detection / normalization / placeholders / stopwords
│   ├── sandbox.py                 # restricted exec() for LLM code; run_with_timeout(); figure/df capture
│   ├── db_tools.py                 # legacy flat Postgres helpers (still used for agent run-history storage)
│   ├── profiler_tools.py          # pandas logic for building DatasetProfile
│   ├── cleaning_ops.py             # THE cleaning execution layer: 21 typed operators, the
│   │                                #   registry + param validation, order_steps(), and
│   │                                #   apply_step/apply_plan (which MEASURE the ledger by
│   │                                #   diffing, rather than trusting an operator's word)
│   ├── defect_detection.py         # 19 deterministic data-quality checks -> DefectFinding,
│   │                                #   plus plan_from_defects(): a complete typed cleaning
│   │                                #   plan derived with no LLM involved at all
│   ├── localize.py                 # month names / timestamps per language — so an Arabic
│   │                                #   sentence never renders an English month via strftime
│   ├── llm_client.py               # multi-provider LLM client (Groq→Anthropic→OpenAI→OpenRouter)
│   ├── llm_provider_models.py      # per-purpose model choice for the non-Groq providers
│   └── llm_usage.py                # UsageEvent + a ContextVar sink the client pushes to, so
│                                    #   metering needed no change at any of the 23 call sites
│
├── agents/
│   ├── constants.py             # AVAILABLE_MODELS + the 11 DEFAULT_*_MODEL entries +
│   │                             #   DEFAULT_HORIZONS. tests/test_constants.py pins that every
│   │                             #   default is selectable and that no decommissioned Groq
│   │                             #   model is still offered in the picker
│   ├── cleaning/                # profiler, planner, coder (fallback only), executor,
│   │                             #   validator, invariants (the authorization gate), and
│   │                             #   multi_table (orchestration + reconciliation +
│   │                             #   clean_table_deterministically, the no-LLM floor)
│   ├── analytics/              # schema_intel, kpi_engine, timeseries, quality, engine
│   ├── visualization/          # builder (deterministic dashboard), theme, export,
│   │                           #   viz_graph, dashboard (AI charts), explain (explain-chart),
│   │                           #   viz_models
│   ├── insights/               # agent, evidence (the ranking engine alerts also use),
│   │                           #   figures (citation registry), decision_metrics,
│   │                           #   strict_verify, template_selector
│   ├── forecasting/            # pipeline, graph, nodes, validation, metric_discovery,
│   │   │                       #   interpreter, report, schema, storage, forecast_models
│   │   └── tools/              # models (incl. SARIMA), backtest (rolling-origin, 1-SE rule),
│   │                           #   engines, evaluation (MASE/RMSSE), conformal, anomalies,
│   │                           #   diagnostics, preparation, transform,
│   │                           #   calendar_events (Hijri regressors — NOT wired in)
│   ├── marketing/              # segmentation, engine, agent, storage
│   ├── churn/                  # features, model (incl. SHAP), engine, agent, storage
│   ├── crm/                    # contracts (vocabulary + CustomerRecord + the PII boundary),
│   │                           #   snapshot (PURE compute), repository (PERSIST only),
│   │                           #   service (orchestration) — no LLM, no FastAPI import
│   ├── copilot/                 # planner (multi-step), router, graph, tools, narrate, state —
│   │                             #   the cross-agent supervisor (see Copilot section above)
│   ├── rootcause/              # dimensions (what may be sliced, and what may not),
│   │                            #   measures (what is being explained, over which windows),
│   │                            #   search (the beam search + the two exact pruning bounds +
│   │                            #   the noise model), figures/narrate (citation-token prose),
│   │                            #   engine (orchestrator), schema (published contracts)
│   └── reporting/             # grounding (faithfulness check), export (HTML/PDF)
│
├── monitoring/                  # the autonomous analyst — Pillar III-1
│   ├── runner.py                #   one scheduled run, start to finish (advisory-locked)
│   ├── rules.py                 #   what is worth saying: evidence engine + snapshot deltas
│   │                             #   + the owner's targets + freshness + quality
│   ├── briefing.py              #   the message itself, composed EN/AR, no model in the path
│   ├── evidence_ar.py           #   Arabic findings carrying the same {{citation}} tokens
│   ├── history.py               #   MetricSnapshot capture — recorded, never recomputed
│   ├── scheduler.py             #   APScheduler; the Schedule table stays the source of truth
│   └── worker.py                #   `python -m monitoring.worker` — the same scheduler, standalone
│
├── notifications/               # delivery channels behind one Channel interface
│   ├── base.py                  #   Message / DeliveryResult, markdown flattening, safe truncation
│   ├── inapp_channel.py         #   always works, no credentials
│   ├── email_channel.py         #   SMTP (stdlib — no third-party email SDK)
│   ├── telegram_channel.py      #   Bot API
│   ├── whatsapp_channel.py      #   Meta Cloud API + Twilio, and the 24-hour template rule
│   └── registry.py              #   construction + the setup-form contract the UI renders from
│
├── prompts/
│   ├── planner_prompt.txt  coder_prompt.txt  repair_prompt.txt
│   ├── insights/   non_technical.md  executive.md  detailed.md
│   ├── marketing/  strategy.md  campaigns.md  ad_copy.md
│   └── churn/      strategy.md
│
├── data/                       # sample CSVs, Bloom & Bean (flat + multi-table), Nile Retail (836k rows)
└── tests/                      # pytest suite; conftest.py auto-skips @pytest.mark.integration
                                 #   tests when the live Postgres isn't reachable
```

---

## Shared/core components

- **`core/state.py: GraphState`** — the typed state passed between cleaning-pipeline nodes
  (raw_df, planner_model, coder_model, key_columns, metadata, cleaning_plan, generated_code,
  execution_result, validation_report, transformation_log, retry_count, last_error).
  `execution_result` also carries the measured `ledger`, and `validation_report` carries
  structured `violations`/`warnings` plus the `repair_feedback` the retry loop uses.
- **`tools/cleaning_ops.py`** — the cleaning execution layer. `REGISTRY` maps 21 operator
  names to an `OperatorSpec` (parameter schema + the `may_*` authorization flags) and a pure
  function. `validate_step()` rejects a bad step before it runs; `order_steps()` puts any
  plan into safe execution order; `apply_step()` runs one operator and **measures** what it
  did by diffing; `apply_plan()` chains them into a `CleaningLedger`. `KEY_SAFE_OPS` is the
  subset permitted to touch a cross-table join key.
- **`tools/defect_detection.py`** — `detect_defects(df, key_columns=...)` runs all 19
  deterministic quality check and returns measured findings; `plan_from_defects()` turns them
  into a complete typed plan. This is what makes the pipeline work with no LLM at all.
- **`agents/cleaning/invariants.py`** — `check_invariants(raw, clean, plan, ledger, keys)`.
  Derives what the plan authorized and rejects anything else: unauthorized value changes,
  null fills or null introductions, column drops/adds, row additions, non-duplicate row
  deletions, dtype changes, join-key modification, and key-uniqueness regressions.
- **`tools/sandbox.py`** — `run_analysis(code, df)` executes LLM-generated Python with a
  restricted `__builtins__`, a 30s timeout, 1 MB output cap, and automatic capture of a
  Plotly `fig`, a modified `df`, and stdout. Also `df_context()` (LLM-friendly schema text)
  and `sanitize_fig()` (JSON-safe Plotly).
- **`tools/db_tools.py`** — `is_pg_configured()`, `store_df_to_pg()`, `load_df_from_pg()`,
  `get_schema_info()`; identifiers validated against SQL-injection. Still used for each
  agent's own run-history storage (`agents/*/storage.py`) and by `db/session.py` for the
  connection string — the schema-per-project data path itself goes through `db/loader.py`.
- **`tools/llm_client.py`** — `complete(purpose, messages, model=None, ...)`: tries Groq (the
  caller's literal model choice — the frontend's per-agent Settings selector), then
  Anthropic/OpenAI/OpenRouter (fixed per-purpose models from `tools/llm_provider_models.py`)
  in order, logging which provider served each call. `any_provider_configured()` /
  `provider_status()` back the Settings page's provider display.
- **`backend/app/services/views.py: resolve_view(project_id, view_name)`** — the FastAPI
  equivalent of the old `tools/page_data.py`: every router calls this to get its DataFrame,
  raising a clear `400`/`502` instead of a page-level "no active project" message.
- **`agents/analytics/schema_intel.py`** — the column-role detector that makes the whole
  platform "configuration-free".
- **`agents/reporting/`** — the shared report layer used by every narrative-producing agent:
  - `grounding.py` — `check_grounding(report, *payloads)` extracts the material figures
    (currency, percentages, large numbers) from a report and verifies each against the
    deterministic payload, returning a coverage score and the list of unmatched figures.
    Handles fraction-vs-percent (`0.083` ↔ `8.3%`), magnitude suffixes (`$1.2M`), and
    sensible rounding; ignores structural counts and years. Pure stdlib, unit-tested.
  - `export.py` — `build_html_report(...)` and a dependency-free `markdown_to_html(...)`
    that produce the styled, print-ready HTML deliverable.
  - The frontend renders reports directly from `report_md` (React Markdown) instead of via a
    server-side HTML template; `GET /api/v1/projects/{id}/dashboard/export-html` still serves
    the standalone HTML export for the main dashboard specifically.
- **`backend/app/services/analysis_chat.py: run_grounded_chat(...)`** — the shared "answer a
  question, optionally executing generated Python against a DataFrame in the sandbox"
  pattern, reused by the Insights report follow-up chat and Copilot's `general` route (see
  [Copilot](#copilot--the-cross-agent-assistant)). **Fabrication guard**: `df_context()`
  only gives the LLM schema + head(5) + `describe()` — no exact sums — so a model answering
  a numeric question without writing code could previously state a number it never actually
  computed. `_ground_or_correct()` now runs `agents/reporting/grounding.py::check_grounding()`
  on the draft answer against the real sandbox stdout; only when a genuine, material
  mismatch is found does it pay for one corrective LLM re-ask (a clean or already-grounded
  answer costs nothing extra). The response now includes a `grounded: bool`. Marketing's
  own chat (`backend/app/api/v1/marketing.py: marketing_chat`) does **not** go through this
  helper — it's a direct `complete()` call grounded by injecting the report + payload into
  the prompt instead, so it doesn't get the same post-hoc fabrication check.
- **`backend/app/services/pipeline_sessions.py`** — an in-memory, TTL-based store for the
  multi-step HITL Data Cleaning workflow, keyed by a `pipeline_session_id` the frontend holds
  between steps. Mirrors what `st.session_state` gave the old Streamlit page for free; see
  [Design notes & known limitations](#design-notes--known-limitations) for why this is
  single-process only.
- **`frontend/src/components/locale-provider.tsx` + `frontend/src/messages/`** — the whole UI
  is bilingual English/Arabic, and the split mirrors how the backend already handles language:
  each section owns its **own** `{en,ar}/<section>.json` pair (20 namespaces per locale,
  aggregated in `messages/index.ts`), so two independent translation passes never touch the
  same file and a missing key is scoped to one screen rather than blanking the app.
  `LocaleProvider` wraps next-intl, persists the choice in `localStorage`, and sets
  `document.documentElement.dir` to `rtl` for Arabic — which is why layout code uses Tailwind's
  **logical** properties (`ms-`/`me-`, `ps-`/`pe-`, `text-start`/`text-end`) rather than
  left/right: one stylesheet then serves both directions with no mirrored variants to maintain.
  It renders the SSR-safe `en` default first and upgrades post-mount **on purpose**: reading
  `localStorage` in the `useState` initializer would make the client's first render disagree
  with the server's on actual translated text, which `suppressHydrationWarning` cannot paper
  over the way it can for an attribute. Arabic prose that carries *numbers* is composed, not
  translated — see `monitoring/evidence_ar.py` and `tools/localize.py`.
- **`frontend/src/components/theme-provider.tsx`** — next-themes over the OKLCH custom
  properties in `globals.css`, plus the 12-step type scale that **overrides** Tailwind's
  defaults (so shadcn's `text-sm` and a section's `text-sm` are the same size) and is enforced
  by a `no-restricted-syntax` ESLint rule rejecting `text-[13px]`. Plotly cannot read
  `oklch()`, so `agents/visualization/theme.py` re-themes charts with hex/rgba separately.

---

## Setup & installation

```bash
# 1. (recommended) create a virtual environment
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

# 2. install backend dependencies
pip install -r requirements.txt

# 3. configure backend secrets
cp .env.example .env        # then edit .env and add your API key(s) + JWT secret

# 4. start PostgreSQL (required — Projects, storage, and the Data Manager all need it)
docker compose up -d

# 5. apply platform-table migrations (users, projects, reports, api_keys, ...)
alembic upgrade head

# 6. install frontend dependencies
cd frontend
npm install     # or: bun install, if you have bun
cd ..
```

> npm is the assumed toolchain — nothing in the project needs bun, and every script
> (`dev`, `build`, `start`, `setup`) runs on plain Node. bun works too if you already have it,
> but `bun install` on a machine without it fails with
> `The term 'bun' is not recognized...`, which is a missing tool rather than a broken checkout.

`frontend/.env.local` needs no manual step: `npm run dev` (and `npm run build`) run
`scripts/setup-env.mjs` first, which creates the file from `.env.local.example` on a fresh
clone and generates a real `NEXTAUTH_SECRET` if it is missing or blank. It never overwrites a
value you have already set, so it is safe to re-run — `npm run setup` invokes it on its own if
you want to prepare the file before starting anything.

`.env` keys (backend):
```
GROQ_API_KEY=your_groq_api_key_here      # primary LLM provider (get one at console.groq.com)

# Optional fallback providers — any provider without a key here is skipped automatically
LLM_PROVIDER_ORDER=groq,anthropic,openai,openrouter
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
OPENROUTER_API_KEY=

# PostgreSQL — used by both docker-compose (to configure the container) and the app.
# Port defaults to 5433, not 5432, in case a native Postgres already owns 5432.
PG_HOST=localhost
PG_PORT=5433
PG_DBNAME=smart_analyst
PG_USER=postgres
PG_PASSWORD=postgres

# Fernet key for encrypting stored external-database credentials (Path B). Generate with:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
FERNET_KEY=

# JWT auth (backend/app/core/security.py). Generate a secret with:
#   python -c "import secrets; print(secrets.token_urlsafe(48))"
# JWT_ALGORITHM defaults to HS256 in code and doesn't need to be set here.
JWT_SECRET_KEY=
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=60
JWT_REFRESH_TOKEN_EXPIRE_DAYS=30

# CORS — must match the frontend's origin
FRONTEND_ORIGIN=http://localhost:3000

# --- Scheduled monitoring & proactive alerts ---
# The scheduler is hosted in the API process by default; set to 0 to host it in
# `python -m monitoring.worker` instead. Running both is safe (advisory-locked).
MONITORING_SCHEDULER=1
# Base URL for the "open the full analysis" link inside briefings. Falls back to
# FRONTEND_ORIGIN; set it when the public URL differs (a tunnel, a reverse proxy).
APP_PUBLIC_URL=http://localhost:3000

# Notification channels. Each of these can ALSO be stored per channel in the UI,
# Fernet-encrypted with FERNET_KEY; these env vars are the single-tenant shortcut
# so a deployment can configure delivery once instead of per user.
SMTP_HOST=          SMTP_PORT=587       SMTP_USERNAME=      SMTP_PASSWORD=
SMTP_FROM=          SMTP_FROM_NAME=DAAS
TELEGRAM_BOT_TOKEN=                      # @BotFather — the fastest channel to get working

# WhatsApp: "meta" = the official Cloud API (production), "twilio" = the sandbox
# that works in minutes without business verification (demos).
WHATSAPP_PROVIDER=meta
WHATSAPP_ACCESS_TOKEN=                   WHATSAPP_PHONE_NUMBER_ID=
# Scheduled briefings normally fall OUTSIDE WhatsApp's 24-hour free-form window,
# so an approved template name is required for reliable delivery.
WHATSAPP_TEMPLATE_NAME=                  WHATSAPP_TEMPLATE_LANGUAGE=
TWILIO_ACCOUNT_SID=   TWILIO_AUTH_TOKEN=   TWILIO_WHATSAPP_FROM=
```

`frontend/.env.local` keys:
```
NEXT_PUBLIC_API_URL=http://localhost:8000   # the FastAPI backend above

NEXTAUTH_URL=http://localhost:3000
NEXTAUTH_SECRET=                             # auto-generated by `npm run dev` if left blank

# Optional — the login page's Google/GitHub SSO buttons only render once the
# corresponding pair below is set. Leave both blank to hide them entirely.
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GITHUB_CLIENT_ID=
GITHUB_CLIENT_SECRET=
```

> **`NEXTAUTH_SECRET` must actually be set** — this used to be the single most common way to
> end up with a frontend that looks fine until the moment someone tries to sign in, and it bit
> every fresh clone on a new machine. `.env.local` is gitignored, so a new checkout has no
> secret at all; copying the example produced a *blank* `NEXTAUTH_SECRET=`, which is worse than
> a missing one, because NextAuth resolves the value with `??` and an empty string counts as
> set. Every `POST /api/auth/callback/credentials` then died inside HKDF with
> `TypeError: "ikm" must be at least one byte in length`, and because `signIn()` assumes that
> route always returns JSON, the promise rejected and the button span forever — or, when it did
> surface, reported a server misconfiguration as "Incorrect email or password".
>
> Three things now prevent that, so a fresh clone signs in with no setup step:
> 1. `scripts/setup-env.mjs` runs before `next dev`/`next build` and guarantees a real secret
>    exists in `frontend/.env.local` (idempotent — an existing value is never touched).
> 2. `src/lib/auth-secret.ts` normalises a blank secret back to "unset" for both the route
>    handler and `middleware.ts`, falling back to a fixed development secret and refusing to
>    start in production. It is the single source both sign and verify against.
> 3. The login form treats a rejected `signIn()` as a `Configuration` error, so a
>    misconfiguration says so instead of blaming the password or hanging.

> **"Cannot reach the API server at http://…:8000"** with uvicorn plainly running is usually
> the IPv4/IPv6 loopback split, not a dead backend. `uvicorn --port 8000` binds **127.0.0.1
> only**, while `localhost` resolves to `::1` first on a dual-stack machine, so the first
> connection attempt goes to an address nothing is listening on. Current Node and Chrome retry
> on the other family and hide it; where that retry doesn't happen, the request just fails.
> `NEXT_PUBLIC_API_URL` therefore defaults to `http://127.0.0.1:8000` — the literal keeps the
> client and the bind from disagreeing. Note `--host ::` is *not* the fix: on Windows it binds
> IPv6 only and then refuses 127.0.0.1, moving the problem rather than solving it.

> **Every API call failing with `OPTIONS ... 400 Bad Request`** means the CORS *preflight* was
> refused, so no request ever reaches a route — the UI loads but nothing in it works. Starlette
> refuses a preflight for one of four reasons (origin, method, headers, private-network) and
> puts the reason in a response body the browser never shows, which is why this reads as a
> mystery. The API now logs the reason itself: look for `CORS preflight REJECTED` in the uvicorn
> output, which prints the exact `Origin` the browser sent and what is currently allowed.
>
> Allowed out of the box: any port on `localhost`, `127.0.0.1`, or `[::1]`, plus Chrome's
> Private Network Access preflights. If the log shows any other origin — a LAN IP for a phone
> demo, an ngrok/tunnel hostname — add that exact origin to `FRONTEND_ORIGIN` in `.env`
> (comma-separated) and restart the API. Note `[::1]` and `localhost` are *different origin
> strings* even though they are the same machine, which is one way this failed on one laptop
> and not another.

> Note: `prophet` (and optionally `statsmodels`) are scientific packages; on some systems the
> first install compiles native code and can take a few minutes.

> Note: if this is a fresh Postgres volume, `alembic upgrade head` is the explicit way to
> create the platform tables (`users`, `projects`, `relationships`, `connection_configs`,
> `reports`, `api_keys`). The backend's startup hook (`db/init_platform.py`) also calls
> `create_all` defensively so a first run without migrations still works, but running
> `alembic upgrade head` is the supported path (it's the only one Alembic will track
> revisions against going forward).

---

## Running the project

```bash
# 0. Make sure Postgres is up and migrations are applied
docker compose up -d
alembic upgrade head

# 1. Start the backend — from the REPO ROOT (so agents/db/tools resolve as top-level
#    packages, exactly like pytest already expects)
uvicorn backend.app.main:app --reload --port 8000

# 2. In a second terminal, start the frontend
cd frontend
npm run dev     # or: bun dev, if you have bun
# -> http://localhost:3000

# For a production build instead of the dev server (plain Node, no bun required):
#   npm run build && npm start

# The scheduled-monitoring scheduler is hosted INSIDE the API process by default, so the
# two commands above are already a complete deployment — no broker, no third service.
# To host it separately instead (a web tier that restarts often, or several API replicas),
# set MONITORING_SCHEDULER=0 in .env and run:
python -m monitoring.worker
# Running both is safe: every run takes a Postgres advisory lock, so a duplicate is skipped
# rather than delivered twice.

# Run the cleaning pipeline only, from the terminal (uses data/sample.csv, no web UI).
# Prints the typed plan for review — [enter] to approve, "d 2,5" to drop steps, "q" to quit.
python main.py

# Generate test datasets
python Generate_Data/generate_sample_data.py            # -> data/sample.csv (dirty electronics retail)
python Generate_Data/generate_business_data.py          # -> data/bloom_and_bean_sales*.csv (flat, realistic)
python Generate_Data/generate_multi_table_business_data.py  # -> data/multi_table_demo/ (3-table star schema)
python Generate_Data/generate_enterprise_data.py        # -> data/nile_retail/ (836k lines, 4 tables, ground truth)
python Generate_Data/generate_enterprise_data.py --scale 0.05   # same shape, ~5% of the rows, for a quick run

# Run the backend test suite (integration tests needing Postgres auto-skip if it isn't running)
python -m pytest -q

# Frontend checks
cd frontend
npx tsc --noEmit && npx eslint . && npx next build
```

**Typical session:** `docker compose up -d` → `alembic upgrade head` →
`uvicorn backend.app.main:app --reload --port 8000` → `cd frontend && bun dev` → open
`http://localhost:3000` → **register/sign in** → create a **Project** → **Data Workspace** →
upload `data/multi_table_demo/{customers,products,orders}.csv` (or the flat
`data/bloom_and_bean_sales.csv` for the single-table path) → review relationships → review/
approve the cleaning plan → **Save to Project** → explore **Visualization / Insights /
Forecasting / Marketing / Churn**.

---

## Configuration

- **LLM providers** — set `GROQ_API_KEY` (and optionally `ANTHROPIC_API_KEY` /
  `OPENAI_API_KEY` / `OPENROUTER_API_KEY`) in the backend's `.env` for automatic fallback
  (order via `LLM_PROVIDER_ORDER`). Provider keys are a **server-side secret only** — there is
  no UI to add or edit them; the frontend's Settings page shows configured/not-configured
  status per provider (`GET /api/v1/settings/providers`) and nothing more. Only Groq honors a
  per-agent model override — the fallback providers use a fixed model per purpose
  (`tools/llm_provider_models.py`).
- **Per-agent models** — frontend → **Settings** (`components/shared/providers-manager.tsx`).
  Each of the eleven agent purposes (planner, coder, viz, dashboard, insights, forecast,
  marketing, churn, schema_discovery, copilot, explain_chart — the list lives in
  `backend/app/api/v1/settings.py`) has its own selector, persisted per-user in Postgres
  (`users.model_preferences`, `GET/PATCH /api/v1/settings/models`) — not a session-only
  setting. Available models and defaults live in `agents/constants.py`, and a PATCH with a
  model outside `AVAILABLE_MODELS` is rejected rather than stored.

  **The catalogue is a moving target.** Groq decommissioned `llama-3.3-70b-versatile`,
  `llama-3.1-8b-instant`, `mixtral-8x7b-32768`, `llama-4-scout` and `qwen3-32b` on 2026-08-16,
  which had been the default for eight of the eleven purposes — every one of those agents would
  have failed on its next call. All eleven now default to **`openai/gpt-oss-120b`**, and the
  selectable list is `openai/gpt-oss-120b`, `openai/gpt-oss-20b`, `qwen/qwen3.6-27b`.
  `tests/test_constants.py` pins the invariant that made the outage silent in the first place:
  every `DEFAULT_*_MODEL` must be a member of `AVAILABLE_MODELS`, and no decommissioned id may
  reappear in it. Read the `qwen/qwen3.6-27b` caveat in
  [Design notes & known limitations](#design-notes--known-limitations) before selecting it.
- **PostgreSQL** — `docker compose up -d` (see `docker-compose.yml`, port 5433). Required for
  Projects, storage, and every agent endpoint; the ingestion/cleaning steps before "Save to
  Project" work without it (they live in the in-memory pipeline-session store), but nothing
  can be persisted or read back until it's up.
- **Projects** — frontend → **Projects**, or the sidebar project switcher. Switch between
  projects or create a new one; each gets its own Postgres schema (`project_<slug>`) and its
  own owner (`projects.owner_id`) — one user cannot see another user's projects.
- **FERNET_KEY** — encrypts every stored third-party credential at rest: Path B/C connection
  details (`db/connection_configs.py`) **and** notification-channel secrets — the Telegram bot
  token, SMTP password, WhatsApp access token saved through the Monitoring UI
  (`notifications/registry.py` decrypts through the same helpers). So it is required for
  scheduled delivery, not only for linking an existing database. One key, no versioning: see
  the limitations section before relying on it in production.
- **JWT_SECRET_KEY** — signs access/refresh tokens; changing it invalidates every existing
  session (users just log in again — there's no other persisted session state to clean up).

---

## Datasets

| File | Description |
|------|-------------|
| `data/sample.csv` | Dirty electronics-retail sample (from `generate_sample_data.py`) |
| `data/bloom_and_bean_sales.csv` | Single-table path — realistic, dirty coffee-store sales (24 months, repeat customers, trend + seasonality, profit/cost). Upload this to Data Cleaning. |
| `data/bloom_and_bean_sales_clean.csv` | Clean reference version of the flat file |
| `data/README_bloom_and_bean.md` | Data dictionary + which patterns each agent should find |
| `data/multi_table_demo/{customers,products,orders}.csv` | **Recommended for testing multi-source ingestion** — the same Bloom & Bean business, normalized into a real 3-table star schema (`orders` fact table referencing `customers` and `products`). Upload all 3 together. |
| `data/multi_table_demo/clean/` | Clean reference versions of all three tables |
| `data/multi_table_demo/README.md` | Schema diagram + what Schema Discovery/Cleaning/Reconciliation should each find |
| `data/cafe_sales.csv`, `data/dirty_cafe_sales.csv` | Additional test files |
| `data/nile_retail/{customers,products,stores,orders}.csv` | **Scale + correctness dataset** — "Nile Retail Co.", an Egyptian omnichannel retailer: **836,630 order lines**, 70,000 customers, 900 products, 14 stores, 3.6 years ending yesterday. A 4-table schema where `orders` is line-grain with direct FKs to all three dimensions. |
| `data/nile_retail/ground_truth.json` | The dataset's **measured** ground truth — revenue, order counts, the injected decline and its cause, outage coverage, Pareto share. Written by re-measuring the generated output, not by restating the generator's inputs. |

> **`data/nile_retail/` is not in the repository** — it is ~120 MB and `orders.csv` alone
> exceeds GitHub's 100 MB hard limit, so it is gitignored and reproduced on demand:
> `python Generate_Data/generate_enterprise_data.py`. The generator is seeded, so the
> `ground_truth.json` the docs quote is the one you will get. Every other file in the table is
> checked in.

**Nile Retail Co.** exists for the two things the Bloom & Bean files cannot answer: does the
platform hold up at realistic scale, and does it get the *right* answer. On scale, the whole
analytics path pulls the joined 836k-row fact table into pandas with no sampling — the view
builds in ~18s, `run_analytics` in ~5s, churn in ~23s, a full monitoring run in ~64s. On
correctness, every interesting pattern is injected deliberately (a two-week supply outage, a
+12% price rise, a marketing burst, Ramadan/Eid and Black Friday peaks, a recent category
collapse) and then **re-measured on the generated result** into `ground_truth.json`, so a
claim by any agent can be checked against a known answer rather than eyeballed. The business
is Egyptian on purpose — Friday/Saturday weekends, Hijri-calendar demand, cash-on-delivery
and governorate geography are the conditions this product targets, and none of them appear in
a US-shaped dataset.

The **Bloom & Bean** generators are deliberately designed so every agent has something to
find: duplicates/outliers/bad-dates for cleaning, KPIs + a strong repeat rate for analytics,
anomalies (Black Friday spikes, a supply-outage dip) for time-series, a forecastable daily
series, a full RFM segment spread for marketing, and a strong recency signal for churn. The
multi-table version additionally seeds small, deliberate key-formatting drift (e.g.
`"SKU-1019"` vs `"sku-1019"`) between tables — real enough that independent per-table
cleaning could plausibly make it worse, which is exactly what Reconciliation exists to catch.

---

## How to add a new agent (worked example)

The **Churn agent** is the cleanest template to copy. To add an agent `foo`:

1. **Create the package** `agents/foo/`:
   - `features.py` / `engine.py` — the **deterministic** computation. Reuse
     `agents.analytics.schema_intel.build_schema_summary(df)` to find column roles. Return a
     JSON-serialisable `payload` dict (this is what the LLM and API consume).
   - `model.py` — any ML (scikit-learn etc.), if applicable.
   - `agent.py` — the **LLM layer**. Load a prompt from `prompts/foo/`, pass the deterministic
     payload as grounded context, call `tools.llm_client.complete("foo", messages, model=model)`
     where `model` is a plain function parameter (not read from any global state) — the
     caller resolves it from the request or the user's stored preference
     (add a `"foo"` purpose to `tools/llm_provider_models.py` for the non-Groq fallback models,
     and to `backend/app/api/v1/settings.py`'s `_PURPOSES` list so it shows up in Settings).
   - `storage.py` — optional PostgreSQL persistence. **Mirror `agents/crm/`, not
     `agents/churn/storage.py`**: declare the tables as SQLAlchemy models in `db/foo_models.py`
     (registered in `db/init_platform.py` *and* `alembic/env.py`) with an Alembic migration,
     and keep the persistence module free of computation. `agents/churn/storage.py` is the
     older pattern — raw `CREATE TABLE IF NOT EXISTS` strings interleaved with the write — and
     it is unmanaged by migrations. If your model declares a `relationship()` naming another
     class by string (e.g. `"Project"`), add `from db import platform_models  # noqa: F401` at
     the top of the module, or importing it standalone fails at mapper-configure time.
2. **Add the prompt** `prompts/foo/strategy.md` (instruct the model to use only payload numbers).
3. **Register the model**: add `DEFAULT_FOO_MODEL` to `agents/constants.py`.
4. **Wire the backend** (mirror `backend/app/api/v1/churn.py`):
   - `backend/app/schemas/foo.py` — pydantic request/response models.
   - `backend/app/api/v1/foo.py` — `router = APIRouter(prefix="/projects/{project_id}/foo")`;
     resolve the DataFrame via `resolve_view(project.id, "customer_360")` (or add a new view
     name to `db/views.py` + `data_manager/manager.py` if `foo` needs a different join), call
     the engine, then the agent layer, return the response model. Resolve the LLM model as
     `payload.model or current_user.model_preferences.get("foo") or DEFAULT_FOO_MODEL`.
   - Register the router in `backend/app/main.py`.
5. **Wire the frontend**:
   - `frontend/src/lib/queries/foo.ts` — a `useMutation`/`useQuery` hook calling the new
     endpoint via `useApi()`.
   - `frontend/src/components/sections/foo.tsx` — real controls + real result rendering (no
     mock data). Add `'foo'` to the `Section` union in `frontend/src/lib/store.ts` and to the
     nav/command-palette lists. Wrap it in `SectionScroll` + `SectionHeader`
     (`components/shared/layout.tsx`) like every other section, and take font sizes from the
     type scale in `src/app/globals.css` — `text-[13px]` and friends are rejected by ESLint.
6. **Add tests** in `tests/test_foo.py` (test the deterministic engine on synthetic data; no
   network/LLM in tests) — and a live smoke test through `fastapi.testclient.TestClient` for
   the new router while developing, mirroring the pattern used for every other agent's
   endpoints.

Conventions to follow: deterministic-first; degrade gracefully on missing columns; never let
the LLM invent numbers; keep the engine pure (no framework imports — Streamlit *or* FastAPI)
so it's unit-testable in isolation.

---

## Testing

```bash
python -m pytest -q                        # whole backend suite (882 collected, all passing
                                           #   with Postgres up; integration tests auto-skip)
python -m pytest tests/test_root_cause.py -q   # the drill-down search, incl. the null-result control
python -m pytest tests/test_monitoring.py -q   # alert ranking, cooldown, EN/AR briefings, cron
python -m pytest tests/test_briefing_report.py -q  # report structure, metric units, EN/AR sections
python -m pytest tests/test_churn.py -q
python -m pytest tests/test_crm_snapshot.py -q     # CRM compute (hermetic, no Postgres)
python -m pytest tests/test_crm_repository.py -q   # CRM persistence (@integration)
python -m pytest tests/test_llm_usage.py -q        # token accounting, incl. the streaming path
python -m pytest tests/test_usage_binding.py -q    # usage attribution across the threadpool hop
python -m pytest -m "not integration" -q    # skip the Postgres-backed tests explicitly

# Frontend
cd frontend
npx tsc --noEmit      # type errors
npx eslint .          # lint (react-hooks rules + the type-scale guard)
npx next build        # production build — catches SSR-incompatible code, etc.
```

The `tests/` suite covers the deterministic engines (schema intelligence, KPIs, time-series,
quality, forecasting back-test/validation, marketing, churn), the sandbox, the
DB/DDL/storage/reconciliation/schema-discovery layer, the cleaning stack, and the Copilot
planner + SSE streaming layer (`test_copilot_streaming.py`: provider-fallback for `stream_complete`, plan
normalization, and the full multi-step `meta → step → token → done` frame sequence with the
planner/tools/LLM mocked and the in-memory session store used for real).

The cleaning stack has five dedicated files, and they are the regression suite for the
architecture rather than incidental coverage:

| File | What it pins down |
|------|-------------------|
| `test_cleaning_ops.py` | Every operator's contract — money/date/boolean parsing (including that `"A1"` and `"2024-01-05"` must **never** become numbers), that flagging operators leave values untouched, that `impute` refuses a mostly-empty column, key-column protection, and that the apply engine rejects an operator which reorders or invents rows |
| `test_defect_detection.py` | Each of the six dirty tables that used to be declared "already clean", the name-heuristic false positives (`paid`/`valid`/`width`/`record` are not identifiers), spreadsheet-style `"Order ID"` names, and that tidy data still produces an empty plan |
| `test_cleaning_invariants.py` | **All seven corruptions that used to pass**, plus the legitimate operations that must still pass, plus the free-text authorization rules |
| `test_cleaning_graph.py` | The repair loop: an invariant violation re-enters the Coder with the rule and column named, bad code eventually fails instead of looping, and a typed plan never calls an LLM |
| `test_cleaning_already_clean.py` | Planner behaviour: LLM refines the baseline, and every way the model can fail (unreachable, unparseable, invented operator, malformed parameter) degrades to the deterministic plan |

`test_root_cause.py` is built around a matched pair, because the hard part of attribution is
not finding a cause but declining to invent one. The same generator produces a dataset with a
planted three-dimensional cause (`Office Bulk Subscription × North × Email` collapses in the
current month) and an identical one with the plant removed. The engine must recover the
planted slice **and mark it confirmed**, and must confirm **nothing at all** in the control —
which is the assertion that the noise model and the multiple-comparisons correction are
actually doing their job. The rest of the file pins the arithmetic that has to close (slice +
rest = total; volume + basket + interaction = the slice's change), that identifiers are never
treated as dimensions, that weekday is off by default with the calendar confound stated, and
that every pruning bound is *reported* rather than silently applied.

`test_monitoring.py` pins the three properties that decide whether scheduled monitoring is
useful or gets muted within a week: that alerts are ranked by money at stake through the same
evidence engine the Insights report uses, that a standing problem is fingerprinted by its
identity rather than its value so the cooldown recognises it tomorrow, and that the Arabic
briefing is genuinely Arabic — including a check that every Arabic template cites figures
through `{{tokens}}` and **types no digits of its own**, so the fabrication guarantee holds in
both languages. It also pins that the reconciler survives its own reconcile — a one-line
namespace bug that left the scheduler looking perfectly healthy while silently ignoring every
schedule created after start-up.

Two of those checks exist because a live run produced the failure, not because a reviewer
imagined it. The Arabic briefing rendered `أُنشئ في 09 Aug 2026`, and a narration draft came
back with a Chinese conjunction inside an Arabic sentence — so `test_root_cause.py` now pins
that the deterministic summary exists in Arabic, that both languages cite the *identical* set
of figure tokens (one arithmetic, two renderings), and that a foreign script is caught unless
it came from the customer's own data.

The CRM's two files are split along the architectural seam rather than by convenience, which
is the point of the seam: `test_crm_snapshot.py` (19 tests) exercises the compute against a
synthetic DataFrame with **no database at all**, and `test_crm_repository.py` (16 tests,
`@integration`) exercises persistence against Postgres with **no dataset**, each creating and
tearing down its own throwaway project. Between them they pin the four contracts — that every
customer gets a record and not just the displayed top-N (the defect that made the old churn
persistence unable to back a customer page), that records survive the churn model being
unavailable, that `value_at_risk` prefers CLV and *says which basis it used*, that re-running
a captured date replaces rather than duplicates and leaves no orphaned rows, that an unknown
sort key falls back instead of injecting SQL, that NULLs sort last, that the portfolio reports
null rather than `0.00` when money is unmeasurable, and that `strip_pii` clears identity
fields from flat, nested and list-wrapped payloads alike.

They are **LLM-free
by design** everywhere (LLM calls are mocked at the `complete()`/`stream_complete()` boundary). Most are also network-free;
tests that need the live docker-compose Postgres are marked `@pytest.mark.integration`
(`tests/conftest.py`) and are **skipped automatically** if it isn't reachable, so the suite
stays green without Docker.

There is still no dedicated `backend/tests/` suite beyond the placeholder package — every
backend router was instead verified live during development via
`fastapi.testclient.TestClient`, exercising the real endpoints end-to-end against the real
Postgres and real LLM providers (register → login → create project → ingest → clean →
save → run the agent → assert on the real response), with test data cleaned up afterward.
That verification isn't captured as a checked-in automated suite — adding one (`backend/tests/`,
using `TestClient` + a disposable test schema) is the highest-value testing gap to close next.

The one exception is `tests/test_usage_binding.py`, which drives a miniature FastAPI app
through `TestClient` because the thing under test *is* the framework's behaviour: FastAPI runs
`def` endpoints in a threadpool, and a ContextVar set in the wrong place lands on a throwaway
copy of the context that the endpoint never sees. Asserting on the ContextVar directly would
have proved nothing — the propagation is the claim. It also pins that bindings cannot leak
between requests, because the failure there is one user's tokens billed to whoever called last.

---

## Design notes & known limitations

- **Cleaning is deterministic; the free-text fallback is bounded, not proven.** A plan of
  typed operators runs no generated code, so its correctness is exactly the correctness of
  its unit tests. A user-typed free-text step still generates Python — bounded to the columns
  that step names, to the change-kinds the plan's operators permit, and re-checked by the
  invariant gate, with violations fed back for repair. That is a hard bound, not a proof, so
  the system's accuracy claim is only as strong as its weakest path: **deterministic where
  operators cover the work, bounded-and-verified where they don't.** The way to shrink the
  gap further is to add operators, not to trust the model more.
- **Flagged is not fixed.** Outliers, negative quantities, arithmetic mismatches and
  duplicate business keys are marked in `<column>__is_outlier` / `__out_of_range` /
  `__mismatch` / `__is_duplicate_key` columns rather than corrected, because each is
  ambiguous (a large order is usually a real large order; a negative amount may be a refund).
  Nothing downstream currently *consumes* those flags — Insights and the KPI engine don't yet
  exclude or annotate flagged rows, so a human still has to read them.
- **Ambiguous dates are reported, not guessed.** When no value in a `DD/MM` column has a
  component above 12, the day/month order is genuinely undecidable and the finding says so
  (`auto_fixable = False`). The plan will not convert that column until someone supplies
  `dayfirst`, because guessing wrong silently moves up to 11/12 of the rows into the wrong
  month and nothing downstream can detect it.
- **Sandbox is restricted, not bulletproof.** `tools/sandbox.py` / `agents/cleaning/executor.py`
  run LLM-generated code with a shared restricted `__builtins__` allow-list plus a pre-scan
  for dunder escapes (`__class__`, `__subclasses__`, `__import__`, …) and for calls to
  blocked functions (`eval`, `exec`, `compile`, `open`, `getattr`, …). That call scan is
  anchored on whole identifiers, so a column genuinely named `open(x)` no longer trips it
  while a real call still does. `np` and `re` are provided to cleaning code deliberately:
  generated code reaches for `np.nan` and `re.sub` constantly, and without them every attempt
  burned one of only two repair attempts on a `NameError`. There's also a shared
  `run_with_timeout()` (both surfaces genuinely give up after 30s instead of blocking on
  `ThreadPoolExecutor.shutdown(wait=True)`, which used to silently defeat the timeout for
  merely-slow, not-infinite code). This blocks casual misuse and the common escape vectors,
  but is still not a true security boundary (Python can't forcibly kill a thread, so a truly
  non-terminating loop can still hang the process at exit). For fully untrusted input, move
  execution to a subprocess/container. *(Planned hardening — see `FEATURE_ROADMAP.md` item C3.)*
- **Forecasting is blind to the Hijri calendar, and the cost is measured.** Daily seasonality
  is modelled with Fourier terms at 7 and 365.25 days, both periodic in the *Gregorian* year.
  The Hijri year is ~354 days, so Ramadan drifts ~11 days earlier annually and never returns
  to the same annual phase — no number of 365-day harmonics can represent a cycle that moves.
  A six-origin out-of-sample holdout on `data/nile_retail/` put the gap precisely: **6.5%
  MAPE on the 30-day total in ordinary windows (4/4 inside the 80% interval) against 28.8% in
  windows containing Ramadan or Eid (0/2 inside)** — under-forecasting the build-up by 26%,
  then over-forecasting the following month by 31% after reading the surge as a new level.
  `agents/forecasting/tools/calendar_events.py` implements the regressors and is **not wired
  in**: adding an exogenous input to one candidate and not the others breaks the comparison
  the `Auto` selector depends on, so the enriched model won every back-test fold and was then
  chosen for ordinary windows too, doubling their error. The module's docstring records the
  measurements and the fair-selection design that would fix it.
- **Reliability labels don't track holiday-driven failure.** They are derived from back-test
  skill, and the folds contain no comparable regime shift — in the holdout above, both
  catastrophic forecasts were labelled `reliable` while the most accurate one was labelled
  `indicative`. With n=6 that is a direction, not a proof, but the mechanism is concrete.
- **Forecasting LLM narrative is wired into the graph.** The graph is
  validate→prepare→forecast→interpret→END; `agents/forecasting/interpreter.py` enriches every
  forecast output with a grounded `business_summary` + risks/opportunities/actions, falling back
  to a deterministic summary when no provider is configured.
- **Semantic views join one hop only.** `db/views.py` joins the chosen fact table with tables
  *directly* related to it — it does not chase multi-hop chains. Keep data star-schema-shaped
  (one fact table, dimensions referencing it directly) for the cleanest result.
- **Real per-user auth, but stateless sessions.** Every project is owned
  (`projects.owner_id`) and every route checks it — one user genuinely cannot see or act on
  another's projects/reports/API keys. What's *not* implemented: access tokens are plain
  signed JWTs with no server-side session table, so there is no way to list or revoke an
  individual "active session" (the Account page says so explicitly rather than faking a
  device list) — logging out just discards the token client-side, and rotating
  `JWT_SECRET_KEY` is the only way to invalidate every token at once.
- **The Data Cleaning HITL workflow is held in memory, per backend process
  (`backend/app/services/pipeline_sessions.py`).** A `pipeline_session_id` keys an in-memory,
  TTL-expiring dict of raw/interim DataFrames between ingestion and "Save to Project" — the
  same durability guarantee Streamlit's `st.session_state` already had, not a regression.
  It does mean: (a) restarting the backend mid-cleaning loses any unsaved session, and
  (b) running more than one backend process/replica behind a load balancer would need this
  swapped for a shared store (Redis, or the Postgres-backed pattern used everywhere else) —
  today's deployment target is a single backend process.
- **Copilot conversations aren't persisted.** The chat lives in client-side React state, and
  `backend/app/services/copilot_sessions.py` caches only the *last* turn's route + `raw_payload`
  (so a "follow-up" can explain it) in the same in-memory, per-process, TTL-expiring store as
  the cleaning HITL store above. A page refresh or backend restart ends the conversation —
  there is no conversation-history table or "resume thread" yet.
- **Token usage is recorded; cost and the dashboard are not built yet.** Every provider
  attempt now writes an `llm_usage_events` row with the counts the provider itself reported
  (`tools/llm_usage.py`), but nothing reads them back — there is no pricing table, no
  `/usage` endpoint and no page. The fact table also deliberately has **no cost column**:
  token counts are ground truth and never change, whereas a stored cost silently rots as
  provider prices drift, so cost belongs at read time against a dated price list and clearly
  labelled an estimate.
- **LLM API keys are per-deployment, not per-user.** Every provider key is read from the
  process environment at call time, so the LLM layer is single-tenant by construction. Users
  can pick a *model* per agent (`User.model_preferences`) but cannot bring their own key. The
  seam for it now exists — `tools/llm_usage.py`'s ContextVar plumbing is the same mechanism a
  per-user credential would travel on — but the handlers still read `os.environ`.
- **Only Groq honours the per-agent model picker.** Every other provider is pinned to one
  fixed model per purpose by `tools/llm_provider_models.py`. Harmless while keys are
  deployment-wide; the moment users bring their own OpenAI key it reads as a bug, and that
  file has to become a per-provider catalogue of selectable models.
- **`qwen/qwen3.6-27b` is selectable in Settings but is not yet safe for the JSON agents.**
  It is the strongest reasoner in the current Groq catalogue, which is why it is offered — but
  it emits its chain of thought as inline `<think>…</think>` inside `content` rather than in a
  separate `reasoning` field, and every agent here parses JSON out of raw text. Suppressing
  that needs `reasoning_effort="none"` on the request, and **`tools/llm_client.py` never sends
  that parameter** — neither `_call_groq` nor the streaming path takes a per-model kwarg at
  all. So selecting it for the planner, coder, insights or copilot purposes will produce
  unparseable output and fall through to that agent's deterministic floor. It also costs ~4–5×
  and caps output at 16K tokens. The fix is a per-model request-kwargs table in the client,
  the same shape `tools/llm_provider_models.py` already uses for per-purpose model ids;
  `agents/constants.py` records the constraint in the meantime.
- **No audit/activity log, no 2FA, no billing/plans.** The Account and Settings pages say so
  explicitly ("not yet configured" / "Future-ready") rather than rendering placeholder data
  for any of these.
- **MSSQL (Path B) is code-complete but untested** — `mssql+pyodbc` needs a system ODBC
  driver that isn't installed by `pip install -r requirements.txt`. Postgres and MySQL work
  out of the box.
- **Fernet credential encryption is proportionate, not a KMS.** No key rotation/versioning;
  losing `FERNET_KEY` means re-entering any saved external-database credentials.
- **Manually-added relationships aren't validated.** A relationship the auto-scan proposed
  goes through `relationships/review.py: validate_relationship()` (dtype compatibility,
  orphan ratio, PK uniqueness) before it's shown; one the user types in via
  `ManualRelationshipForm` is persisted on trust alone — neither the frontend nor
  `POST /pipeline/{session}/relationships` calls that same check against the real data for
  manual entries. A confidently wrong manual mapping would only surface later, at
  Reconciliation's orphan-rate check.
- **`backend/app/api/v1/assistant.py`'s `/ask` route is orphaned.** It's a real, working
  endpoint (calls `run_grounded_chat` directly) with a matching frontend hook
  (`useAskDaas` in `frontend/src/lib/queries/assistant.ts`), but no page currently
  calls that hook — Copilot's `general` route reaches the same underlying function via its
  own path instead. Worth wiring `/ask` to a UI (or removing it) rather than leaving
  working-but-unreachable code in the tree.
- **SHAP explains the base churn model, not the calibrated probability.** Sigmoid
  calibration (`agents/churn/model.py`) is a monotonic rescaling fit *after* the
  `HistGradientBoostingClassifier`, so `TreeExplainer` intentionally runs against the
  pre-calibration model — the ranking/direction of drivers is unaffected by calibration, but
  the raw SHAP values are in the model's margin space, not a probability delta.
- **The CRM page is shipped, but nothing about it is pinned by an automated test.** All six
  `/crm/*` endpoints have Python coverage (35 tests across the compute/persistence seam), and
  `frontend/src/components/sections/crm.tsx` was verified by hand against a real project — but
  there is no frontend test suite anywhere in this repo, so the two UI rules that carry a
  *correctness* claim rather than a cosmetic one (null renders as an em-dash and never as
  `EGP 0.00`; PII appears only inside the Customer 360 dialog) hold by review, not by CI.
  `tsc --noEmit` + `eslint` + `next build` are the only automated frontend gates.
- **CRM snapshots accumulate one row per customer per snapshot date, with no retention
  policy.** That is the intended design — the timeline is the feature — but nothing prunes it,
  so a project refreshed daily for a year holds 365 rows per customer. The indexes are built
  for that access pattern; disk is not yet managed.
- **`customer_state.predicted_clv` is always null.** The column, its horizon and
  `predicted_purchases` exist and are declared `{"status": "pending"}` rather than absent, so
  the API can distinguish *not modelled yet* from *modelled as zero* — but the CLV agent is
  Stage 1 and not built. Every `value_at_risk` currently carries
  `value_basis = "historical_monetary"`, which is stated in the payload rather than implied.

See `FEATURE_ROADMAP.md` for the prioritised plan addressing these and adding new value.

---
## Recent enhancements

- **The CRM gets its screen — and the ranking argument becomes visible (2026-08-23)** — the
  six `/crm/*` endpoints had been live, tested and ownership-enforced for a week with nothing
  calling them, which is the least useful state a feature can be in: the customer history was
  accumulating on every churn run and no one could see it. `crm.tsx` (four tabs + a Customer
  360 dialog), `queries/crm.ts`, the route, and both locale bundles close that.

  The design problem the page had to solve is that a CRM screen is where a *"looks tidy"*
  instinct does the most damage. Three of its decisions are the contracts made visible rather
  than styling choices. The **provenance panel** reports observed / derived / predicted
  coverage as three separate numbers instead of one completeness bar, because a customer with
  RFM but no CLV is not 66% of a customer — and with CLV unbuilt, one bar would read as a
  defect rather than as a stage that hasn't shipped. **Null renders as an em-dash, never as
  `EGP 0.00`**, so a project with no monetary column can't show a currency figure nobody
  measured. And **PII is confined to the Customer 360 dialog**, matching `strip_pii()` on the
  server: the list views the user scans — and screenshots, and shares — carry ids.

  The **Prioritisation** tab is the pillar's whole argument on one screen: the same snapshot
  ranked two ways, with the value delta and the count of names a risk-only list misses. On
  `bloom_and_bean` that is EGP 432 against EGP 22,127 with **zero overlap** — an argument that
  only holds because both lists are read from stored state rather than recomputed, so they
  provably describe the same customers at the same moment. Full design record:
  [`CRM_ARCHITECTURE.md`](CRM_ARCHITECTURE.md).

- **A silent provider decommission, and the test that would have caught it (2026-08-23)** —
  Groq retired `llama-3.3-70b-versatile`, `llama-3.1-8b-instant`, `mixtral-8x7b-32768`,
  `llama-4-scout` and `qwen3-32b` on 2026-08-16. Eight of the eleven `DEFAULT_*_MODEL` entries
  pointed at the first of those, so the planner, insights, marketing, churn, schema-discovery,
  copilot, explain-chart and coder purposes were all one API call away from failing — and
  because every agent has a deterministic floor, most would have degraded *quietly* rather
  than erroring, which is worse. All eleven now default to `openai/gpt-oss-120b`.

  The durable part isn't the new id, it's `tests/test_constants.py`: `AVAILABLE_MODELS` and
  the defaults were two hand-maintained lists that could drift apart with nothing noticing, so
  the suite now asserts that **every** `DEFAULT_*_MODEL` is a member of `AVAILABLE_MODELS`
  (discovered by reflection, so a twelfth purpose is covered the day it is added) and that no
  retired id has crept back into the picker. `qwen/qwen3.6-27b` was added to the catalogue as
  the strongest available reasoner and is documented as *not yet safe* for the JSON-parsing
  agents until the client can send `reasoning_effort` — see the limitations section, because
  offering it without that note would have traded one silent failure for another.

- **The briefing becomes a report that shows its working (2026-08-22)** — the scheduled
  message was already template-composed with no model in the delivery path, which made it
  *trustworthy*; it was not yet *checkable*. It now carries **Basis of this report** (the
  baseline reading its percentages are measured against, coverage, record count, currency),
  **The figures** (prior → current → change per finding, plus both comparison windows and
  their row counts), and **How this report was produced**.

  That last section states only what the system can actually keep: no figure was written by a
  language model, the narrative was verified against those same figures, **the model's draft
  was rejected when it was**, and how many explanations survive multiple-testing correction —
  closing with *figures are measured, causes are inferred*. Stamping the whole thing "100%
  accurate" would have wrapped a provable claim and an unprovable one in the same words, and
  the unprovable half is what would eventually cost the provable half its credibility.

  Because WhatsApp and Telegram cut at 4096 characters — and would cut the *evidence* first,
  since it sits lowest — short channels now get a purpose-built executive summary and a link
  while email gets the full report; a test pins that the summary can never state a figure the
  report does not. Rendering it also caught two bugs that looked authoritative and were wrong:
  a gross-margin percentage printed as `EGP 31.40` (metrics now declare units, and an unknown
  metric falls back to a plain number, never money), and a standing *exposure* printed as a
  bare amount beneath a headline total that deliberately excludes it, which read as an
  arithmetic error in the report.

  Delivery setup got the matching fix: the schedule form's "where it goes" step used to
  dead-end at *"no channels yet"* with no way out, so the first schedule anyone created forced
  them to abandon a half-filled form and start over from the Channels tab. A channel can now
  be added inline and is auto-selected, and untested channels are flagged — the failure mode
  otherwise announces itself at 07:00, when the briefing doesn't arrive.

- **Token accounting across every LLM call (2026-08-22)** — the provider SDKs had been
  returning exact token counts on every response and all eight handlers were discarding them;
  there was no `usage` reference anywhere in the codebase. `tools/llm_usage.py` +
  `db/usage_models.py` (migration `d17c93a5f2b8`) now record one row per provider *attempt*,
  so a chain quietly failing over to its second choice is visible rather than hidden.

  `complete()` returns a bare `str` and has 23 call sites, so instead of changing its return
  type the client **pushes** events to a sink installed in the surrounding context — metering
  is invisible to callers and no agent knows it exists. The ContextVar has to be set in an
  **`async`** dependency: FastAPI runs `def` endpoints and `def` dependencies in a threadpool,
  where a `set()` lands on a throwaway copy the endpoint never sees. Sync dependencies can
  still *mutate* the bound object, which is how `get_owned_project` attaches the project id.

  The streaming path was the real trap. Usage arrives on a final chunk whose `choices` list is
  **empty** — precisely what the stream loops were skipping — and OpenAI-compatible providers
  only send it when asked via `stream_options={"include_usage": True}`. Copilot is entirely
  streamed, so without both fixes the dashboard would have under-reported the heaviest surface
  in the product while looking perfectly healthy. Reading it back — pricing, an endpoint, a
  page — is not built yet; see the limitations section.

- **One type scale, lint-enforced (2026-08-22)** — the frontend carried **818 hardcoded pixel
  font sizes across 27 distinct values** (12px ×146, 12.5px ×149, 11px, 11.5px, 10.5px, 13px,
  13.5px, 9.5px…) and zero uses of any shared step. Half-pixel distinctions nobody can
  perceive, applied inconsistently, are why two cards built a week apart never quite lined up
  — the measurable part of "it doesn't look professional".

  A 12-step scale now lives in `globals.css` and **overrides** Tailwind's defaults rather than
  sitting beside them, so shadcn's `text-sm` and a section's `text-sm` are the same size (the
  product is denser than shadcn's defaults, so the app's density wins). Steps `2xs`–`base`
  pair the 1.5 line-height those sizes already inherited, making the migration font-size-only;
  `md` and up tighten, because 1.5 on a 20px page title is simply too loose. A
  `no-restricted-syntax` rule now rejects `text-[13px]` in both `className` strings and
  template literals. The colour system was never the problem — the oklch tokens were already
  good, and the layout primitives were already adopted by 12 of 17 sections.

- **Scale + correctness validation, and a measured forecasting limitation (2026-08-18)** — the
  platform gets a dataset big enough to break it and honest enough to grade it.

  `Generate_Data/generate_enterprise_data.py` produces **Nile Retail Co.**: 836,630 order
  lines across four related tables, 3.6 years ending yesterday, built for an Egyptian
  omnichannel retailer. Its point is not size but *verifiability* — every pattern is injected
  deliberately and then **re-measured on the generated output** into `ground_truth.json`, so
  "did the analysis run?" becomes "did it find the answer we already know is true?".

  Against that ground truth the platform holds up: schema detection got all six columns at
  high confidence on a 40-column join, KPIs matched exactly (revenue to the cent, 389,897
  distinct orders, outage coverage), the root-cause drill-down named the true injected cause,
  churn reached **AUC 0.874** on 295k rows with out-of-time validation, and nothing needed
  sampling — the 836k-row fact view builds in ~18s and a full monitoring run takes ~64s.

  It also produced the first properly-quantified limitation of the forecasting engine. A
  six-origin **true holdout** (truncate, refit, compare the published total to what actually
  happened) measured **6.5% MAPE with 4/4 interval hits on ordinary 30-day windows**, against
  **28.8% with 0/2 hits** on windows containing Ramadan or Eid — a structural consequence of
  modelling seasonality with Gregorian-periodic Fourier terms when the Hijri calendar drifts
  ~11 days a year. The fix was implemented, measured, and **reverted**: the regressors worked
  (Ramadan/Eid error fell to 19.9%) but adding an exogenous input to one candidate broke the
  `Auto` selector's comparison and doubled ordinary-window error. See the limitations section
  and `agents/forecasting/tools/calendar_events.py` for the evidence and the fair-selection
  design that would fix it properly.

- **Pillar II Stage 0 — the stateful customer layer (2026-08-16)** — the platform gets a
  memory of its customers. Full design record in **[CRM_ARCHITECTURE.md](CRM_ARCHITECTURE.md)**.

  Every engine before this one computed a number and forgot it, which is fine for a report and
  fatal for a CRM: *"was this customer riskier last month?"* cannot be answered by recomputing,
  because recomputing answers a different question that happens to produce a similar-looking
  number. `agents/crm/` + `db/crm_models.py` (migration `b3e91c47d208`) add a per-customer,
  per-snapshot record written once and never recomputed — covering **every** customer, not the
  displayed top-N, which is precisely why the pre-existing `agents/churn/storage.py` could never
  have backed a customer page.

  The package is split so that `snapshot.py` never touches the database and `repository.py`
  never computes, which is what lets the compute be tested against a CSV with no Postgres and
  the persistence against Postgres with no dataset (19 + 16 tests). Four contracts are pinned
  by those tests: persistence failure returns a status instead of raising (a storage problem
  must not turn a successful churn analysis into a 500); PII crosses to an LLM only through
  `strip_pii()`, generalising the `_`-prefix convention that a serialised dataclass silently
  defeats; a refresh is idempotent by snapshot date via delete-and-rewrite in one transaction;
  and every ranked figure carries `value_basis` — with `portfolio_summary` returning **null,
  not `0.00`**, when there is no monetary column, because a currency zero reads as a
  measurement.

  Two tables rather than one, for the reason `monitor_runs` is separate from `monitor_alerts`:
  without a run record, "no at-risk customers" and "the refresh has been failing for six days"
  look identical on the page. Lifecycle stage replaces the planned 0–100 health score, with
  thresholds derived from the data's own median inter-purchase cadence and **stored on the
  snapshot** so the rule is auditable rather than a magic 90 days.

  `GET /crm/ranking-comparison` is the artifact the layer exists for: on `bloom_and_bean`, the
  top 10 by churn probability alone cover **EGP 432**; the top 10 by value at risk cover **EGP
  22,127**, with zero names in common — both read from the same stored snapshot, so they
  provably describe the same customers at the same moment.

  Two findings worth carrying forward. The day-one CLV gate passed on repeat buyers (74.8%),
  history (730 days) and multi-product orders (47.1%), but **Gamma-Gamma's independence
  assumption is violated** — `corr(repeat_txns, avg_order_value) = +0.485`, p ≈ 6e-22, monotone
  across deciles and not a multi-line-order artifact — so a vanilla Gamma-Gamma CLV would
  under-predict exactly the high-frequency customers the CRM protects, and that error would land
  in the headline ranking (`CRM_ARCHITECTURE.md` §6). And `db/monitoring_models.py` carried a
  latent mapper bug — a `relationship()` naming `"Project"` by string with no `platform_models`
  import — that broke any standalone import of the module and was masked only by import order in
  `db/init_platform.py`; found because the new models had the identical defect, and fixed in both.

- **Pillar III — the autonomous analyst (2026-08-09)** — two features that together move the
  platform from "answers questions when opened" to "tells you when something matters".

  **Root Cause (`agents/rootcause/`)** automates the question that always follows an insight:
  *in which segment?* It searches the lattice of `dimension = value` combinations for the
  smallest slice explaining the largest part of a metric's change, ranking on three axes that
  a `groupby` collapses into one — contribution (how much of the change), **excess** (how much
  the business-wide trend fails to explain — a segment that shrank at exactly the company rate
  contributed a lot and *caused nothing*), and concentration (92% of the decline from 4% of the
  transactions is a finding; from 89% it is a restatement). Tractability comes from vectorised
  sibling evaluation (one `bincount` per node/dimension), two **exact** pruning bounds
  (support, and a magnitude bound over the slice's positive/negative mass), and a split beam —
  half following the biggest slices, half the most anomalous — which is the only approximate
  step and is reported as such.

  Two guards keep it from being confidently wrong. A **compound-Poisson noise model**
  (`Var(Σx) ≈ Σx²`) accounts for both how many transactions a slice has and how much they
  vary, so a segment with uniform prices is not treated as noiseless. And a **Bonferroni
  correction** over the number of slices actually tested: a 2σ bar applied to 300 slices
  produces ~15 "findings" from data with no cause in it, so explanations are *labelled*
  confirmed-or-lead rather than silently dropped. The regression suite is a matched pair — a
  dataset with a planted three-dimensional cause, and the identical generator with the plant
  removed. The engine recovers the first and confirms **nothing** in the second.

  Weekday is available as a dimension but **off by default**, and that is a correctness
  decision: April 2025 has five Tuesdays and May has four, so a flat business shows *"Tuesday
  explains 92% of the decline"* — a calendar artefact wearing the costume of a root cause.

  **Autonomous Monitoring (`monitoring/`, `notifications/`)** runs the analytics on a schedule,
  compares against *recorded* snapshots (not recomputations — a re-cleaned dataset answers
  "what was revenue last week?" differently every time), applies the owner's own targets, ranks
  everything through the **same** `agents/insights/evidence.py` money-at-stake engine the
  Insights report uses, drills into the largest finding, and pushes a briefing. Alerts carry a
  cooldown keyed on the finding's *identity* rather than its value, so a standing leak is
  recognised tomorrow instead of being reported every morning until the channel gets muted.

  Delivery is four transports behind one interface — in-app, email, Telegram, and **WhatsApp**
  with both the Meta Cloud API and Twilio backends plus the 24-hour template rule handled
  explicitly. WhatsApp is the point rather than a checkbox: in Egypt and the wider MENA SMB
  market it is where business is conducted, and a three-line Arabic briefing arriving there at
  07:00 is a different product from the same analysis waiting behind a login. The Arabic is
  **composed, not translated** — each finding has an Arabic sentence carrying the identical
  `{{citation}}` tokens, so the two briefings are siblings rendered from the same arithmetic
  and neither can drift from the numbers. No model sits in the delivery path at all.

  Scheduling is APScheduler hosted in the API process (so `uvicorn` alone is a complete
  deployment) with the `Schedule` table as the source of truth, plus a standalone
  `python -m monitoring.worker`; running both is safe because every run takes a Postgres
  advisory lock. Everything is user-controlled — frequency down to raw cron, per-schedule
  timezone, severity floor, cooldown, targets, channels, language, quiet hours — and
  **Preview** runs the identical pipeline with delivery and history switched off, because a
  preview on a different code path is a preview of something else.

  **What the live run found that the tests did not.** Both features were driven end to end
  against the running API and a real 5,142-row project — 44 assertions covering create →
  preview → run → deliver → cooldown → delete. The engine held up (1.7M candidate combinations
  reduced to 6,991 evaluated, a 249× reduction, and the top explanation cleared Bonferroni at
  p = 0.00013), but four defects surfaced that only exist once real processes and a real
  language are involved: the reconciler **deleted its own job** on its first tick, leaving a
  scheduler that looked healthy and silently ignored every schedule created afterwards; the
  Arabic briefing rendered `09 Aug 2026` and `في May 2025` because `strftime` and the measure
  labels were built before anyone knew which language would read them; a narration draft came
  back with a **Chinese conjunction** inside an Arabic sentence, arithmetically perfect and
  completely unusable; and the deterministic fallback existed only in English, so rejecting a
  bad Arabic draft would have swapped one leak for another. Each is fixed at the level that
  makes it unexpressible rather than merely absent — disjoint job-id namespaces, labels
  localised where they are created, a second verification gate on writing system, and a
  bilingual fallback — and each carries a regression test.

- **Data Cleaning: rebuilt around deterministic operators (2026-08-08)** — cleaning went from
  "an LLM writes `clean_data(df)`, a validator checks the null count" to "an LLM selects typed
  operators, tested code executes and measures them".

  The audit that prompted it ran seven deliberately corrupting `clean_data` functions through
  the old executor and validator. **All seven passed**: a revenue column multiplied by 100,
  overwritten with its own mean, and deleted outright; 45% of rows deleted; a join key
  case-folded; every row duplicated; a column shuffled against the wrong rows. None of them
  changes a null count, and a null count was the entire gate. Separately, six realistic dirty
  tables were declared "already clean" and shipped untouched — placeholders in mixed-dtype
  columns, all-numeric-string columns, `$1,299.00`, `Cairo`/`cairo`/`CAIRO`, `-999` sentinels
  — because detection was gated on `is_string_dtype` (False for any column mixing strings and
  numbers) and required a numeric ratio strictly *below* 1.0.

  1. **21 typed operators** (`tools/cleaning_ops.py`) with parameter schemas, replacing
     freeform code generation. Handles currency/percent/EU-decimal parsing, Excel serials and
     epoch timestamps, DD/MM vs MM/DD resolved from the data, category harmonisation,
     sentinels, business-key duplicates, and cross-column arithmetic checks.
  2. **Measured ledger** — `apply_step()` diffs the frame and computes the audit trail, so the
     transformation log cannot claim a change that did not happen. The old log was whatever
     the model chose to `print()`; in testing it reported *"Filled 3 missing values with mean"*
     while actually overwriting all 20.
  3. **Authorization invariants** (`agents/cleaning/invariants.py`) — cell-level, so an
     `impute` step authorizes filling blanks but not rewriting existing values. All seven
     corruptions are now rejected with a named rule and column.
  4. **Deterministic detection** (`tools/defect_detection.py`) — 19 checks that also derive a
     complete typed plan, so the pipeline cleans correctly with no LLM available at all.
  5. **Fabrication instructions removed** — both prompts told the model to `ffill`/`bfill`
     unparseable dates *"so the validation check doesn't flag a regression"*. Unparseable
     values now become NULL and are reported.
  6. **Validation failure feeds the repair loop** — previously only a *crash* triggered a
     retry, so code that ran but corrupted the data ended the graph immediately.
  7. **Ordering fix** — de-duplication now precedes imputation. The old order imputed first,
     manufacturing duplicates that were never in the source, then deleted genuinely distinct
     rows.

  Suite: 716 passing, 0 failing (was 570 passing, 9 failing).

- **Copilot: token-streaming, multi-step reasoning & chat UX (2026-07-25)** — the Analyst
  Copilot went from a single-agent, blocking chat to a streaming, multi-agent one.
  1. **Streaming (SSE)** — new `POST /copilot/ask/stream`
     (`backend/app/services/copilot_stream.py`) streams the answer token-by-token via
     `tools/llm_client.py: stream_complete()` (a streaming twin of `complete()` with the same
     provider-fallback chain; a *mid-stream* failure can't fall back — that would splice two
     answers together). The frontend consumes it with `fetch` + a stream reader (not the
     browser `EventSource`, which can't POST or send an auth header). First streaming anywhere
     in the codebase.
  2. **Multi-step reasoning** — a new planner (`agents/copilot/planner.py`) decomposes a
     question into 1–3 ordered agent steps and, when there's more than one, ties them together
     with a single grounded `narrate.plan_synthesis` over the union of every step's payload.
     Single-step questions cost exactly what they did before (the planner is the router then).
     Runs each step directly via `agents/copilot/tools.py: run_tool()`, emitting a live `step`
     progress frame per agent. "Forecast revenue **and** flag who's about to churn **and**
     suggest a campaign" now runs Forecasting → Churn → Marketing and answers all of it.
  3. **Stream everything, safely** — a `general` factual answer is verified/corrected *after*
     the LLM finishes (the anti-fabrication guard in `run_grounded_chat`), so its raw draft
     tokens are never streamed; the already-verified answer is revealed with a genuine
     typewriter instead. Only truly-verifiable narration/synthesis streams token-by-token.
  4. **Chat UX** — live multi-agent progress strip, one chart/table/report block per agent,
     and per-answer **Stop / Regenerate / Copy / Save to Reports** actions, plus suggested
     prompts in the empty state (en + ar parity).
  - Verified: 467 backend tests pass (9 new hermetic tests — provider fallback, plan
    normalization, and the full `meta → step → token → done` frame sequence, all with the
    planner/tools/LLM mocked and the in-memory session store used for real); `tsc` + `eslint`
    + `next build` clean. Not yet live-browser tested against real Postgres/LLM.
- **Database: one pooled engine fixes intermittent "save failed" errors (2026-07-25)** —
  `tools/db_tools.py: get_engine()` built a brand-new SQLAlchemy engine (and connection pool)
  on *every* call — and it's called on every save/load/persist (`store_df_to_pg`,
  `db.loader.load_project_schema`, report/churn/forecast/marketing storage, `db.views`). Those
  pools were never disposed, so connections accumulated until Postgres hit `max_connections`
  ("too many clients") and saves began failing intermittently; with no `pool_pre_ping`, a
  connection idle long enough for Postgres to drop it also failed on next use. Now cached one
  engine per connection string with `pool_pre_ping=True` + `pool_recycle` — the same treatment
  `ingestion/db_link.py` already gave *external* databases. `db/session.py` binds its
  sessionmaker to the same engine, so the whole platform shares one pool.
- **Data Cleaning: already-clean detection + editable, structured plan steps (2026-07-21)** —
  *(the short-circuit below was superseded on 2026-08-08: `is_profile_clean()` missed six
  classes of genuinely dirty data and was replaced by `tools/defect_detection.is_clean()`.
  The structured-plan and manual-step-opinion work it introduced still stands.)*
  Closes a real reported gap: uploading data that was already clean still produced a
  generated cleaning plan (LLM-invented busywork), only the primary table's plan was ever
  reviewable in a multi-table upload, the plan was one opaque text blob nothing could
  address individually, and there was no way to get a second opinion on a manually-added
  step.
  1. **Deterministic already-clean short-circuit** — `tools/profiler_tools.is_profile_clean()`
     (no missing values, placeholders, duplicate rows, or type mismatches; deliberately
     excludes outliers, which are often legitimate extreme values) runs BEFORE any LLM call.
     When true, the planner returns `[]` without calling the model at all, and the coder
     returns a literal `return df.copy()` without calling the model either — cheaper and
     more reliable than asking an LLM to self-assess "nothing to fix," which tends to invent
     minor busywork instead of admitting that.
  2. **Structured plan** — `cleaning_plan` changed from a free-text string to
     `list[{id, description, source}]` end-to-end (`core/state.py`, `backend/app/schemas/
     pipeline.py`, the planner prompt now `json_mode=True`), so individual steps are
     addressable.
  3. **Per-table plan visibility** — the backend already supported planning/editing any
     table by name; the frontend `CleanStage`/`ProfileStage` hardcoded the primary table.
     Now both key their state and hooks off the sidebar's `activeTable`, so switching tables
     auto-profiles/plans that table and caches the result per table.
  4. **Add/delete steps + LLM opinion** — delete any generated step; add your own and get a
     short, profile-grounded opinion on it (`POST .../plan/opinion`,
     `agents/cleaning/planner.py::review_manual_step`) before committing.
  - Verified live end-to-end via `fastapi.testclient.TestClient` against real Postgres + real
    Groq: a genuinely clean table produced zero LLM calls; a dirty table produced 3 real
    structured steps; a manually-added step got a specific, grounded opinion; deleting a
    generated step and adding a manual one, then executing, produced generated code that
    included the manual step alongside the AI ones (confirmed via the transformation log,
    not just the API response shape).
  - Fixed a test fixture relying on the old "the LLM is always called" assumption
    (`tests/test_multi_table_cleaning.py`) — a defect-free bait table used to simulate a
    cleaning failure no longer reaches the LLM at all, so it needed a real defect to keep
    exercising that path. Tests: 359 → **378 passing**.
- **Churn: SHAP explainability + a full LLM-output accuracy audit (2026-07-21)** —
  1. **SHAP on churn** (`agents/churn/model.py`) — `shap.TreeExplainer` on the *base*
     `HistGradientBoostingClassifier` (never the probability-calibration wrapper — see
     [Design notes](#design-notes--known-limitations)) adds a second, complementary
     explainability technique alongside the existing permutation importance: a global panel
     plus per-customer signed top-3 drivers, computed only for the customers already shown in
     `at_risk_customers`. The retention-strategy LLM can now cite one customer's actual
     driver by name instead of only speaking in generalities — verified live: AUC 0.933 on
     real data, and the generated retention plan correctly referenced a specific customer's
     real SHAP driver.
  2. **Grounding audit across every agent** — rather than assume, an Explore pass checked
     whether every agent's LLM-stated numbers are actually verified against computed data.
     Insights/forecasting/churn narration/explain-chart were already solid. Two real gaps
     found and fixed: Copilot's `general` chat route could state a number its own code never
     computed (fixed — see [Shared/core components](#sharedcore-components)), and Marketing
     had no post-hoc grounding check at all (fixed — see the Marketing section above).
  - Tests: 378 → **397 passing**, plus three live `fastapi.testclient.TestClient`/direct
    verification runs against real Groq (churn retention plan, copilot chat, marketing
    report) with no mocking.
- **Copilot supervisor-router agent + explain-chart (2026-07-20)** — the "ask anything,
  routed automatically" agentic-AI feature: `agents/copilot/` classifies a natural-language
  question into one of seven other agents (or a grounded follow-up on the last answer) and
  narrates the result — see [Copilot](#copilot--the-cross-agent-assistant) for the full
  design. Shipped alongside `agents/visualization/explain.py`, an "explain this chart"
  capability available on every chart in the app, not just Copilot's own.
- **Dark/Light theming, Forecasting MASE + SARIMA, manual relationships (2026-07-18)** —
  1. **Real light/dark toggle** (`next-themes`, `frontend/src/components/theme-provider.tsx`
     + `theme-toggle.tsx`) backed by OKLCH CSS custom properties, with Plotly charts
     re-themed separately since Plotly can't read `oklch()` directly.
  2. **MASE replaces MAPE as the forecasting model-selection metric**
     (`agents/forecasting/tools/evaluation.py`) — fixes MAPE's best-known pathology
     (exploding/undefined error when actuals are near zero or negative, which could silently
     corrupt model ranking); MAPE is still shown as the familiar "%" figure but only breaks
     ties. **SARIMA** joins auto-order ARIMA in `tools/models.py` (curated order-grid + AIC
     search, no `pmdarima` dependency) — see the Forecasting section above.
  3. **Manual relationships from scratch** — the Relationship Review step now lets a user
     define any table/column pair the automatic scan never proposed at all, not just
     approve/reject what it found — see [Multi-source ingestion & storage](#multi-source-ingestion--storage).
     Not yet run through the same `validate_relationship()` check as auto-detected
     candidates — see [Design notes](#design-notes--known-limitations).
- **Live end-to-end verification pass, browser-driven (2026-07-18)** — after the Streamlit →
  full-stack migration below, the app was actually launched (Postgres + `uvicorn` + `next dev`)
  and driven through a real Chromium browser via Playwright — sign-up, project creation, file
  upload, the full Data Cleaning pipeline (schema discovery → relationships → plan → clean →
  clean-remaining → integrity → reconcile → save), then every one of Visualization, Insights,
  Forecasting, Marketing, Churn, Reports, Settings, and Account, including actually clicking
  "Generate"/"Run" on every AI agent and generating a real API key — not just `TestClient`
  calls against the API in isolation. This caught three real bugs that all prior
  endpoint-level testing had missed, because none of them are visible from the API alone:
  1. **An infinite render loop that crashed the entire Next.js dev process.**
     `RelationshipsStage`'s decision-seeding effect guarded itself with
     `Object.keys(decisions).length > 0` — for any single-table project (no relationship
     candidates at all, the common case), the seeded object is legitimately `{}`, so that
     guard is never satisfied and `setDecisions({})` fires every render, forever. Fixed with a
     `useRef` "already seeded" flag instead of an emptiness check.
  2. **A dead-end in the cleaning pipeline UI.** The Reconciliation stage only rendered a
     "Continue to save" button inside its "here are the checks" branch — for a project with
     no cross-table relationships (so zero reconciliation checks, the common single-table
     case), there was no button at all; the only way forward was clicking the "Save" stage
     indicator directly, which most users would never think to do. Fixed by always offering a
     "Continue to save" button, regardless of whether there was anything to reconcile.
  3. **A hardcoded fake `activeProjectId` default (`'proj-atlas'`, leftover from the original
     mock scaffold) that 404'd every project-scoped query for any real user**, and would still
     go stale after the first project was deleted or on a fresh reload. Fixed with a new
     `ProjectSync` component (`frontend/src/components/shared/project-sync.tsx`, mounted in
     `providers.tsx`) that auto-selects a real project once `useProjects()` resolves, plus a
     genuine "create your first project" empty state on Command Center for brand-new accounts
     instead of blank widgets.
  - Also fixed: the login page's "Continue with demo workspace" button claimed it dropped the
    user into "a fully-stocked demo workspace with the Atlas Commerce Q3 project" — that
    project was mock-era copy that never existed server-side; a demo sign-in creates a real,
    empty account like any other. Copy corrected to say so.
  - Environment gotchas hit and documented: a stale Turbopack `.next` cache from before
    `@tailwindcss/typography` was added to `package.json` 500'd every page until `rm -rf
    .next`; an unset `NEXTAUTH_SECRET` 500'd every sign-in/sign-up (see the callout in
    [Setup & installation](#setup--installation)).
  - Result: a full sign-up-to-saved-insights/forecast/marketing/churn session with **zero
    browser console errors and zero page errors** across all 9 real sections, verified twice
    (once per bug fixed) with fresh Playwright runs.
- **Streamlit → production full-stack: FastAPI backend + Next.js frontend (2026-07-17)** —
  replaces the multipage Streamlit UI entirely with a real client/server split, with every
  page rewired to real backend calls (zero hardcoded/mock data left in the frontend):
  1. **`backend/app/`** — a FastAPI service, one router per domain under `/api/v1`
     (auth, account, projects, ingestion, pipeline, dashboard, insights, forecasting,
     marketing, churn, settings, reports, assistant), importing `agents/`, `db/`, `tools/`,
     etc. exactly as they were — **no agent package was rewritten to build this**, each
     router is a thin request → engine-call → response mapping, run with `uvicorn` from the
     repo root.
  2. **Real auth** — email/password registration/login, JWT access + refresh tokens
     (`pyjwt` + `bcrypt`), every project scoped to its owner (`projects.owner_id`), enforced
     on every route via `get_owned_project`.
  3. **`frontend/`** — Next.js 16 (App Router) + React 19 + NextAuth (Credentials provider) +
     `@tanstack/react-query` for all server state + Tailwind v4/shadcn/ui, replacing a
     previously separate, fully-mocked prototype UI. Every section (Projects, Data Workspace,
     Visualization, Insights, Forecasting, Marketing, Churn, Reports, Settings, Account,
     Command Center) now calls the real backend; Plotly charts render via `react-plotly.js`
     from real figure JSON.
  4. **In-memory HITL pipeline sessions** (`backend/app/services/pipeline_sessions.py`) —
     faithfully replicates the multi-step Data Cleaning workflow (profile → schema discovery
     → relationships → per-table plan/clean → reconciliation → integrity → save) that used to
     live in `st.session_state`, with the same single-process durability guarantee, documented
     as a known limitation rather than silently assumed away.
  5. **Cross-agent grounding preserved end-to-end** — Marketing's churn integration
     (model-scored risk per RFM segment, exportable at-risk audience lists) and
     Forecasting/Marketing cross-linking all verified live, matching the original Streamlit
     behavior exactly (down to identical customer counts across the Churn and Marketing
     endpoints in the same live test run).
  6. **New platform tables**: `users`, `api_keys`, `reports` (a generic saved-report table:
     any Insights/Forecast/Marketing/Churn markdown narrative can be persisted and browsed
     later, with real ownership-scoped listing and a working cascade delete when a project is
     removed), plus `users.model_preferences` (per-user, per-agent-purpose Groq model
     override, replacing the old sidebar session-state selectors).
  7. **Streamlit fully removed** — `streamlit_app.py`, `pages/*.py`, `tools/page_data.py`,
     `agents/reporting/render.py` deleted; `streamlit` dropped from `requirements.txt`.
  - Caught and fixed several real bugs along the way that a shallower "looks right" pass
    would have missed: a `NoReferencedTableError` from a missing cross-module import at
    mapper-configure time (twice, for two different new tables); an Alembic autogenerate that
    tried to `DROP TABLE` the unmanaged legacy forecast tables (stripped by hand before
    applying, twice); a forecasting bug where the *engine* model selector ("Auto") was being
    sent to Groq as if it were an LLM model id; a missing cascade-delete relationship that
    made deleting a project with saved reports fail with a `ForeignKeyViolation`; and a
    frontend sidebar project-switcher that was still reading a hardcoded mock project list
    and would have thrown at runtime for any real project id.
  - Verification discipline: full `pytest` (320 passed) + `tsc --noEmit` + `eslint` +
    `next build` after every milestone, plus a live end-to-end smoke test per milestone
    through `fastapi.testclient.TestClient` against the real Postgres and real LLM providers
    (register → login → create project → ingest → clean → save → run the agent → assert on
    the real response → clean up) — not just static review.
- **Forecasting: honest Ensemble + universal outlier-robustness (2026-07-11)** — pushes
  forecast accuracy and interval quality further while keeping the existing "prove it
  out-of-sample or don't ship it" philosophy:
  1. **Ensemble candidate** (`tools/backtest.py: backtest_ensemble`, `inverse_error_weights`,
     `select_model`) — an inverse-error-weighted blend of the top 3 individually back-tested
     models is itself back-tested on the same rolling-origin folds as every other candidate
     and only wins (and is only ever selected by `Auto`) when its out-of-sample MAPE is
     genuinely lower than any single model's. On a synthetic seasonal-plus-trend series with
     injected outliers it beat the best individual model 3.22% vs 3.48% MAPE. Users can also
     force it directly from the model dropdown (`ForecastEngine.SUPPORTED_MODELS` now
     includes `"Ensemble"`).
  2. **Statistically-correct combined intervals** (`tools/engines.py: _combine_forecasts`) —
     an ensemble's prediction interval uses the standard forecast-combination variance
     decomposition (Bates–Granger): within-model uncertainty **plus** between-model
     disagreement, not a naive average of bounds (which understates risk when components
     disagree).
  3. **Universal MAD outlier cleaning** — the median-absolute-deviation outlier pass
     (previously Prophet-only) now runs once up front in `ForecastEngine.run` for every
     candidate model, so Naive/Drift/ETS/ARIMA/etc. no longer chase one-off spikes either.
  4. **Adaptive back-test folds** (`_adaptive_folds`) — 3 folds on short histories scaling up
     to 6 on long ones (≥180 points), for a more statistically robust model ranking as more
     data becomes available, instead of a fixed 3.
  - `model_selection_reason` now names the blend and its weights when an ensemble is chosen
     (e.g. *"Ensemble (Prophet 34%, Holt-Winters 34%, Theta 33%)"*), and the leaderboard /
     exported report surface it like any other candidate — fully grounded, since the weights
     and MAPE are real computed values, not LLM narration.
  - Tests: 8 new cases in `tests/test_forecast_upgrades.py` (weighting, blend back-testing,
     leaderboard inclusion/exclusion, forced-Ensemble success and graceful fallback).
- **Multi-source ingestion & storage upgrade (2026-07-08)** — turns DAAS from a
  single-session, single-DataFrame tool into a project-based, Postgres-backed platform:
  1. **Projects** (`db/platform_models.py`, sidebar switcher) — each gets its own Postgres
     schema (`project_<slug>`); Alembic manages the fixed platform tables.
  2. **Multi-provider LLM client** (`tools/llm_client.py`) — Groq → Anthropic → OpenAI →
     OpenRouter fallback; all 11 previous direct `Groq()` call sites migrated.
  3. **Path A rework** — multi-file upload now keeps tables **separate**
     (`ingestion/multi_table.py`) instead of collapsing them into one DataFrame (superseding
     the V3.2 `combine_datasets` contract for the multi-file case only — single-file
     behavior is unchanged).
  4. **Schema Discovery** (`schema_discovery/`) — heuristic FK/PK scoring + LLM escalation
     for ambiguous candidates.
  5. **Relationship Review** (`relationships/review.py`) — confidence-gated HITL (auto /
     explicit-approval / manual-mapping buckets), persisted per project.
  6. **Multi-table cleaning** (`agents/cleaning/multi_table.py`) — loops the existing,
     unmodified single-table LangGraph per table; found and fixed a real gap along the way:
     `agents/cleaning/executor.py`'s `exec()` had no timeout at all (unlike
     `tools/sandbox.run_analysis`), and the shared fix also corrected a subtler bug in the
     *existing* timeout mechanism (`ThreadPoolExecutor.shutdown(wait=True)` was silently
     defeating it for merely-slow code).
  7. **Reconciliation** (`reconciliation/`) — numeric-aware key normalization + orphan-rate
     before/after gating, so independent per-table cleaning can't silently orphan rows.
  8. **Real schema/DDL generation** (`db/ddl.py`, `db/loader.py`) — PK/FK constraints,
     topologically-ordered `CREATE TABLE`, bulk `COPY` load.
  9. **Semantic views + Data Manager** (`db/views.py`, `data_manager/manager.py`) — TTL-cached
     per-agent views; **the six engines needed zero changes** — they already took a plain
     DataFrame via a `data_df` parameter.
  10. **The actual page cutover** (`tools/page_data.py`) — all 6 pages now read through the
      Data Manager instead of `st.session_state.clean_df`/a flat opportunistic Postgres dump.
  11. **Google Sheets** (`ingestion/gsheets.py`) — via a GCP service account, not OAuth.
  12. **Existing-database linking** (`ingestion/db_link.py`, `db/connection_configs.py`) —
      `ReadOnlyConnector`, FK constraints trusted directly (ahead of heuristics/LLM),
      Fernet-encrypted credential storage.
  - New realistic test data: `data/multi_table_demo/` — the Bloom & Bean business normalized
    into a genuine 3-table star schema, with deliberate cross-table key-formatting drift to
    exercise Reconciliation.
  - Fixed a project-switcher bug found during live testing: creating a second project crashed
    with a Streamlit `session_state` widget-conflict error (fixed via an `on_click` callback).
  - Tests: 140 → **265 passing**; new integration tests (`@pytest.mark.integration`) exercise
    the real Docker Postgres and auto-skip when it isn't reachable.
- **V3.2 production refactor (2026-07-04)** — see [`CHANGELOG_V3.2.md`](CHANGELOG_V3.2.md)
  for full detail:
  1. **Multi-file, multi-format ingestion** (`tools/ingestion.py`) — CSV/TSV/Excel/JSON/
     Parquet with encoding fallback (incl. CP1256 Arabic) + delimiter sniffing; multiple
     files are appended (same schema) or star-schema joined (fact + dimension) with an
     HITL combination plan. Downstream contract unchanged: one primary `clean_df`.
  2. **Arabic-aware cleaning** (`tools/arabic_text.py`) — deterministic normalization in
     the profiler (diacritics/tatweel, alef variants, Arabic-Indic digits → Western,
     Arabic placeholders → NaN), `arabic_ratio` in the column profile, planner prompt
     taught about it.
  3. **Insights quality redesign** (`agents/insights/findings.py`, since rebuilt and renamed
     to `evidence.py` — see *The ten agents* §4) — deterministic ranked evidence digest +
     slimmed prompt payload replaces the raw-JSON dump; derived figures are groundable;
     `<think>` blocks stripped; follow-up chat keeps history.
  4. **Churn production pipeline** — multi-cutoff training panel, **out-of-time
     validation**, `HistGradientBoostingClassifier`, calibration on the holdout,
     permutation importances, momentum features (orders_last_30/60/90, spend_trend).
     Out-of-time AUC 0.93 on Bloom & Bean. *(Breaking: model name is now
     `HistGradientBoosting`.)*
  5. **Churn ↔ Marketing integration** — marketing payload gains a model-scored `churn`
     section (risk per RFM segment, expected revenue at risk, exportable at-risk audience
     CSVs); prompts prefer model scores over RFM proxies. Convention: `_`-prefixed payload
     keys are internal and stripped before any LLM prompt.
  6. **Forecast chart readability** — forecast line/band anchored to the last actual,
     full computed horizon plotted, visible 80% band, granularity-aware rolling average,
     "forecast →" divider (fixes the single-floating-dot chart).
  - Tests: 100 → **140 passing** (4 new test files).
- **Churn & Marketing correctness/quality** — fixed a shared KPI bug where `new_customers`
  equalled *total* (so `new_customer_pct` was always ~100%); new vs returning is now a correct
  non-overlapping partition (one-time vs repeat), which also corrects the Marketing report.
  Churn now **ranks at-risk customers by expected value at risk** (probability × spend) with a
  per-customer `expected_loss` and an **expected-revenue-at-risk** KPI, and **calibrates** the
  churn probabilities so scores are trustworthy. Tests updated in `tests/test_churn.py`,
  `tests/test_kpi_engine.py`.
- **Forecasting accuracy & honesty** — additive metrics are now summed (not averaged) per
  period; a **granularity control** (daily/weekly/monthly) lets volatile daily series be
  forecast where the back-test error is meaningful (on Bloom & Bean, weekly revenue moved from
  a daily 90% MAPE / 37% confidence to Seasonal Naive at 29% MAPE / **70% confidence, +68%
  skill vs naive**); three professional models added (**Theta, ETS, ARIMA**); the confidence
  score is now a principled transform of back-test error and reports **"not back-tested"**
  instead of a fake number; metric ranking prioritises real revenue. Tests:
  `tests/test_forecast_upgrades.py`.
- **Executive Visualization dashboard** — the Visualization agent now leads with a
  deterministic, themed, board-quality dashboard (KPI band + curated charts, one house
  Plotly theme, live date/category slicers, one-click interactive-HTML export) instead of a
  tab strip of ad-hoc LLM charts; AI charts remain for anything custom, matched to the theme.
  New: `agents/visualization/{theme,builder,export}.py`.
- **Professional reporting layer (`agents/reporting/`)** — all four narrative agents
  (Insights, Marketing, Churn, Forecasting) now render through one shared presentation with
  a run-metadata header, a KPI strip, a **grounding/faithfulness badge** that verifies every
  figure against the computed data, and one-click export to styled **HTML (print-to-PDF)** and
  Markdown. The forecast narrative is assembled deterministically from the pipeline output
  (`agents/forecasting/report.py`). No new dependencies. Tests: `tests/test_reporting.py`.
- **Forecasting** — replaced the placeholder "Auto" (which always ran Prophet and reported
  in-sample metrics) with a real multi-model library + rolling-origin cross-validation +
  honest out-of-sample selection, a back-test leaderboard, and a skill-vs-naive score.
- **Churn Prediction agent** — new sixth agent (windowed labelling + gradient boosting,
  AUC ≈ 0.93 on the Bloom & Bean data) with a retention-strategy LLM layer.
- **Realistic dataset generator** — `generate_business_data.py` (Bloom & Bean).
- **pandas 3.x compatibility + revenue-column detection fix** in
  `agents/analytics/schema_intel.py` / `quality.py`.
