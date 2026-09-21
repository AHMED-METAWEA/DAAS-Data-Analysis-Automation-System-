'use client'

import { useMutation, useQuery } from '@tanstack/react-query'
import { useApi } from '@/lib/api/use-api'

export type ForecastMetrics = {
  date_column: string | null
  metrics: string[]
}

export function useForecastMetrics(projectId: string) {
  const { call, ready } = useApi()
  return useQuery({
    queryKey: ['forecast-metrics', projectId],
    queryFn: () => call<ForecastMetrics>(`/api/v1/projects/${projectId}/forecast/metrics`),
    enabled: ready && Boolean(projectId),
  })
}

export type ForecastOutput = {
  metric: string
  current_value: number
  forecasted_value: number
  change_percent: number
  confidence_score: number | null
  forecast_horizon: string
  granularity: string
  training_periods: number
  selected_model: string
  model_selection_reason: string
  evaluation: { mae?: number; rmse?: number; mape?: number; mase?: number }
  business_impact: string
  business_summary: string
  trend: string
  risks: string[]
  opportunities: string[]
  recommended_actions: string[]
  horizons: Record<string, number>
  cv_results: Record<string, any>[]
  skill_score: number | null
  error: string | null

  /** "sum" for additive metrics (a horizon figure is a total), "mean" for rates. */
  aggregation: string
  /** The headline business number: the total/average over the horizon, with its band. */
  horizon_total: number | null
  horizon_total_lower: number | null
  horizon_total_upper: number | null
  /** Same aggregate over the equivalent trailing window — what change_percent compares against. */
  baseline_window_value: number | null
  transform: string

  interval_level: number
  interval_method: string
  /** Leave-one-out coverage of the band on the back-test. Far from interval_level = don't trust the range. */
  measured_coverage: number | null

  /** Share of variation that is structure rather than period-to-period noise. */
  predictability: number | null
  trend_strength: number | null
  seasonal_strength: number | null
  signal_verdict: string

  anomalies_detected: number
  anomaly_periods: { index: number; z: number; date?: string }[]
  level_shift: { index: number; score: number } | null
  data_quality: Record<string, any>
  series_fingerprint: string
  notes: string[]

  /** Whether this forecast is fit to act on. Drives the banner in the UI. */
  reliability: 'reliable' | 'indicative' | 'unreliable' | 'unknown'
  reliability_headline: string
  reliability_reasons: string[]
  recommended_granularity: string
  granularity_reason: string

  /** How the previous run's forecast held up against what actually happened. */
  drift: {
    status: string
    message: string
    compared_points: number
    live_mae: number | null
    expected_mae: number | null
    error_ratio: number | null
    coverage: number | null
    bias: number | null
  } | null
}

export type ForecastRunResult = {
  run_id: string
  forecastable: boolean
  validation_message: string
  date_column: string
  frequency: string
  history_length: number
  outputs: ForecastOutput[]
  figures: Record<string, { data: any[]; layout: Record<string, any> }>
  report_md: string
}

export function useRunForecast(projectId: string) {
  const { call } = useApi()
  return useMutation({
    mutationFn: (input: {
      targets?: string[]
      horizon_days: number
      granularity: string
      model_override: string
      business_context?: string
    }) => call<ForecastRunResult>(`/api/v1/projects/${projectId}/forecast/run`, { method: 'POST', json: input }),
  })
}
