# DAAS — Architecture Diagrams

Mermaid diagrams for the **core platform and agents**, plus one for the web
application surface that sits on top of them (§16). Preview in VS Code with the
built-in Markdown preview, or on GitHub — both render Mermaid natively.

Contents:

1. [Whole platform (end-to-end)](#1--whole-platform-end-to-end)
2. [Cleaning Agent](#2--cleaning-agent)
3. [Schema Discovery Agent](#3--schema-discovery-agent)
4. [Analytics Engine](#4--analytics-engine)
5. [Insights Agent](#5--insights-agent)
6. [Visualization Agent](#6--visualization-agent)
7. [Forecasting Agent](#7--forecasting-agent)
8. [Churn Agent](#8--churn-agent)
9. [Marketing Agent](#9--marketing-agent)
10. [Analyst Copilot Agent](#10--analyst-copilot-agent)
11. [Grounding / Verification layer](#11--grounding--verification-layer)
12. [Root Cause Agent](#12--root-cause-agent)
13. [Autonomous Monitoring (Pillar III)](#13--autonomous-monitoring-pillar-iii)
14. [CRM — customer state (Pillar II Stage 0)](#14--crm--customer-state-pillar-ii-stage-0)
15. [LLM token metering](#15--llm-token-metering)
16. [Web application surface](#16--web-application-surface)

---

## 1 · Whole platform (end-to-end)

Data flows top→bottom: raw sources are ingested as separate tables, their
relationships discovered, each table cleaned + reconciled, stored in Postgres,
then served through **four cached semantic views** — each agent reads the view
shaped for it. The **Analyst Copilot is a supervisor**: it routes a question to
1–3 of the other agents and synthesizes their results, rather than reading views
on its own. Insights and Marketing pass their LLM narratives through the formal
grounding check (`verify_report`) before they reach the user; the Copilot's
general-chat answers are grounded the same way (Churn and Forecasting narratives
are grounded by prompt but not formally re-checked today).

```mermaid
flowchart TB
    subgraph SRC["📥 Data Sources"]
        F["Files<br/>CSV / Excel / JSON"]
        GS["Google Sheets"]
        DBX["External Database<br/>(link)"]
    end

    subgraph ING["1 · Ingestion"]
        LOAD["load_tabular_file<br/>tables_from_datasets"]
        TABLES{{"dict[table_name → DataFrame]"}}
    end

    subgraph SD["2 · Schema Discovery"]
        PROF["profile_tables"]
        HEUR["heuristic FK/PK<br/>candidates"]
        ESC["LLM escalation<br/>(low-confidence only)"]
    end

    HITL1{{"👤 approve<br/>relationships"}}

    subgraph CLEAN["3 · Multi-table Cleaning (per table)"]
        direction TB
        PG["Planner Graph<br/>profiler → planner"]
        HITL2{{"👤 approve / edit plan"}}
        CG["Cleaning Graph<br/>coder → executor → validator"]
        REC["Reconciliation<br/>normalize keys + orphans"]
        PG --> HITL2 --> CG --> REC
    end

    subgraph STORE["4 · Storage & Cached Views"]
        PGSQL[("PostgreSQL")]
        DM["Data Manager<br/>(TTL cache)"]
        VA["analytics view"]
        VF["forecast view"]
        VM["marketing view"]
        VC["customer_360 view"]
        PGSQL --> DM
        DM --> VA & VF & VM & VC
    end

    subgraph AGENTS["5 · Analytical & AI Agents"]
        AN["Analytics Engine<br/>(deterministic)"]
        INS["Insights Agent"]
        VIZ["Visualization Agent"]
        FC["Forecasting Agent"]
        CH["Churn Agent"]
        MK["Marketing Agent"]
        RC["Root Cause Agent<br/>(slice search + stats)"]
        CRM["CRM · customer state<br/>(the only STATEFUL layer)"]
    end

    CO["🧭 Analyst Copilot<br/>supervisor — routes 1–3 agents<br/>per question, then synthesizes"]

    MON["⏰ Autonomous Monitoring<br/>scheduler → runner → briefing<br/>→ email / Telegram / WhatsApp"]

    GROUND["🔒 Grounding layer<br/>figure registry + strict verify"]
    OUT["📊 Reports · Charts · Exports"]

    LLM["🤖 LLM Provider client<br/>Groq → Anthropic → OpenAI → OpenRouter"]
    METER["📈 Token metering<br/>llm_usage_events"]
    SBX["🧪 Safe Sandbox<br/>restricted code exec"]

    %% top pipeline
    F --> LOAD
    GS --> LOAD
    DBX --> LOAD
    LOAD --> TABLES --> PROF --> HEUR --> ESC --> HITL1 --> PG
    REC --> PGSQL

    %% each view feeds only the agents that read it
    VA --> AN
    VA --> INS
    VA --> VIZ
    VA --> RC
    VF --> FC
    VM --> MK
    VC --> CH
    VC --> CRM

    %% real inter-agent data flows
    AN -. "analytics payload" .-> INS
    CH -. "churn scores" .-> MK
    FC -. "forecast highlights" .-> MK
    CH -. "risk folded into state<br/>on every churn run" .-> CRM

    %% copilot orchestrates the same agents
    CO -. "invokes" .-> AGENTS

    %% monitoring re-runs the same engines on a schedule, unattended
    MON -. "runs on a cron" .-> AN
    MON -. "drills into the top finding" .-> RC

    %% Insights, Marketing and the monitoring narrative are grounded
    INS --> GROUND
    MK --> GROUND
    RC --> GROUND
    GROUND --> OUT
    AN --> OUT
    VIZ --> OUT
    FC --> OUT
    CH --> OUT
    CRM --> OUT
    CO --> OUT
    MON --> OUT

    %% cross-cutting dependencies
    LLM -.-> SD
    LLM -.-> CLEAN
    LLM -.-> AGENTS
    LLM -.-> CO
    LLM -. "every attempt<br/>records its tokens" .-> METER
    SBX -.-> CLEAN
    SBX -.-> VIZ
    SBX -.-> CO
```

> **Two things this diagram deliberately shows.** The CRM is the only box that
> keeps state between runs — every other agent computes a number and forgets it.
> And Monitoring does not contain analysis of its own: it re-runs the *same*
> engines on a schedule, which is why a scheduled finding and a finding you'd
> get by opening the app can never disagree.

---

## 2 · Cleaning Agent

Two LangGraph state machines (`graphs/planner_graph.py`, `graphs/cleaning_graph.py`)
wrapped by a per-table orchestrator (`agents/cleaning/multi_table.py`).

**Rebuilt 2026-08-08 around deterministic operators.** The LLM *plans*; it no
longer *executes*. A typed plan runs as operators from
`tools/cleaning_ops.py`'s registry (21 of them), and each step's effect is
**measured by diffing the frame** rather than reported by the code that made the
change. The coder is now a no-op for a fully-typed plan — the normal case — and
is invoked only for free-text steps a user typed by hand, which run *after* the
operators, in the sandbox, on the already-cleaned frame.

Everything then passes `agents/cleaning/invariants.py`, the authorization gate:
a step may only touch what the plan named. Retries exist only because generated
code can be wrong in fixable ways — a *typed* plan that fails invariants means an
operator bug, so it goes straight to END rather than re-running something that
would be byte-for-byte identical.

```mermaid
flowchart TB
    RAW["raw DataFrame<br/>(looped once per table)"] --> PROFILER

    subgraph PLAN["Planner Graph"]
        PROFILER["Profiler<br/>build DatasetProfile"]
        DEFECTS["detect_defects<br/>19 deterministic checks"]
        CLEANCHK{"is_profile_clean?"}
        PLANNER["Planner (LLM)<br/>typed plan + rationale"]
        EMPTY["Empty plan<br/>(skip LLM entirely)"]
        PROFILER --> DEFECTS --> CLEANCHK
        CLEANCHK -- "yes" --> EMPTY
        CLEANCHK -- "no" --> PLANNER
    end

    HITL{{"👤 Human review<br/>approve · edit · add · drop steps"}}
    PLANNER --> HITL
    EMPTY --> HITL

    subgraph CLEANG["Cleaning Graph"]
        FREE{"plan contains<br/>free-text steps?"}
        CODER["Coder (LLM)<br/>ONLY for free-text steps"]
        NOOP["Coder no-op<br/>(typed plan — no LLM call)"]
        EXEC["Executor<br/>1· apply_plan → typed operators<br/>2· sandboxed code for free text<br/>ledger MEASURED by diffing"]
        INV["Invariant gate<br/>did anything change that<br/>the plan did not authorize?"]
        ROUTE{"passed?"}
        VALID["Validator<br/>structured violations"]
        FREE -- "yes" --> CODER --> EXEC
        FREE -- "no" --> NOOP --> EXEC
        EXEC --> INV --> ROUTE
        ROUTE -- "fail · free-text · retries &lt; 2" --> CODER
        ROUTE -- "fail · typed plan" --> VALID
        ROUTE -- "pass" --> VALID
    end

    HITL --> FREE
    VALID --> RECON["Reconciliation<br/>normalize join keys + orphan check"]
    RECON --> CLEANOUT["Cleaned tables → Storage (Stage 7)"]
```

---

## 3 · Schema Discovery Agent

Heuristics first, LLM only for the ambiguous cases. Fails **open** — an LLM
error keeps the heuristic verdict. Only the two candidate columns are ever sent
to the LLM, never the full schema.

```mermaid
flowchart LR
    T["dict[table → DataFrame]"] --> P["profile_tables<br/>row / column profiles"]
    T --> H["find_relationship_candidates"]

    subgraph H2["Heuristic signals (weighted score)"]
        NAME["fuzzy name similarity<br/>difflib (0.25)"]
        SUFFIX["key suffix<br/>_id / _fk / _ref / id"]
        DT["dtype compatibility (0.15)"]
        UNIQ["PK uniqueness (0.30)"]
        COV["FK coverage /<br/>value overlap (0.30)"]
    end

    H --> H2 --> C["candidates + confidence<br/>(best pair per table-pair)"]
    C --> THRESH{"confidence ≥<br/>threshold?"}
    THRESH -- "yes" --> KEEP["accept (source = heuristic)"]
    THRESH -- "no" --> LLM["LLM escalation<br/>only the 2 ambiguous columns sent"]
    LLM -- "ok" --> ADJ["re-scored (source = llm)"]
    LLM -. "on error" .-> FAILOPEN["keep heuristic verdict"]
    KEEP --> RESULT["SchemaDiscoveryResult<br/>tables + candidates"]
    ADJ --> RESULT
    FAILOPEN --> RESULT
```

---

## 4 · Analytics Engine

Pure deterministic (no LLM). Produces the analytics payload the **Insights agent**
and the **KPI dashboard** build on (Visualization reads the raw view instead, and
the Copilot reaches this only via its Insights tool).

```mermaid
flowchart LR
    V["Semantic view / DataFrame"] --> S["Schema Intelligence<br/>column-role detection"]
    S --> K["KPI Engine<br/>revenue · AOV · customers · retention"]
    S --> TS["Time-series Intelligence<br/>trends · seasonality"]
    S --> Q["Data Quality<br/>nulls · duplicates · outliers"]
    K --> PAY["Analytics Payload<br/>{schema, kpi, timeseries, quality, metadata}"]
    TS --> PAY
    Q --> PAY
    PAY --> DOWN["→ Insights agent + KPI dashboard"]
```

---

## 5 · Insights Agent

**Rebuilt 2026-08-04 so the model never types a digit.** Checking a model's
numbers afterwards can only ever *catch* fabrication, and it catches it
probabilistically — a wrong figure that lands near some value in a big nested
payload coincidentally "verifies". So the flow is inverted: every number the
report is allowed to contain is computed *first* into a **FigureRegistry**, each
with an exact value, the display string it must be printed as, and the formula
it came from. The model is then told to write **citation tokens**
(`{{revenue_total}}`) and `render()` substitutes the registered display strings
server-side. A cited number is not checked — it *cannot* be wrong.

`strict_verify` closes the other half: numbers the model typed as digits anyway
are checked against that closed ~100-figure allow-list, not against every
numeric leaf in the payload, so a coincidental match is far harder. Anything
unverified triggers one self-correction pass naming the exact sentence.

```mermaid
flowchart TB
    DF["view + analytics payload"] --> FIND["build_findings<br/>deterministic ranked evidence"]
    DF --> REG["FigureRegistry<br/>~100 curated figures:<br/>value · display · formula · key"]
    DF --> SEL["select_template<br/>non-technical / executive / detailed"]

    FIND --> PROMPT["assemble prompt<br/>+ ALLOWED FIGURES table<br/>“cite {{tokens}}, never type digits”"]
    REG --> PROMPT
    SEL --> PROMPT

    PROMPT --> LLM["LLM draft (insights model)"]
    LLM --> STRIP["strip &lt;think&gt; reasoning tags"]
    STRIP --> RENDER["render()<br/>substitute {{tokens}} → display strings<br/>(server-side, post-generation)"]
    RENDER --> SV["strict_verify<br/>unknown tokens? · typed digits<br/>not in the registry? · bare currency?"]
    SV --> CHK{"clean?"}
    CHK -- "yes" --> REPORT["Insights report (markdown)<br/>+ certificate + audit trail<br/>(every figure's formula)"]
    CHK -- "no" --> CORR["_correct (LLM rewrite)<br/>given the offending sentence"]
    CORR --> RENDER2["re-render + re-verify"]
    RENDER2 --> REPORT
```

---

## 6 · Visualization Agent

LLM writes Plotly code; a sandboxed executor runs it under a 30-second timeout
and retries the coder on failure. Output figures are sanitized and themed.

```mermaid
flowchart TB
    Q["user_query + df / table"] --> RS["retrieve_schema<br/>df + dtype summary"]
    RS --> CODER["Coder (LLM)<br/>write Plotly code (no imports)"]
    CODER --> EXEC["Executor<br/>sandboxed exec, 30s timeout"]
    EXEC --> ROUTE{"error &amp;<br/>retries &lt; 2?"}
    ROUTE -- "yes" --> CODER
    ROUTE -- "no" --> SAN["sanitize_fig + style_figure"]
    SAN --> FIGS["Plotly figure(s)<br/>fig, fig1…figN"]
```

---

## 7 · Forecasting Agent

LangGraph: `validate → prepare → forecast → interpret`. Each metric is forecast
in parallel; the engine back-tests several candidate models and auto-selects the
best by out-of-sample error.

**Rebuilt 2026-08-03.** Measurement showed daily point-forecasts sitting at the
series' own noise floor — no model choice moves that — so the engine's bet
shifted to the two things that *are* improvable: **horizon totals** (what the
next 30 days add up to, which is the number a business actually plans against)
and **calibrated intervals** via conformal prediction (`tools/conformal.py`),
where an 80% band is measured to contain the truth ~80% of the time rather than
merely being labelled 80%. Selection uses **MASE** with a 1-standard-error rule,
preferring the simpler model when candidates are statistically tied.

```mermaid
flowchart TB
    DF["view / DataFrame"] --> VAL["validate<br/>detect date col · frequency ·<br/>metrics · forecastable?"]
    VAL --> PREP["prepare"]
    PREP --> FCAST["forecast<br/>(ThreadPool, one per metric)"]

    subgraph ENGINE["ForecastEngine (per metric)"]
        RESAMP["outlier-clean + resample<br/>to granularity"]
        MODELS["back-test candidates<br/>Naive · Seasonal Naive · Drift · MA ·<br/>Linear · Holt-Winters · Theta · ETS ·<br/>ARIMA · SARIMA · Prophet (+ Ensemble)"]
        CV["cross-validate<br/>MASE · MAPE · skill vs naive<br/>(adaptive folds)"]
        PICK["auto-select winner<br/>+ refit on full history"]
        CHART["Plotly chart<br/>+ 80% confidence band"]
        RESAMP --> MODELS --> CV --> PICK --> CHART
    end

    FCAST --> ENGINE
    ENGINE --> DERIVE["derive (deterministic)<br/>change % · trend ·<br/>business impact · confidence"]
    DERIVE --> INT["interpret (LLM)<br/>exec summary · risks ·<br/>opportunities · actions"]
    INT --> OUT["forecast outputs<br/>+ charts + evaluations"]
    OUT -. "if store=true" .-> DBS[("save_forecast_run")]
```

---

## 8 · Churn Agent

Deterministic engine trains a churn model (heuristic fallback), scores every
customer, and computes **per-customer SHAP drivers** only for the ones shown.
The LLM turns the payload into a retention strategy — grounded by a payload-only
prompt (underscore-prefixed internals are stripped), though not run through the
formal `verify_report` check that Insights/Marketing use. Scores also feed the
Marketing Agent.

```mermaid
flowchart TB
    DF["customer_360 view"] --> SCH["schema summary<br/>customer / time / money columns"]
    SCH --> GATE{"enough history<br/>& columns?"}
    GATE -- "no" --> NA["available = false + reason"]
    GATE -- "yes" --> FEAT["build RFM-style features<br/>+ label churn window"]
    FEAT --> TRAIN["train_and_predict (ML)"]
    TRAIN -. "fallback" .-> HEUR["heuristic_predict"]
    TRAIN --> SCORE["score customers<br/>churn probability"]
    HEUR --> SCORE
    SCORE --> SHAP["SHAP explain<br/>per-customer drivers (shown only)"]
    SHAP --> PAY["Churn payload<br/>risk tiers · revenue at risk ·<br/>expected loss · at-risk list"]
    PAY --> LLM["LLM retention plan<br/>(payload-only prompt;<br/>no formal verify_report today)"]
    LLM --> OUT["Retention strategy (markdown)"]
    PAY -. "_customer_scores" .-> MK["→ Marketing Agent"]
```

---

## 9 · Marketing Agent

Deterministic RFM segmentation + marketing KPIs + channel breakdown, optionally
folding in the Churn agent's model scores (and Forecast highlights). The LLM
produces three artifacts — a strategy report, a campaign plan, and ad copy — of
which the **strategy report** is the one run through `verify_report`.

```mermaid
flowchart TB
    DF["marketing view"] --> SCH["schema summary"]
    CHIN["Churn payload (optional)"]:::opt
    SCH --> KPI["core KPIs"]
    SCH --> RFM["RFM segmentation"]
    KPI --> MKPI["marketing KPIs<br/>repeat rate · CLV proxy · churn-risk base"]
    RFM --> MKPI
    SCH --> CHAN["channel / dimension performance<br/>region · category · payment…"]
    CHIN --> CHSEC["churn section<br/>risk per RFM segment + target lists"]
    RFM --> CHSEC
    MKPI --> PAY["Marketing payload"]
    CHAN --> PAY
    CHSEC --> PAY
    FCIN["Forecast highlights (optional)"]:::opt
    PAY --> S1["LLM strategy report (markdown)"]
    FCIN --> S1
    PAY --> S2["LLM campaign plan (JSON)"]
    PAY --> S3["LLM ad copy (JSON)"]
    S1 --> GRD["verify_report<br/>(strategy report only)"]
    GRD --> OUT["Strategy + campaigns + copy"]
    S2 --> OUT
    S3 --> OUT
    classDef opt stroke-dasharray: 4 3;
```

---

## 10 · Analyst Copilot Agent

One structured planner call decomposes a turn into `clarify`, `follow_up`, or an
ordered list of **1–3 tool steps**. Single-step turns are narrated; multi-step
turns are synthesized over the union of every tool's payload. Everything streams
to the user token-by-token over SSE.

```mermaid
flowchart TB
    Q["user question + history"] --> PLAN["Planner / Router (LLM)<br/>1 structured-output call"]
    PLAN --> MODE{"mode?"}
    MODE -- "clarify" --> CLAR["clarify question"] --> STREAM
    MODE -- "follow_up" --> EXP["explain<br/>reuse cached payload"] --> STREAM
    MODE -- "tools" --> STEPS["ordered 1–3 tool steps"]

    subgraph TOOLS["Tool nodes (each self-contained, fail-soft)"]
        VIZ["visualization"]
        INS["insights"]
        FC["forecasting"]
        MK["marketing"]
        CH["churn"]
        GEN["general grounded chat"]
    end

    STEPS --> TOOLS
    TOOLS --> N{"how many<br/>steps?"}
    N -- "1" --> NAR["narrate<br/>(single payload)"]
    N -- "2–3" --> SYN["synthesize<br/>(union of payloads)"]
    NAR --> STREAM
    SYN --> STREAM
    STREAM["SSE token stream → user"]
```

---

## 11 · Grounding / Verification layer

**Two mechanisms, not one**, and the difference matters. Prevention makes a
fabricated number structurally impossible; detection only makes it probable that
you notice. The platform now leads with prevention and keeps detection as the
backstop for anything that slips past it.

| | Mechanism | Where | Guarantee |
|---|---|---|---|
| **Prevention** | Figure registry + citation tokens; engine substitutes every value | Insights, Root Cause | A cited number *cannot* be wrong |
| **Detection (strict)** | Typed digits checked against a closed ~100-figure allow-list | Insights, Root Cause | Coincidental "verification" is hard |
| **Detection (general)** | `verify_report`: figures matched against payload leaves, label-bound where possible | Marketing, Copilot, Churn | Catches most fabrication; probabilistic |

```mermaid
flowchart TB
    subgraph PREV["Prevention — the model never types a digit"]
        REG["FigureRegistry<br/>value · display · formula · key"]
        TOK["prompt: cite {{tokens}}"]
        SUB["render(): substitute server-side"]
        REG --> TOK --> SUB
    end

    subgraph STRICT["Strict detection — for digits typed anyway"]
        UNK["unknown {{tokens}}?"]
        TYPED["typed numbers ∉ registry?"]
        CUR["bare currency symbol<br/>when no currency column exists?"]
        SCRIPT["alien script<br/>(an English clause inside Arabic)?"]
    end

    subgraph GEN["General detection — the older, wider net"]
        EXT["extract material figures<br/>currency / % / counts"]
        LV["semantic (label-bound) check"]
        MAG["magnitude check vs payload leaves"]
        EXT --> LV & MAG
    end

    SUB --> STRICT
    STRICT --> VERD{"all clean?"}
    VERD -- "yes" --> CERT["certificate<br/>coverage · status · traces · formulas"]
    VERD -- "no" --> FIX["one correction pass<br/>(given the exact sentence)"]
    FIX --> SUB
    FIX -. "still failing" .-> DET["deterministic summary<br/>says less, provably right"]
    DET --> CERT

    GEN --> CERT
    CERT --> BADGE["UI badge · export footer · briefing method section<br/>“N of M figures traced to your data”"]
```

> The fallback edge is the one people miss: when a draft cannot be verified it is
> **discarded**, not patched — replaced by a deterministic summary that says less
> and is provably right. Downstream that fact is *reported*, not hidden: the
> scheduled briefing's method section states when the model's draft was rejected.

---

## 12 · Root Cause Agent

Answers *"why did revenue fall?"* by finding the **smallest slice of the business
that explains the largest part of a change**. The hard part is not the search but
knowing when to believe it: testing thousands of slices guarantees some will look
significant by chance, so the threshold is corrected for how many were tested, and
a finding that fails that bar is reported as a **lead, not a cause**.

The search is a beam search over dimension combinations with a node budget —
exhaustive enumeration is combinatorial and pointless, since most slices are too
small to matter. Pruning happens on support (too few rows), a magnitude bound (this
slice cannot possibly explain enough), the beam, and redundancy (the same slice
reached by a different predicate order).

```mermaid
flowchart TB
    DF["view + measure (revenue / orders / customers)"] --> WIN["window_mode: auto<br/>pick current vs prior period"]
    WIN --> DIMS["detect_dimensions<br/>which columns are sliceable?"]

    subgraph SEARCH["Beam search over slice lattice"]
        EXPAND["expand candidate slices<br/>(depth &le; max_depth)"]
        PRUNE["prune: support · magnitude bound<br/>· beam width · redundant paths"]
        SCORE["score: explanatory power<br/>= slice change ÷ total change"]
        EXPAND --> PRUNE --> SCORE --> EXPAND
    end

    DIMS --> SEARCH
    SEARCH --> STATS["significance test<br/>threshold CORRECTED for<br/>slices_tested (multiplicity)"]
    STATS --> ROBUST{"survives<br/>correction?"}
    ROBUST -- "yes" --> CAUSE["robust = true<br/>a cause"]
    ROBUST -- "no" --> LEAD["robust = false<br/>treat as a lead"]

    CAUSE --> NARR
    LEAD --> NARR
    NARR["narrate()<br/>registry tokens then verified prose"]
    NARR --> VERIFY{"verified?"}
    VERIFY -- "yes" --> OUT["RootCauseResult<br/>+ figures + certificate"]
    VERIFY -- "no" --> DETSUM["deterministic_summary<br/>assembled from tokens, no model"]
    DETSUM --> OUT
```

> A null result is a real answer here. When no slice explains the change, the
> agent says so rather than promoting the best of a bad set — and there is a
> regression test pinning exactly that.

---

## 13 · Autonomous Monitoring (Pillar III)

The platform stops waiting to be opened. A `Schedule` says *when* to look and
*what matters*; a run evaluates the same engines the UI uses, ranks findings by
money at stake, drills into the largest one, and pushes a briefing.

Three design decisions carry most of the weight:

* **No model in the delivery path.** The briefing is templates over
  already-computed figures, so it sends at 07:00 whether or not an LLM provider
  is reachable or in credit. A verified narrative is *included* when one exists;
  without it the briefing is shorter, never absent.
* **The `Schedule` table is the source of truth**, not APScheduler's job store —
  a second record of the same fact can disagree, and the classic symptom is a
  deleted schedule that keeps firing. Reconciled every 60s.
* **A Postgres advisory lock per run.** Two workers, or a manual run racing the
  cron, must not double-deliver. The lock releases automatically if the process
  dies holding it.

```mermaid
flowchart TB
    SCHED[("Schedule table<br/>SOURCE OF TRUTH")] -->|"reconcile every 60s"| APS["APScheduler<br/>(in the API process by default)"]
    APS --> LOCK{"Postgres advisory lock<br/>acquired?"}
    LOCK -- "no (duplicate)" --> SKIP["skip, never double-deliver"]
    LOCK -- "yes" --> RUN

    subgraph RUN["One monitored run"]
        ANALYSE["re-run the same engines<br/>the UI uses"]
        SNAP["history.capture<br/>MetricSnapshot: RECORDED,<br/>never recomputed"]
        RULES["rules.evaluate<br/>snapshot deltas · owner targets<br/>· freshness · data quality"]
        FILTER["filter_findings<br/>severity · cooldown · max per run"]
        RC["root-cause drill-down<br/>into the largest finding"]
        COMPOSE["briefing.compose (EN/AR)"]
        ANALYSE --> SNAP --> RULES --> FILTER --> RC --> COMPOSE
    end

    COMPOSE --> FULL["FULL REPORT<br/>basis · figures · cause<br/>· detail · method"]
    COMPOSE --> SUMM["EXEC SUMMARY<br/>(4096-char channels)"]

    FULL --> EMAIL["email (HTML)"]
    FULL --> INAPP["in-app inbox"]
    SUMM --> TG["Telegram"]
    SUMM --> WA["WhatsApp<br/>(Meta Cloud API / Twilio)"]

    EMAIL & INAPP & TG & WA --> LOG[("DeliveryLog<br/>per-channel outcome + retryable?")]
```

**Why two renderings.** WhatsApp and Telegram cut at 4096 characters, and the
evidence sections sit lowest in the report — so truncation would remove the
figures and the method first, leaving conclusions with nothing backing them. A
test pins that the summary can never state a figure the full report does not.

**Comparing today to yesterday requires yesterday to have been recorded.**
`MetricSnapshot` exists because recomputing the prior period from the current
dataset silently answers a different question every time the data is re-cleaned
or backfilled.

---

## 14 · CRM — customer state (Pillar II Stage 0)

The platform's **only stateful layer**. Every other engine computes a number and
forgets it, which is fine for a report and fatal for a CRM: *"was this customer
riskier last month?"* cannot be answered by recomputing, because recomputing
answers a different question that happens to produce a similar-looking number.

The package is split along a strict seam — `snapshot.py` never touches the
database and `repository.py` never computes anything — which is what lets the
compute be tested against a CSV with no Postgres, and the persistence against
Postgres with no dataset.

```mermaid
flowchart TB
    TRIG1["churn run<br/>(record_churn_run)"] --> SVC
    TRIG2["manual POST /crm/refresh"] --> SVC
    SVC["service.py, orchestration only"]
    SVC --> VIEW["customer_360 view"]

    subgraph COMPUTE["snapshot.py — PURE (no DB)"]
        direction TB
        OBS["OBSERVED<br/>recency · frequency · monetary<br/>· tenure · AOV"]
        DER["DERIVED<br/>RFM segment + scores<br/>· lifecycle stage"]
        PRED["PREDICTED<br/>churn probability · risk tier<br/>· CLV (not built yet)"]
        VAR["value_at_risk = P(churn) × value<br/>records WHICH basis it used"]
        OBS --> DER --> PRED --> VAR
    end

    VIEW --> COMPUTE
    CADENCE["stage thresholds derived from<br/>the population's OWN median<br/>inter-purchase gap (×1.5, ×3)"] -.-> DER

    COMPUTE --> REPO["repository.py, PERSISTENCE (no compute)"]
    REPO --> DB[("CrmSnapshot + CustomerState<br/>one row per customer per date")]
    DB --> API["/crm/refresh · /portfolio · /customers<br/>· /customers/{id} · /snapshots<br/>· /ranking-comparison"]

    API --> UI["frontend section 'crm'<br/>(crm.tsx + queries/crm.ts)"]
    UI --> T1["Portfolio<br/>risk · stage · segment +<br/>observed/derived/predicted<br/>coverage reported SEPARATELY"]
    UI --> T2["Customers<br/>filter · whitelisted sort · page<br/>→ Customer 360 dialog"]
    UI --> T3["Prioritisation<br/>risk-only vs value-at-risk,<br/>side by side"]
    UI --> T4["Snapshots<br/>refresh history + which<br/>component produced values"]

    PII["strip_pii()<br/>prompts see IDs, humans see names"] -.-> API
    PII -. "names render ONLY inside<br/>the Customer 360 dialog" .-> T2
    NULLR["null renders as an em-dash,<br/>never as EGP 0.00"] -.-> UI
```

**The provenance rule.** Observed, then derived, then predicted — computed in that
order, and **a later layer never overwrites an earlier one**. So when the churn
model cannot fit, you still get the whole customer book with segments and
lifecycle stages instead of losing everything.

**Self-calibrating lifecycle.** Hardcoding "dormant after 90 days" is wrong in
both directions at once — far too patient for a coffee shop, far too aggressive
for annual insurance. Thresholds are multiples of the population's own median
inter-purchase interval, and the rule is published with the snapshot so it can be
checked.

---

## 15 · LLM token metering

Every provider SDK returns exact token counts and all eight handlers were
discarding them. The awkward constraint: `complete()` returns a bare `str` and
has **23 call sites**, so changing its return type would touch every agent.
Instead the client **pushes** events to a sink installed in the surrounding
context — metering is invisible to callers and no agent knows it exists.

```mermaid
flowchart TB
    REQ["HTTP request"] --> DEP["bind_usage_context<br/>async ON PURPOSE"]
    DEP --> CTXV["ContextVar: UsageContext<br/>(user_id, project_id, source)"]
    DEP -.-> NOTE1["a SYNC dependency would set this<br/>on a throwaway threadpool copy<br/>the endpoint never sees"]

    CTXV --> EP["sync endpoint<br/>runs in a threadpool;<br/>anyio COPIES the context in"]
    OWNED["get_owned_project (sync)"] -. "can only MUTATE<br/>the bound object" .-> CTXV

    EP --> AGENT["agent, 5 frames deep"] --> CLIENT["tools/llm_client.py"]

    subgraph CHAIN["provider fallback chain"]
        G["Groq"] -->|"fails"| A["Anthropic"] -->|"fails"| O["OpenAI"] --> OR["OpenRouter"]
    end
    CLIENT --> CHAIN

    CHAIN --> EV["UsageEvent per ATTEMPT<br/>tokens · latency · status<br/>· fallback_depth"]
    EV --> SINK["llm_usage.record()<br/>never raises: metering must not<br/>break the answer it measures"]
    SINK --> ROW[("llm_usage_events<br/>NO cost column")]

    SCHED2["monitoring worker<br/>no request, no ambient sink"] -. "must bind explicitly<br/>or tokens hit nobody's bill" .-> CTXV

    ROW -. "not built yet" .-> TODO["pricing · /usage endpoint · dashboard"]
```

**The streaming trap.** Usage arrives on a final chunk whose `choices` list is
**empty** — exactly what the stream loops were skipping with
`if not chunk.choices: continue` — and OpenAI-compatible providers only send that
chunk when asked via `stream_options={"include_usage": True}`. Copilot is entirely
streamed, so without both fixes the meter would have under-reported the single
heaviest surface in the product while looking perfectly healthy.

**One row per *attempt*, not per call.** A chain that tries Groq, gets
rate-limited, and succeeds on Anthropic writes two rows. Collapsing that into one
"successful" row would hide a chain quietly running on its second choice — which
is a bill nobody predicted.

**No cost column, deliberately.** Token counts are ground truth and never change;
a stored cost silently rots as provider prices drift. Cost belongs at read time
against a dated price list, clearly labelled an estimate.

---

## 16 · Web application surface

Every diagram above stops at the API. This one is what sits on top: **15 authenticated
sections**, each a route + a section component + a `queries/` file, all reaching the
backend through **one** typed fetch wrapper. The shape is deliberately boring and
repetitive — adding an agent means adding one of each, and nothing else moves.

Three things are worth reading off it. Server state lives **only** in react-query;
Zustand holds UI-only state (active section, active project, cross-page artifacts),
so there is no second copy of a server value that can go stale. The **project id is
the axis of the whole app** — nearly every hook is keyed by it, which is why
switching projects invalidates rather than merges. And **auth is bridged, not
duplicated**: NextAuth holds the session, but the token it carries is the FastAPI
JWT, so the browser never has an identity the backend didn't issue.

```mermaid
flowchart TB
    subgraph PUB["public"]
        LAND["/ — landing"]
        LOGIN["/login"]
    end

    MW["middleware.ts<br/>redirects unauthenticated<br/>requests away from (app)"]

    subgraph PROV["providers.tsx — wraps everything"]
        RQ["QueryClientProvider"]
        TH["ThemeProvider (next-themes)<br/>OKLCH tokens + 12-step type scale"]
        LOC["LocaleProvider (next-intl)<br/>sets html lang + dir=rtl"]
    end

    subgraph SHELL["app-shell"]
        NAV["sidebar + project switcher"]
        CMD["command palette"]
        TOG["theme + language toggles"]
    end

    subgraph SECTIONS["(app)/ — 15 sections, one folder each"]
        direction TB
        S1["command-center · projects"]
        S2["data (+ relationship-erd, React Flow)"]
        S3["visualization · insights (+ insights-audit)"]
        S4["forecasting · marketing · churn"]
        S5["root-cause · monitoring · crm"]
        S6["copilot (SSE) · reports"]
        S7["account · settings (providers-manager)"]
    end

    Q["lib/queries/* — one file per domain<br/>react-query hooks, keyed by project id"]
    CL["lib/api/client.ts (apiFetch)<br/>+ use-api.ts attaches the Bearer token"]
    ST["lib/store.ts (Zustand)<br/>UI-ONLY state — never server data"]
    MSG["messages/{en,ar}/*.json<br/>20 namespaces per locale"]

    API["FastAPI /api/v1/*"]
    NA["NextAuth Credentials provider<br/>→ POST /auth/login"]

    LAND --> LOGIN --> NA
    NA -. "session carries the<br/>FastAPI JWT" .-> CL
    MW --> SECTIONS
    PROV --> SHELL --> SECTIONS
    LOC --> MSG
    MSG -.-> SECTIONS
    SECTIONS --> Q --> CL --> API
    SECTIONS -.-> ST
    ST -.-> SECTIONS
    CMD -. "jumps to any section" .-> SECTIONS

    API -. "SSE token stream<br/>(copilot only)" .-> S6
```

**One deviation from the pattern, and it earns it.** Every hook above is
request/response through `apiFetch`; Copilot instead consumes a
`text/event-stream` (`meta → step → token → done`), because a multi-agent answer
that takes 20 seconds to assemble and arrives all at once reads as a hang. It is
the only surface allowed to bypass the shared client.

**Why sections own their translations.** `messages/` is split into 20 namespace
pairs rather than two big files, so two translation passes never touch the same
file and a missing key blanks one screen instead of the app. Arabic prose that
carries numbers is composed rather than translated — that logic lives on the
server (`monitoring/evidence_ar.py`, `tools/localize.py`), not here.
