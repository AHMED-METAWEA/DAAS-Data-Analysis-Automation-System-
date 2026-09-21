'use client'

import { useMutation } from '@tanstack/react-query'
import { useApi } from '@/lib/api/use-api'

export type TopDriver = {
  feature: string
  shap_value: number
  direction: 'increases_risk' | 'decreases_risk'
}

export type AtRiskCustomer = {
  customer: string
  name: string
  churn_probability: number
  risk_tier: string
  recency_days: number
  frequency: number
  monetary: number | null
  expected_loss: number | null
  top_drivers: TopDriver[]
}

export type FeatureImportance = {
  feature: string
  importance: number
}

export type ChurnRunResult = {
  available: boolean
  reason: string | null
  horizon_days: number | null
  requested_horizon_days: number | null
  snapshot_date: string | null
  cutoff_date: string | null
  training_cutoffs: string[]
  model: Record<string, any>
  feature_importance: FeatureImportance[]
  shap_global_importance: FeatureImportance[]
  risk_distribution: Record<string, number>
  customers_scored: number
  revenue_at_risk: number | null
  expected_revenue_at_risk: number | null
  at_risk_customers: AtRiskCustomer[]
  at_risk_ranked_by: string
  metadata: Record<string, any>
  customer_scores: Record<string, number> | null
}

export function useRunChurn(projectId: string) {
  const { call } = useApi()
  return useMutation({
    mutationFn: (input: { horizon_days: number; top_n: number }) =>
      call<ChurnRunResult>(`/api/v1/projects/${projectId}/churn/run`, { method: 'POST', json: input }),
  })
}

export function useRetentionPlan(projectId: string) {
  const { call } = useApi()
  return useMutation({
    mutationFn: (input: { churn_result: ChurnRunResult; business_context?: string }) =>
      call<{ report_md: string }>(`/api/v1/projects/${projectId}/churn/retention-plan`, {
        method: 'POST',
        json: input,
      }),
  })
}
