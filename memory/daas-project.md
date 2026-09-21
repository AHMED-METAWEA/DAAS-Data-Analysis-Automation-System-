---
name: daas-project
description: What the DAAS project is and the user's goal for it
metadata:
  type: project
---

"DAAS" (Data Analysis Automation System, formerly "Smart Analyst") is the
user's graduation project: a Groq-powered, multi-agent BI platform for
SMB/e-commerce data, built on LangGraph + Streamlit. Five agents (Data
Cleaning, Visualization, Business Insights, Forecasting, Marketing) plus a
deterministic analytics engine. Human-in-the-loop on each page. Optional
PostgreSQL persistence.

Goal stated 2026-06-05: make it "a professional project with real value."
Priority lens chosen: **most analytical value** (deepest data-science features).

Key design strength to preserve: deterministic math (KPIs, RFM, forecasts) is
computed in pure pandas and fed to the LLM as grounded numbers, so the model
narrates rather than invents figures. Keep that pattern in new features.

Stack note: LLM provider is **Groq only** today (per-agent model picker in the
sidebar). Env is **pandas 3.0 / numpy 2.x / python 3.14**; `prophet` installed,
`statsmodels` is NOT installed by default (Holt-Winters degrades gracefully).
See [[professionalization-roadmap]].
