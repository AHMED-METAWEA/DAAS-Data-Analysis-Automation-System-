# Technical Overview — DAAS

A concise, implementation-level reference for the whole system: what every layer does, where
its code lives, and how the pieces connect. For setup/run instructions and product-level
framing, see [`README.md`](README.md). This document assumes you can already read the codebase
and just want the map.

---

## 1. What this is, in one paragraph

DAAS is a multi-agent BI platform: a user connects data (files, Google Sheets, or a
linked database), the system discovers schema/relationships, cleans it with an LLM-planned,
deterministically-executed, human-reviewed pipeline, stores it in a dedicated Postgres schema,
and then **ten specialized agents** analyze it — Cleaning, Analytics, Visualization, Insights,
Forecasting, Marketing, Churn, Root Cause, Autonomous Monitoring and the CRM — with an
**Analyst Copilot** supervising on top, routing one question to 1–3 of them and synthesizing
the answers. Deterministic math first, LLM narration second, every report grounding-checked
against the real computed numbers. The system was originally a single Streamlit app; it is now
a **FastAPI backend** (`backend/app/`) + **Next.js frontend** (`frontend/`) with real JWT auth,
with every existing Python agent package reused unmodified.

Two structural properties distinguish it from the July version this document first described.
**Monitoring** re-runs those same engines on a schedule with no browser involved and pushes a
briefing to email/Telegram/WhatsApp. And the **CRM** is the platform's only *stateful* layer:
every other agent computes a number and forgets it, while the CRM writes one record per
customer per snapshot date and never recomputes it.

---

## 2. Architecture at a glance

```
Next.js 16 (frontend/)  ──HTTPS/JSON──►  FastAPI (backend/app/)  ──imports──►  agents/ db/ tools/
React 19, NextAuth,                     18 routers under /api/v1,        ingestion/ schema_discovery/
react-query, Zustand,                   JWT auth on every project        relationships/ reconciliation/
Tailwind v4 + shadcn/ui,                route; SSE on copilot only       integrity/ data_manager/ graphs/
next-intl (EN/AR + RTL)                            │                     monitoring/ notifications/
                                                   │                                │
                                        APScheduler hosted IN the                   │
                                        API process (MONITORING_                    ▼
                                        SCHEDULER=1) or standalone       Postgres (docker-compose,
                                        via python -m monitoring.worker  port 5433) — platform tables
                                                                         (public schema) + one
                                                                         project_<slug> schema per
                                                                         project (user data)
```

Nothing under `agents/`, `db/`, `tools/`, `ingestion/`, `schema_discovery/`, `relationships/`,
`reconciliation/`, `integrity/`, `data_manager/`, `graphs/`, `core/` was rewritten to build the
API — `backend/app/` is a thin request/response layer that imports and calls those packages
exactly as the original `pytest` suite and the old Streamlit pages did. `uvicorn` is run from
the repo root so these resolve as ordinary top-level Python packages.

`monitoring/` follows the same rule from the other direction: it is a **second front door** onto
the identical engines, not a parallel implementation. That is deliberate and load-bearing — a
scheduled finding and a finding you would get by opening the app cannot disagree, because they
are the same code path with a different trigger.

---

## 3. Repository layout

| Path | What it is |
|---|---|
| `backend/app/` | FastAPI application (see §4) |
| `frontend/` | Next.js application (see §10) |
| `agents/` | The 11 agent packages — cleaning, analytics, visualization, insights, forecasting, marketing, churn, rootcause, crm, copilot, and the shared `reporting/` layer (see §6, §7) |
| `db/` | SQLAlchemy models, Alembic-managed platform tables, DDL/loader/views for per-project schemas |
| `alembic/` | Migrations for the platform tables only (`db/base.py`'s metadata) |
| `tools/` | Cross-cutting utilities: LLM client, sandbox exec, ingestion parsing, Arabic text, profiler helpers |
| `ingestion/` | Multi-source ingestion — files/Google Sheets/existing-database, all converging on one contract |
| `schema_discovery/` | Heuristic + LLM-escalated FK/PK relationship detection |
| `relationships/` | Confidence-gated HITL review + persistence of approved relationships |
| `reconciliation/` | Cross-table key normalization + orphan-rate gating |
| `integrity/` | Pre-save referential/PK/dtype/duplicate validation |
| `data_manager/` | TTL-cached semantic-view reader — the one seam between storage and every agent |
| `graphs/` | LangGraph graph builders for the cleaning pipeline |
| `monitoring/` | The autonomous analyst: scheduler, runner, rules, briefing composer, EN/AR evidence, metric-snapshot history, standalone worker |
| `notifications/` | Delivery channels behind one `Channel` interface — in-app, SMTP email, Telegram, WhatsApp (Meta Cloud API + Twilio) |
| `core/` | `GraphState` TypedDict shared by the cleaning graphs |
| `models/` | `DatasetProfile`/`ColumnProfile` pydantic models (profiler output shape) |
| `prompts/` | All LLM system prompts, one subfolder per agent |
| `data/`, `Generate_Data/` | Sample datasets + the generators that produced them |
| `tests/` | 882-test pytest suite across 68 files (deterministic engines, cleaning contracts, root-cause search, monitoring, CRM, streaming, token metering + the DB/DDL/storage layer). LLM-free by design; Postgres-backed tests marked `@pytest.mark.integration` |
| `main.py` | Standalone CLI: runs the cleaning pipeline against `data/sample.csv`, no web stack needed |

---

## 4. Backend (`backend/app/`)

### 4.1 Structure

```
backend/app/
  main.py            FastAPI(), CORS (FRONTEND_ORIGIN), startup hook → ensure_platform_tables()
  core/
    config.py         Settings dataclass, reads .env directly (no pydantic-settings)
    security.py        bcrypt hashing + pyjwt encode/decode (access + refresh tokens)
  api/
    deps.py            get_db, get_current_user, get_owned_project — the auth/ownership spine
                        — plus bind_usage_context, which is async ON PURPOSE (see §4.6)
    v1/                 18 router modules, all included under /api/v1 in main.py
  schemas/             pydantic request/response models, one file per router
  services/
    views.py            resolve_view(project_id, view_name) — wraps data_manager, raises
                         400/502 with a clear message instead of ever fabricating data
    json_safe.py         numpy/pandas → plain-JSON sanitizer for arbitrary nested payloads
    previews.py          shared table-preview builder
    analysis_chat.py     run_grounded_chat() — the shared "answer a question, optionally
                         executing generated Python against a DataFrame in the sandbox"
                         pattern, reused by Insights chat and the Copilot's general route.
                         Runs a post-hoc grounding check on the draft answer and pays for
                         one corrective re-ask only on a genuine material mismatch
    pipeline_sessions.py  in-memory, TTL-based store for the multi-step Data Cleaning HITL
                         workflow (see §4.4)
    copilot_stream.py     the SSE generator — meta → step → token → done frames for a
                         multi-step Copilot answer
    copilot_sessions.py   in-memory TTL cache of the LAST turn's route + raw_payload, so a
                         follow-up ("explain more") is answered against real numbers
    insights_service.py   insights orchestration behind the router
    rootcause_service.py  root-cause orchestration behind the router
    monitoring_service.py channel/schedule/alert operations shared by router and runner
    dashboard_service.py  dashboard assembly
    usage_recorder.py     binds each LLM attempt to the user/project that caused it and
                         persists it to llm_usage_events (see §4.6)
```

### 4.2 Auth model

Real email/password accounts. `POST /auth/register` and `/auth/login` return a JWT access
token (short-lived) + refresh token (long-lived), signed with `JWT_SECRET_KEY` (HS256,
`pyjwt`). Passwords are `bcrypt`-hashed. `get_current_user` decodes the bearer token and loads
the `User` row; `get_owned_project` additionally 403s if `project.owner_id` doesn't match the
caller. There is **no server-side session/refresh-token table** — tokens are stateless, so
there is no way to list or revoke one "device's" session; rotating `JWT_SECRET_KEY` is the
only way to invalidate every token at once. API keys (`ApiKey` model) are a separate,
independent credential: bcrypt-hashed, prefix-only display, reveal-once at creation.

### 4.3 Full endpoint surface

All paths below are relative to `/api/v1`. `{project_id}` routes all depend on
`get_owned_project`; everything else depends on `get_current_user` except registration/login.

| Router | Prefix | Endpoints |
|---|---|---|
| `auth.py` | `/auth` | `POST /register`, `POST /login`, `POST /refresh`, `GET /me` |
| `account.py` | `/account` | `PATCH /profile`, `POST /password`, `GET/POST /api-keys`, `DELETE /api-keys/{id}` |
| `projects.py` | `/projects` | `GET`, `POST`, `GET /{id}`, `DELETE /{id}` |
| `ingestion.py` | `/projects/{id}/ingest` + `/ingestion` | `POST /files`, `POST /gsheet`, `POST /db-link`, `POST /ingestion/db-link/test` (unscoped) |
| `pipeline.py` | `/pipeline/{session_id}` | `POST /schema-discovery`, `POST /relationships`, `POST`/`PATCH /tables/{t}/plan`, `POST /tables/{t}/clean`, `POST /clean-remaining`, `GET /reconciliation`, `POST /integrity`, `POST /save` |
| `dashboard.py` | `/projects/{id}/dashboard` | `GET` (full dashboard), `GET /snapshot` (lightweight KPIs), `GET /export-html`, `POST /charts/ask`, `POST /charts/auto` |
| `insights.py` | `/projects/{id}/insights` | `POST` (generate report), `POST /chat` |
| `forecasting.py` | `/projects/{id}/forecast` | `GET /metrics`, `POST /run` |
| `marketing.py` | `/projects/{id}/marketing` | `POST /run`, `POST /campaigns`, `POST /ad-copy`, `POST /chat` |
| `churn.py` | `/projects/{id}/churn` | `POST /run`, `POST /retention-plan` |
| `rootcause.py` | `/projects/{id}/root-cause` | `GET /options`, `POST` (run the drill-down) |
| `crm.py` | `/projects/{id}/crm` | `POST /refresh`, `GET /portfolio`, `GET /customers`, `GET /customers/{customer_id}`, `GET /snapshots`, `GET /ranking-comparison` |
| `copilot.py` | `/projects/{id}/copilot` | `POST /ask`, `POST /ask/stream` (SSE) |
| `charts.py` | `/projects/{id}/charts` | `POST /explain` (explain-this-chart) |
| `monitoring.py` | `/monitoring` | **21 endpoints, scoped per row rather than by prefix** — channels (`GET /channel-types`, `GET`/`POST /channels`, `PATCH`/`DELETE /channels/{id}`, `POST /channels/{id}/test`), schedules (`GET /schedules`, `GET`/`PATCH`/`DELETE /schedules/{id}`, `POST /schedules/{id}/preview`, `POST /schedules/{id}/run`), runs (`GET /runs`, `GET /runs/{id}`), alerts (`GET /alerts`, `GET /alerts/summary`, `PATCH /alerts/{id}`, `POST /alerts/read-all`), and `GET /scheduler` |
| `settings.py` | `/settings` | `GET /providers`, `GET`/`PATCH /models` |
| `reports.py` | mixed | `GET /reports`, `GET /reports/{id}`, `GET`/`POST /projects/{id}/reports` |
| `assistant.py` | `/projects/{id}` | `POST /ask` — **orphaned**: real and working, has a matching frontend hook, but no page calls it (§12) |

Two rows deviate from the table's shape. `pipeline.py` also carries
`POST /tables/{t}/plan/opinion` (the LLM's opinion on a user-edited plan). And `monitoring.py`
cannot enforce ownership through its prefix, because a *schedule* belongs to a project while a
*channel* belongs to a user — so it checks per row instead.

### 4.4 The in-memory pipeline-session store

`pipeline_sessions.py` replicates what Streamlit's `st.session_state` gave the cleaning
workflow for free: a `pipeline_session_id` (returned by the ingestion endpoints) keys a
TTL-expiring, single-process dict holding the raw/interim DataFrames between ingestion and
"Save to Project". This is a **known, documented limitation** — not a regression — but it does
mean a backend restart mid-session loses unsaved work, and it would need swapping for a shared
store (Redis) before running more than one backend replica.

### 4.5 Per-agent model resolution pattern

Every LLM-calling endpoint resolves its model the same way:
`payload.model or current_user.model_preferences.get(purpose) or DEFAULT_X_MODEL`. Only Groq
honors this override (`tools/llm_client.py`); every other provider in the fallback chain always
serves a fixed model per `purpose` from `tools/llm_provider_models.py`. Preferences persist in
`users.model_preferences` (a JSON column), editable via `GET/PATCH /settings/models`, and a
PATCH naming a model outside `AVAILABLE_MODELS` is rejected rather than stored.

`agents/constants.py` holds the catalogue and all eleven `DEFAULT_*_MODEL` entries. Those two
lists are hand-maintained and can drift apart with nothing noticing — when Groq decommissioned
the Llama models on 2026-08-16, eight defaults pointed at a model that no longer existed, and
because every agent has a deterministic floor most would have degraded *quietly* rather than
erroring. `tests/test_constants.py` now asserts by reflection that every default is a member of
`AVAILABLE_MODELS` and that no retired id has reappeared. All eleven currently default to
`openai/gpt-oss-120b`.

### 4.6 Token metering, and the threadpool trap

Every provider attempt writes one `llm_usage_events` row (`tools/llm_usage.py` →
`db/usage_models.py`); a chain that fails over from Groq to Anthropic writes **two**, because
collapsing them into one "successful" row would hide a chain quietly running on its second
choice. `complete()` returns a bare `str` and has 23 call sites, so rather than change its
signature the client **pushes** events to a sink installed in the surrounding context — no
agent knows metering exists.

Three facts about that plumbing are easy to get wrong, and each fails silently:

1. **`bind_usage_context` must be an `async` dependency.** FastAPI runs `def` endpoints and
   `def` dependencies in a threadpool, where a ContextVar `set()` lands on a throwaway copy the
   endpoint never sees. Sync dependencies can still *mutate* the already-bound object, which is
   how `get_owned_project` attaches the project id.
2. **Usage arrives on a final stream chunk whose `choices` list is empty** — precisely what
   `if not chunk.choices: continue` was skipping — and OpenAI-compatible providers only send
   that chunk when asked via `stream_options={"include_usage": True}`.
3. **The monitoring worker has no request and therefore no ambient sink**, so it must bind a
   context explicitly or its tokens land on nobody's bill.

Reading those rows back — pricing, a `/usage` endpoint, a page — is not built (§12). The table
has **no cost column** on purpose: token counts are ground truth and never change, whereas a
stored cost silently rots as provider prices drift.

---

## 5. Database

### 5.1 Platform tables (Alembic-managed, `public` schema)

| Table | Model | Purpose |
|---|---|---|
| `users` | `db/auth_models.py::User` | email, bcrypt hash, name, role, organization, timezone, bio, `model_preferences` JSON |
| `api_keys` | `db/auth_models.py::ApiKey` | user-issued programmatic keys — prefix + bcrypt hash only |
| `projects` | `db/platform_models.py::Project` | name, slug (→ `project_<slug>` schema), `data_source_mode`, `owner_id` FK → users |
| `relationships` | `db/platform_models.py::Relationship` | approved/pending cross-table relationships per project |
| `connection_configs` | `db/platform_models.py::ConnectionConfig` | Fernet-encrypted external-DB / Sheets credentials |
| `reports` | `db/report_models.py::Report` | saved markdown narrative from any of Insights/Forecast/Marketing/Churn, cascade-deletes with its project |
| `notification_channels`, `schedules`, `monitor_runs`, `alerts`, `delivery_logs`, `metric_snapshots` | `db/monitoring_models.py` | The autonomous analyst. A run record is kept **separately** from its alerts on purpose: without one, *"no at-risk findings"* and *"the job has been failing for six days"* look identical on the page, and only one is good news. `metric_snapshots` is the *recorded* past a delta is measured against — never a recomputed one |
| `crm_snapshots`, `customer_state` | `db/crm_models.py` | The only stateful customer layer — one snapshot row per refresh, one `customer_state` row per customer within it, `UNIQUE (project_id, customer_id, snapshot_date)`. Columns are grouped identity / observed / derived / predicted / prioritisation and computed **in that order**, so a model output can never overwrite a measured fact |
| `llm_usage_events` | `db/usage_models.py` | One row per provider *attempt* (a fallback is two rows). Tokens only — **no cost column** (§4.6) |

`Project.reports` has `cascade="all, delete-orphan"` — deleting a project via the ORM
(`db/projects.py::delete_project`, used by `DELETE /projects/{id}`) cleanly cascades to its
reports. **Gotcha already hit once:** adding a new table that relates to `Project` requires
importing the new module from `db/platform_models.py` (after the class definitions, to avoid a
circular import) or from `db/init_platform.py`/`alembic/env.py` — otherwise SQLAlchemy's mapper
configuration fails with `NoReferencedTableError`/`InvalidRequestError` the first time any
Project-touching code path runs, because the related class was never registered in the
class registry.

### 5.2 Per-project schemas (unmanaged, dynamic)

Each project gets its own Postgres schema `project_<slug>` created by `db/loader.py` at "Save
to Project" time — real `CREATE TABLE` with inferred column types, real PK/FK constraints from
the approved relationship graph, topologically ordered, bulk-loaded via `COPY`. These are
**deliberately invisible to Alembic** (`alembic/env.py` does not set `include_schemas=True`) so
autogenerate can never propose dropping user data. `db/views.py` then exposes one semantic view
per agent (`analytics`, `forecast`, `marketing`, `customer_360`) — a SQL join of the fact table
with its directly-related dimensions — and `data_manager/manager.py` TTL-caches reads of those
views. This view layer is the **only** thing every agent actually depends on; none of them know
or care how many tables a project started as.

### 5.3 Legacy unmanaged tables (dead code, left in place)

`agents/forecasting/storage.py`, `agents/marketing/storage.py`, `agents/churn/storage.py` each
`CREATE TABLE IF NOT EXISTS` their own flat, cross-project run-history tables
(`forecast_runs`/`forecasts`/`forecast_evaluations`/`forecast_explanations`/`forecast_history`,
`marketing_runs`/`marketing_segments`/`marketing_campaigns`, `churn_runs`/`churn_scores`) via
`tools/db_tools.py`. These were wired to the old Streamlit pages' "Save to PostgreSQL" buttons.
**None of the new FastAPI routers call them** — the `reports` table is the real persistence
path now. The modules and their tests still exist and still pass; they are orphaned, not
broken. Alembic's autogenerate proposes dropping them every time a new platform-table migration
is generated — this is expected and the dangerous `DROP TABLE` lines must be stripped by hand
from the migration file before applying it (this has happened three times across this
project's migrations so far).

---

## 6. The Data Cleaning agent (`agents/cleaning/`, `graphs/`, `core/state.py`)

**This agent was rebuilt in August: the LLM plans the cleaning, it no longer performs it.**
The distinction is the whole design. A generated `clean_data(df)` function is only as
trustworthy as the run you happened to watch; a plan of **typed operators** executed by tested
code is deterministic, re-runnable, and unit-testable.

`Profiler` (deterministic — Arabic normalization via `tools/arabic_text.py`, placeholder→NaN,
`DatasetProfile`, then `tools/defect_detection.py`'s 19 checks producing measured
`DefectFinding`s) → `Planner` (`plan_from_defects()` builds a complete typed plan with **no LLM
at all**; the model then *refines* that baseline and every way it can fail — unreachable,
unparseable, invented operator, malformed parameter — degrades back to it) → **human
reviews/edits the plan**, optionally asking for the model's opinion on their edits
(`/tables/{t}/plan/opinion`) → `Executor` (runs operators from `tools/cleaning_ops.py`'s
21-entry `REGISTRY`) → `Validator` + `agents/cleaning/invariants.py`.

Four contracts hold it together, and each is pinned by its own test file:

- **Operators are pure `(df, columns, params) -> df`.** The framework diffs before and after
  and computes the ledger itself, so a step cannot *claim* a change it did not make.
- **Authorization over resemblance.** `check_invariants()` derives what the plan authorized and
  rejects every other observed difference — unauthorized value changes, null fills, column
  drops/adds, row additions, dtype changes, join-key modification, key-uniqueness regressions.
  "The output still looks reasonable" is not a check.
- **Free-text steps are bounded, not proven.** A step the user types by hand still generates
  Python, but restricted to the columns it names and re-checked by the same invariant gate,
  with violations fed back to the Coder for repair (≤2 retries).
- **Flagged is not fixed.** Outliers, negative quantities, arithmetic mismatches and duplicate
  business keys are marked in `__is_outlier` / `__out_of_range` / `__mismatch` /
  `__is_duplicate_key` columns rather than silently corrected, because each is genuinely
  ambiguous.

Two graphs remain: `graphs/planner_graph.py` (profiler→planner) and `graphs/cleaning_graph.py`
(coder→executor→retry≤2→validator, where a validator rejection re-enters the Coder with the
rule and column named rather than just a traceback), invoked from
`backend/app/api/v1/pipeline.py`'s `/tables/{t}/plan` and `/tables/{t}/clean`. For multi-table
projects, `agents/cleaning/multi_table.py` loops the same graph once per non-primary table
(`/clean-remaining`) — it also holds `clean_table_deterministically()`, the no-LLM floor — then
`reconciliation/` normalizes join keys and gates on orphan-rate delta, `integrity/` runs
pre-save checks, and `/save` calls `db/loader.py`.

---

## 7. The nine analytical agents, and the Copilot above them

Every one follows the same shape: a **deterministic engine** (pure pandas/scikit-learn/Prophet,
computes real numbers) feeding an **LLM narrative layer** (writes prose *about* those numbers,
never invents them) — verified by `agents/reporting/grounding.py::check_grounding()`, which
extracts every currency/percentage/large-number figure from a generated report and confirms it
traces back to the computed payload.

| Agent | Deterministic engine | LLM layer | `purpose` string |
|---|---|---|---|
| Analytics | `agents/analytics/engine.py` — schema intel, KPIs, time-series, quality | — (consumed by Insights, Root Cause and Monitoring) | — |
| Visualization | `agents/visualization/builder.py` — curated executive dashboard, themed Plotly | `dashboard.py` (auto chart-set), `viz_graph.py` (chat-to-chart), `explain.py` (explain-this-chart) | `viz`, `dashboard`, `explain_chart` |
| Insights | `agents/analytics/engine.py` + `agents/insights/evidence.py` (the ranked evidence digest Monitoring's alerts also use) + `figures.py` (the citation registry) + `decision_metrics.py` | `agents/insights/agent.py` — template-selected report, `strict_verify.py` | `insights` |
| Forecasting | `agents/forecasting/tools/` — model library incl. SARIMA, rolling-origin back-test with the 1-SE rule, MASE/RMSSE, conformal intervals, anomaly + diagnostic passes | `agents/forecasting/interpreter.py` — business narrative | `forecast` |
| Marketing | `agents/marketing/segmentation.py` (RFM) + `engine.py` (marketing KPIs, channel performance, optional churn integration) | `agents/marketing/agent.py` — strategy / campaigns / ad copy | `marketing` |
| Churn | `agents/churn/model.py` — multi-cutoff panel, out-of-time validation, `HistGradientBoostingClassifier`, sigmoid calibration, SHAP per-prediction drivers | `agents/churn/agent.py` — retention strategy | `churn` |
| Root Cause | `agents/rootcause/` — `dimensions` (what may be sliced), `measures` (what is explained, over which windows), `search` (beam search + two exact pruning bounds + the noise model), `engine` | `narrate.py` — citation-token prose only; the model never types a digit | `insights` |
| CRM | `agents/crm/snapshot.py` — pure `DataFrame → [CustomerRecord]`, reusing churn features and the marketing RFM quintiles | **none** — this package has no LLM and no FastAPI import at all | — |
| Monitoring | `monitoring/rules.py` — the same evidence engine + recorded snapshot deltas + the owner's targets + freshness/quality | **none in the delivery path** — `briefing.py` composes from templates so a briefing arrives whether or not any provider is reachable | — |

**Analyst Copilot** (`agents/copilot/`) sits above these rather than beside them: `planner.py`
decomposes a question into 1–3 steps, `router.py` picks the agent per step, `graph.py` runs them
and `narrate.py` synthesizes one answer, streamed token by token over SSE. It reads no view of
its own — everything it says comes from an agent it invoked.

All of these modules are **framework-agnostic** — no Streamlit import, no FastAPI import; they
take plain DataFrames/dicts and a `model: str | None` parameter and return plain dicts/strings.
That is what let the FastAPI migration reuse every one of them unmodified, and what lets
`monitoring/runner.py` re-run them on a cron with no browser in the loop.

---

## 8. Multi-source ingestion

Three paths converge on one contract (`dict[str, DataFrame]`, each file/tab/table kept
separate — never collapsed into one):

- **Path A (files)** — `tools/ingestion.py::load_tabular_file` (CSV/TSV/Excel/JSON/Parquet,
  encoding fallback, delimiter sniffing) + `ingestion/multi_table.py`.
- **Path B (existing database)** — `ingestion/db_link.py::ReadOnlyConnector` (Postgres/MySQL/
  MSSQL via SQLAlchemy `inspect()`, enforces SELECT-only at the code level — not a substitute
  for a genuinely read-only DB credential). Real FK constraints are trusted directly
  (confidence 1.0), ahead of heuristic/LLM detection.
- **Path C (Google Sheets)** — `ingestion/gsheets.py` via a GCP service account (`gspread`),
  one DataFrame per worksheet tab.

All three then go through the same `schema_discovery/` → `relationships/` → cleaning →
`reconciliation/` → `db/loader.py` pipeline described in §6.

---

## 9. Autonomous monitoring & delivery (`monitoring/`, `notifications/`)

The platform's second front door. A `Schedule` row (cron + timezone + language + channels)
drives `monitoring/scheduler.py` (APScheduler), which is hosted **inside the API process** by
default — `MONITORING_SCHEDULER=1`, so `uvicorn` + `next` is already a complete deployment with
no broker and no third service. Set it to `0` and run `python -m monitoring.worker` to host it
standalone instead. Running both is safe: every run takes a **Postgres advisory lock**, so a
duplicate is skipped rather than delivered twice.

`monitoring/runner.py` executes one run end to end: resolve the view → re-run the *same*
analytics engine the website uses → `rules.py` ranks candidate findings by money at stake
through `agents/insights/evidence.py` → drill into the top one via the Root Cause engine →
`briefing.py` composes the message → `notifications/registry.py` delivers it → the run, its
alerts, and its `MetricSnapshot`s are persisted.

Four properties are load-bearing, and each exists because the alternative fails quietly:

- **No model in the delivery path.** `briefing.py` composes from templates over
  already-computed numbers, so the 07:00 briefing arrives whether or not any provider is
  reachable or in credit. It is also why a fabricated figure is structurally impossible here.
- **Deltas are measured against a *recorded* past** (`history.py` → `metric_snapshots`), never
  a recomputed one — otherwise "down 5% since last week" silently means something different
  every time the data is re-cleaned.
- **Alerts are fingerprinted by identity, not value**, so a standing problem is recognised
  tomorrow by the cooldown instead of re-alerting daily until the user mutes the whole feature.
- **Arabic is composed, not translated.** Each finding has a stable `key`; `evidence_ar.py`
  holds an Arabic sentence per key carrying the *identical* `{{citation}}` tokens, and the
  engine substitutes the same computed values into either language. `tools/localize.py` renders
  months and timestamps per language, because `strftime` would wedge an English month into an
  Arabic sentence.

`notifications/` puts every channel behind one `Channel` interface — `inapp` (always works, no
credentials), `email` (SMTP, stdlib only), `telegram` (Bot API), and `whatsapp` (Meta Cloud API
for production, Twilio for demos). `registry.py` also publishes the setup-form contract the UI
renders from, so adding a channel does not mean editing the frontend. Credentials are
Fernet-encrypted at rest with `FERNET_KEY`, the same helper the external-DB connection configs
use. WhatsApp's 24-hour free-form window is why scheduled briefings need an approved template
name; short channels (4096-char cut) get a purpose-built executive summary plus a link while
email gets the full report, with a test pinning that the summary can never state a figure the
report does not.

---

## 10. Frontend (`frontend/`)

### 10.1 Structure

```
frontend/src/
  app/
    (app)/{command-center,projects,data,visualization,insights,forecasting,marketing,
           churn,root-cause,monitoring,crm,copilot,reports,account,settings}/
                                        15 sections, one route each
    api/auth/[...nextauth]/route.ts    NextAuth Credentials provider → FastAPI /auth/login
    login/                              real sign-in/sign-up page
    page.tsx                            public landing page
    providers.tsx                       QueryClient + ThemeProvider + LocaleProvider
  components/
    sections/       one component per section — all real, zero mock data. Two are embedded
                     rather than routed: relationship-erd.tsx (the @xyflow/react schema
                     graph inside the Data page) and insights-audit.tsx (figure provenance,
                     ranking rationale, and what the data provably could not answer)
    landing/         the unauthenticated marketing page
    shared/          app-shell, command-palette, plotly-chart (+ the explain-this-chart
                     dialog), session-sync, project-sync, section-route-sync,
                     save-report-button, states (Loading/Error/Empty), theme-toggle,
                     language-toggle, chat-message + ai-response-card + agent-indicator
                     (Copilot), pipeline-progress, kpi-card, dashboard-blocks,
                     providers-manager (the Settings model picker)
    theme-provider.tsx / locale-provider.tsx      the two cross-cutting providers (§10.5)
    ui/              shadcn/ui primitives
  messages/{en,ar}/  20 namespace pairs, one per section, aggregated by index.ts
  lib/
    api/client.ts     apiFetch() — the one place auth headers / base URL / error shape live
    api/use-api.ts     binds apiFetch to the NextAuth session's access token
    queries/           one file per domain — react-query hooks, nothing else touches fetch
    store.ts            Zustand — UI-only state (active section, active project id,
                         command-palette open, cross-page artifacts like the last
                         Forecast/Churn run so Marketing can ground on them)
```

Adding a section means adding one route folder, one `sections/` component, one `queries/` file
and one `{en,ar}` message pair, then registering it in `store.ts`'s `Section` union, the nav and
the command palette. Nothing else moves — the repetitiveness is the point.

### 10.2 State management split

**React Query** owns everything that comes from the server (projects, pipeline results,
dashboards, reports, settings) — no server data is ever duplicated into Zustand. **Zustand**
owns only client-only UI state. `ProjectSync` and `SessionSync` (mounted in `providers.tsx`)
are the two bridges that keep Zustand's `activeProjectId`/`user` fields honest against the real
`useProjects()`/NextAuth session data — see the "Recent enhancements" entry in `README.md` for
why `ProjectSync` exists (a real bug: a hardcoded mock-era default 404'd every real project).

### 10.3 Auth

NextAuth Credentials provider calls the FastAPI backend directly; the returned access token is
embedded in the NextAuth JWT session and read by `useApi()` for every subsequent call.
`middleware.ts` redirects unauthenticated requests away from the `(app)` route group.
Google/GitHub SSO buttons only render once the corresponding env vars are set — the login page
never shows a broken SSO button.

### 10.4 Charts

Every chart is a real Plotly figure (`data`/`layout` JSON) computed server-side and rendered by
`frontend/src/components/shared/plotly-chart.tsx` (`react-plotly.js`, dynamically imported with
`ssr: false` since Plotly touches `window`). Plotly cannot parse `oklch()`, so
`agents/visualization/theme.py` re-themes figures in hex/rgba rather than reusing the CSS
custom properties the rest of the UI runs on — a silent-failure trap: an `oklch()` colour is
simply dropped and the chart renders in Plotly's defaults.

### 10.5 Theming and internationalization

Two client providers wrap the whole app in `providers.tsx`.

**`theme-provider.tsx`** is next-themes over OKLCH custom properties in `globals.css`, plus a
12-step type scale that **overrides** Tailwind's defaults instead of sitting beside them — so
shadcn's `text-sm` and a section's `text-sm` are the same size. A `no-restricted-syntax` ESLint
rule rejects `text-[13px]` in both `className` strings and template literals, which is what
keeps the scale from eroding back into the 27 hardcoded pixel values it replaced.

**`locale-provider.tsx`** is next-intl. Three decisions in it are worth knowing before editing:

- **Each section owns its own `{en,ar}/<section>.json` pair** (20 namespaces per locale,
  aggregated in `messages/index.ts`) rather than two large files — so independent translation
  passes never touch the same file, and a missing key blanks one screen instead of the app.
- **It renders the SSR-safe `en` default first and upgrades post-mount on purpose.** Reading
  `localStorage` in the `useState` initializer would make the client's first render disagree
  with the server's on actual translated *text* — not an attribute `suppressHydrationWarning`
  can paper over.
- **It sets `document.documentElement.dir = 'rtl'` for Arabic**, which is why layout code uses
  Tailwind's logical properties (`ms-`/`me-`, `ps-`/`pe-`, `text-start`/`text-end`) rather than
  left/right. One stylesheet then serves both directions with no mirrored variants to maintain.

Arabic prose that carries *numbers* is composed on the server, not translated in the browser —
see `monitoring/evidence_ar.py` and `tools/localize.py` (§9).

---

## 11. Testing & verification

- **`python -m pytest -q`** — **882 tests across 68 files** (`tests/`), covering every
  deterministic engine, the cleaning contracts, the root-cause search, monitoring, the CRM,
  Copilot's SSE streaming, token metering, the sandbox, and the
  DB/DDL/storage/reconciliation/schema-discovery layer. LLM-free by design (mocked at the
  `complete()`/`stream_complete()` boundary). Postgres-backed tests are marked
  `@pytest.mark.integration` and auto-skip if `docker compose up -d` isn't running.

  A few files are the regression suite for an *architecture* rather than incidental coverage,
  and are worth reading before changing the code they guard:

  | File(s) | What they pin |
  |---|---|
  | `test_cleaning_ops.py`, `test_cleaning_invariants.py`, `test_cleaning_graph.py`, `test_defect_detection.py`, `test_cleaning_already_clean.py` | Every operator's contract, all seven corruptions that used to pass the old validator, the repair loop, and that a typed plan never calls an LLM |
  | `test_root_cause.py` | A **matched pair** — the same generator with a planted three-dimensional cause and with it removed. The engine must recover the plant *and* confirm nothing at all in the control, which is the assertion that the noise model and multiple-comparisons correction actually work |
  | `test_monitoring.py`, `test_briefing_report.py` | Money-ranked alerts, identity-based cooldown fingerprints, and that every Arabic template cites through `{{tokens}}` and types no digits of its own |
  | `test_crm_snapshot.py` (19) / `test_crm_repository.py` (16, `@integration`) | Split along the architectural seam on purpose: compute against a DataFrame with no Postgres, persistence against Postgres with no dataset |
  | `test_copilot_streaming.py` | Provider fallback for `stream_complete`, plan normalization, and the full `meta → step → token → done` frame sequence |
  | `test_usage_binding.py` | The only test that drives a real `TestClient`, because the thing under test *is* FastAPI's threadpool behaviour (§4.6) — asserting on the ContextVar directly would prove nothing |
  | `test_constants.py` | Every `DEFAULT_*_MODEL` is selectable, and no decommissioned provider model is still offered |
- **No checked-in `backend/tests/` suite yet**, beyond the placeholder package. Every backend
  router was instead verified live during development via `fastapi.testclient.TestClient`,
  hitting the real endpoints against the real Postgres and real LLM providers end-to-end, with
  disposable test users/projects cleaned up via proper ORM cascade deletes afterward. This is a
  known gap, not an oversight — see `README.md`'s Testing section. It is the highest-value
  testing gap left to close.
- **No frontend test suite at all.** `npx tsc --noEmit`, `npx eslint .` and `npx next build` are
  the only automated frontend gates, and they must be clean across the whole repo rather than
  just changed files. Anything the UI asserts that is a *correctness* claim rather than a
  cosmetic one — the CRM's "null renders as an em-dash, never `EGP 0.00`", or PII appearing
  only inside the Customer 360 dialog — currently holds by review, not by CI.
- **Live browser verification (2026-07-18)** — the full stack was actually launched
  (`docker compose up -d`, `uvicorn`, `next dev`) and driven through real Chromium via
  Playwright: sign-up → project creation → file upload → the entire cleaning pipeline → every
  one of the 9 sections that existed then, including clicking "Generate"/"Run" on every AI
  agent. This caught 3 real bugs invisible from API-level testing alone (an infinite React
  render loop, a UI dead-end in the Reconciliation stage, and a stale mock-era default project
  id) — all fixed; see `README.md`'s "Recent enhancements" for the full writeup. The sections
  added since (root-cause, monitoring, crm, copilot and their supporting surfaces) were each
  verified live the same way as they shipped, but that sweep has not been re-run across all
  15 at once. Frontend static checks (`npx tsc --noEmit`, `npx eslint .`,
  `npx next build`) are run after every change and must be clean across the whole repo, not
  just changed files.

---

## 12. Known dead code / rough edges

- `agents/*/storage.py` (forecasting/marketing/churn) — orphaned since the Streamlit removal
  (§5.3). Safe to delete once confirmed nothing references them, or repurpose to back the new
  `reports` table's persistence if per-run structured (not just markdown) history is wanted.
- No audit/activity log, no 2FA, no billing — the Account/Settings pages say so explicitly
  rather than rendering placeholder data.
- Stateless JWTs mean no per-session revocation (§4.2).
- `backend/app/api/v1/assistant.py::POST /ask` is **orphaned** — a real, working endpoint with a
  matching frontend hook (`useAskDaas`), but no page calls it; Copilot's `general` route reaches
  the same underlying function by its own path. Wire it to a UI or delete it, rather than
  leaving working-but-unreachable code in the tree.
- `llm_usage_events` rows are written and never read: no pricing table, no `/usage` endpoint, no
  page (§4.6).
- `agents/forecasting/tools/calendar_events.py` implements Hijri regressors and is deliberately
  **not wired in** — adding an exogenous input to one candidate and not the others broke the
  fair comparison the `Auto` selector depends on. The module's docstring records the
  measurements and the selection design that would fix it.
- `qwen/qwen3.6-27b` is selectable in Settings but emits `<think>` blocks inline, and
  `tools/llm_client.py` has no way to send `reasoning_effort` — see `README.md`'s limitations.
- `main.py` (root) is an independent CLI entry point for the cleaning pipeline only — it has no
  relationship to `backend/app/main.py` (the FastAPI app) beyond the coincidental filename.

---

## 13. Quick "where do I look for X" index

| I want to... | Look at |
|---|---|
| Add a new agent | `README.md` → "How to add a new agent (worked example)" |
| Change how a report is graded for hallucinated numbers | `agents/reporting/grounding.py` |
| Change the LLM fallback order or add a provider | `tools/llm_client.py`, `tools/llm_provider_models.py` |
| Change what a project-scoped route can see | `backend/app/api/deps.py::get_owned_project` |
| Change the cleaning HITL session lifetime/storage | `backend/app/services/pipeline_sessions.py` |
| Add a field to the per-project semantic views | `db/views.py`, `data_manager/manager.py` |
| Add a new platform table | `db/platform_models.py` or a new `db/*_models.py`, then `alembic revision --autogenerate` **and hand-strip any proposed `DROP TABLE` on unmanaged tables** |
| Add a new frontend section | `frontend/src/lib/queries/<domain>.ts` + `frontend/src/components/sections/<domain>.tsx`, register in `store.ts`'s `Section` union and the nav/command-palette lists |
| Understand the cross-agent grounding (Marketing↔Churn) | `agents/marketing/engine.py::build_churn_section`, `backend/app/api/v1/marketing.py::_churn_payload`, and `frontend/src/lib/store.ts`'s `lastChurnResult`/`lastForecastOutputs` |
| Add or change a cleaning operator | `tools/cleaning_ops.py`'s `REGISTRY` (spec + `may_*` flags + pure function), then `tests/test_cleaning_ops.py`. Widening what cleaning can do means adding operators — **not** trusting the model more |
| Change what a scheduled briefing says | `monitoring/rules.py` (what is worth saying) → `monitoring/briefing.py` (how it is said) → `monitoring/evidence_ar.py` for the Arabic wording of the same key |
| Add a notification channel | `notifications/` — implement `Channel`, register it in `registry.py` (which also publishes the setup-form contract the UI renders from). No frontend change needed |
| Change how a customer's state is computed or stored | `agents/crm/snapshot.py` for compute, `repository.py` for persistence — **never both in one function**; that seam is what makes each testable alone. Design record: `CRM_ARCHITECTURE.md` |
| Add a selectable LLM model | `agents/constants.py` (`AVAILABLE_MODELS` + the relevant `DEFAULT_*_MODEL`), and check `tests/test_constants.py` still passes |
| Add a new language, or fix Arabic copy | `frontend/src/messages/<locale>/` + `messages/index.ts` for UI strings; `monitoring/evidence_ar.py` + `tools/localize.py` for server-composed prose carrying numbers |
| Find out why a figure in a report is trusted | `agents/reporting/grounding.py` (post-hoc check) and `agents/insights/figures.py` (the citation registry, where the model never types a digit at all) |
