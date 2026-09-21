'use client'

import { useMutation, useQuery } from '@tanstack/react-query'
import { useApi } from '@/lib/api/use-api'

/** A column the search may slice by. */
export type RcDimension = {
  name: string
  source: 'column' | 'derived'
  cardinality: number
  kind: string
  role: string
}

export type RcMeasure = {
  key: string
  label: string
  unit: string
  additive: boolean
}

export type RcWindowOption = {
  mode: string
  label: string
  current: string
  prior: string
  current_start?: string
  current_end?: string
  prior_start?: string
  prior_end?: string
}

export type RootCauseOptions = {
  available: boolean
  reason: string
  time_column: string | null
  measures: RcMeasure[]
  dimensions: RcDimension[]
  dimensions_skipped: { column: string; reason: string }[]
  windows: RcWindowOption[]
}

/** How a slice's own change splits into order count vs order size. */
export type RcBridge = {
  available: boolean
  reason?: string
  orders_prior?: number
  orders_current?: number
  orders_change?: number
  aov_prior?: number
  aov_current?: number
  aov_change?: number
  volume_effect?: number
  basket_effect?: number
  interaction_effect?: number
  residual?: number
  primary_driver?: string
}

export type RcExplanation = {
  rank: number
  slice: Record<string, string>
  slice_label: string
  slice_expression: string
  depth: number
  prior_value: number
  current_value: number
  delta: number
  change_pct: number | null
  explanatory_power: number
  explanatory_power_pct: number
  expected_current: number
  excess: number
  excess_share: number
  excess_share_pct: number
  prior_rows: number
  current_rows: number
  rows_share: number
  rows_share_pct: number
  /** |share of the change| ÷ |share of the rows| — "23× its own weight". */
  concentration: number
  surprise: number
  /** Standard errors the excess clears. Null for distinct-count measures. */
  signal_to_noise: number | null
  p_value: number | null
  /** Survives the Bonferroni correction for how many slices were tested. */
  robust: boolean | null
  bridge: RcBridge | null
  rest_prior: number
  rest_current: number
  rest_delta: number
  rest_change_pct: number | null
  direction: 'decline' | 'increase'
  score: number
}

export type RcStats = {
  dimensions_searched: string[]
  dimensions_skipped: { column: string; reason: string }[]
  max_depth: number
  beam_width: number
  node_budget: number
  nodes_evaluated: number
  nodes_examined: number
  duplicate_paths: number
  nodes_expanded: number
  pruned_by_support: number
  pruned_by_magnitude_bound: number
  pruned_by_beam: number
  pruned_by_redundancy: number
  budget_exhausted: boolean
  elapsed_seconds: number
  exhaustive_combinations: number
  slices_tested: number
  corrected_threshold: number
  search_reduction: number | null
}

export type RcDimensionSummary = {
  dimension: string
  role: string
  cardinality: number
  divergence: number
  elements_for_two_thirds: number | null
  top_elements: {
    value: string
    prior: number
    current: number
    delta: number
    explanatory_power_pct: number
  }[]
  offsetting_elements: {
    value: string
    prior: number
    current: number
    delta: number
    explanatory_power_pct: number
  }[]
}

export type RcDrillStep = {
  added: string
  slice_label: string
  depth: number
  delta: number
  explanatory_power_pct: number
  rows_share_pct: number
  concentration: number
}

export type RcWindow = {
  label: string
  start: string
  end: string
  value: number
  rows: number
}

export type RootCauseResult = {
  available: boolean
  reason: string
  measure: string
  measure_label: string
  measure_unit: string
  measure_basis: string
  measure_additive: boolean
  current: RcWindow | null
  prior: RcWindow | null
  total_delta: number
  total_change_pct: number | null
  window_basis: string
  explanations: RcExplanation[]
  per_dimension: RcDimensionSummary[]
  drill_path: RcDrillStep[]
  dimensions: RcDimension[]
  stats: RcStats
  warnings: string[]
  narrative: string
  figures: Record<string, unknown>[]
  verification: Record<string, unknown>
}

export type RootCauseRequest = {
  measure?: string
  window_mode?: string
  current_start?: string | null
  current_end?: string | null
  prior_start?: string | null
  prior_end?: string | null
  dimensions?: string[] | null
  include_weekday?: boolean
  max_depth?: number
  beam_width?: number
  top_k?: number
  min_explanatory_power?: number
  min_signal_to_noise?: number
  with_narrative?: boolean
  language?: string
  business_context?: string
}

export function useRootCauseOptions(projectId: string) {
  const { call, ready } = useApi()
  return useQuery({
    queryKey: ['root-cause-options', projectId],
    queryFn: () => call<RootCauseOptions>(`/api/v1/projects/${projectId}/root-cause/options`),
    enabled: ready && Boolean(projectId),
    retry: false,
  })
}

export function useRunRootCause(projectId: string) {
  const { call } = useApi()
  return useMutation({
    mutationFn: (input: RootCauseRequest) =>
      call<RootCauseResult>(`/api/v1/projects/${projectId}/root-cause`, {
        method: 'POST',
        json: input,
      }),
  })
}
