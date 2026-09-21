'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useApi } from '@/lib/api/use-api'

/** Mirrors backend/app/schemas/crm.py. Kept as a separate declaration from the
 * domain record for the same reason the backend does: what the browser is
 * promised should not change every time an internal field is renamed. */

export type RiskTier = 'High' | 'Medium' | 'Low'

export type CustomerDriver = {
  feature: string
  shap_value: number | null
  direction: string | null
}

export type CustomerSummary = {
  customer_id: string
  display_name: string | null
  snapshot_date: string

  recency_days: number | null
  frequency: number | null
  monetary: number | null
  avg_order_value: number | null
  last_order_date: string | null

  rfm_segment: string | null
  lifecycle_stage: string | null

  churn_probability: number | null
  risk_tier: RiskTier | null
  predicted_clv: number | null

  value_at_risk: number | null
  /** 'predicted_clv' | 'historical_monetary' — always sent alongside the
   * figure, so a prioritised list can state what it prioritised on. */
  value_basis: string | null
}

export type CustomerDetail = CustomerSummary & {
  contact: Record<string, unknown>
  tenure_days: number | null
  first_order_date: string | null
  r_score: number | null
  f_score: number | null
  m_score: number | null
  clv_horizon_days: number | null
  predicted_purchases: number | null
  drivers: CustomerDriver[]
  /** Churn explains only the displayed top-N, so most customers legitimately
   * have no drivers. Distinguishes that from "nothing drives their risk". */
  explained: boolean
}

export type CustomerHistoryPoint = {
  snapshot_date: string
  churn_probability: number | null
  risk_tier: RiskTier | null
  predicted_clv: number | null
  value_at_risk: number | null
  monetary: number | null
  frequency: number | null
  recency_days: number | null
  rfm_segment: string | null
  lifecycle_stage: string | null
}

export type SnapshotSummary = {
  id: string
  snapshot_date: string
  status: string
  trigger: string
  customers: number
  components: Record<string, any>
  row_count: number
  history_days: number
  duration_ms: number | null
}

export type PortfolioResponse = {
  available: boolean
  reason: string | null
  snapshot_date: string | null
  customers: number
  /** Null, not zero, when the project's data carries no monetary column — the
   * UI must render an em-dash for null and never a currency figure. */
  total_monetary: number | null
  total_value_at_risk: number | null
  avg_churn_probability: number | null
  customers_scored: number
  customers_with_clv: number
  customers_with_monetary: number
  customers_with_value_at_risk: number
  by_segment: Record<string, number>
  by_stage: Record<string, number>
  by_risk_tier: Record<string, number>
  value_basis: Record<string, number>
  latest_snapshot: SnapshotSummary | null
}

export type CustomerListResponse = {
  available: boolean
  reason: string | null
  snapshot_date: string | null
  total: number
  limit: number
  offset: number
  customers: CustomerSummary[]
}

export type CustomerDetailResponse = {
  available: boolean
  reason: string | null
  customer: CustomerDetail | null
  history: CustomerHistoryPoint[]
}

export type RankingComparisonRow = {
  rank: number
  customer_id: string
  display_name: string | null
  churn_probability: number | null
  monetary: number | null
  predicted_clv: number | null
  value_at_risk: number | null
  rfm_segment: string | null
  lifecycle_stage: string | null
}

export type RankingComparisonResponse = {
  available: boolean
  reason: string | null
  snapshot_date: string | null
  top_n: number
  value_basis: string | null
  by_risk: RankingComparisonRow[]
  by_value_at_risk: RankingComparisonRow[]
  value_covered_by_risk: number
  value_covered_by_value_at_risk: number
  difference: number
  overlap: number
}

export type CrmRefreshResponse = {
  status: 'success' | 'partial' | 'failed' | 'skipped'
  snapshot_id: string | null
  snapshot_date: string | null
  customers: number
  components: Record<string, any>
  grain: Record<string, any>
  stage_rules: Record<string, any>
  duration_ms: number | null
  reason: string | null
}

export type CustomerFilters = {
  segment?: string
  stage?: string
  tier?: string
  search?: string
  sort_by?: string
  descending?: boolean
  limit?: number
  offset?: number
}

function toQuery(filters: CustomerFilters): string {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(filters)) {
    // Empty strings are the "no filter" state of the Select controls, and must
    // not be sent — the backend would filter on a segment named "".
    if (value === undefined || value === null || value === '') continue
    params.set(key, String(value))
  }
  const query = params.toString()
  return query ? `?${query}` : ''
}

const base = (projectId: string) => `/api/v1/projects/${projectId}/crm`

export function usePortfolio(projectId: string) {
  const { call, ready } = useApi()
  return useQuery({
    queryKey: ['crm', 'portfolio', projectId],
    queryFn: () => call<PortfolioResponse>(`${base(projectId)}/portfolio`),
    enabled: ready && Boolean(projectId),
  })
}

export function useCustomers(projectId: string, filters: CustomerFilters) {
  const { call, ready } = useApi()
  return useQuery({
    queryKey: ['crm', 'customers', projectId, filters],
    queryFn: () => call<CustomerListResponse>(`${base(projectId)}/customers${toQuery(filters)}`),
    enabled: ready && Boolean(projectId),
    placeholderData: (previous) => previous,
  })
}

export function useCustomerDetail(projectId: string, customerId: string | null) {
  const { call, ready } = useApi()
  return useQuery({
    queryKey: ['crm', 'customer', projectId, customerId],
    queryFn: () =>
      call<CustomerDetailResponse>(
        `${base(projectId)}/customers/${encodeURIComponent(customerId as string)}`
      ),
    enabled: ready && Boolean(projectId) && Boolean(customerId),
  })
}

export function useRankingComparison(projectId: string, topN = 10) {
  const { call, ready } = useApi()
  return useQuery({
    queryKey: ['crm', 'ranking-comparison', projectId, topN],
    queryFn: () =>
      call<RankingComparisonResponse>(`${base(projectId)}/ranking-comparison?top_n=${topN}`),
    enabled: ready && Boolean(projectId),
  })
}

export function useSnapshots(projectId: string, limit = 30) {
  const { call, ready } = useApi()
  return useQuery({
    queryKey: ['crm', 'snapshots', projectId, limit],
    queryFn: () => call<SnapshotSummary[]>(`${base(projectId)}/snapshots?limit=${limit}`),
    enabled: ready && Boolean(projectId),
  })
}

export function useRefreshCrm(projectId: string) {
  const { call } = useApi()
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (input: { run_churn: boolean; horizon_days: number }) =>
      call<CrmRefreshResponse>(`${base(projectId)}/refresh`, { method: 'POST', json: input }),
    // A refresh writes a new snapshot, which every other view on the page reads
    // from. Invalidating the whole `crm` key is what keeps the portfolio KPIs,
    // the list and the comparison describing the same moment.
    onSuccess: () => qc.invalidateQueries({ queryKey: ['crm'] }),
  })
}
