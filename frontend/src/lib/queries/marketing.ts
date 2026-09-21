'use client'

import { useMutation } from '@tanstack/react-query'
import { useApi } from '@/lib/api/use-api'
import type { ChurnRunResult } from '@/lib/queries/churn'
import type { Grounding } from '@/lib/queries/insights'

export type MarketingRunResult = {
  schema_summary: Record<string, any>
  kpi: Record<string, any>
  rfm: {
    available: boolean
    reason?: string
    total_customers?: number
    total_revenue?: number
    snapshot_date?: string
    segment_order?: string[]
    segments?: Record<string, {
      count: number
      customer_pct: number
      revenue: number
      revenue_pct: number
      avg_monetary: number
      avg_frequency: number
      avg_recency_days: number
      playbook: string
    }>
  }
  marketing_kpis: {
    repeat_purchase_rate: number | null
    one_time_buyer_rate: number | null
    new_customers: number | null
    returning_customers: number | null
    avg_customer_value: number | null
    median_customer_value: number | null
    aov: number | null
    aov_median: number | null
    churn_risk_customers: number | null
    churn_risk_pct: number | null
    revenue_at_risk: number | null
    growth_segment_customers: number | null
    high_value_customers: number | null
    [key: string]: any
  }
  channels: Record<string, Record<string, { revenue?: number; revenue_pct?: number; count?: number; share_pct?: number }>>
  churn: {
    available: boolean
    reason?: string
    horizon_days?: number
    snapshot_date?: string
    model_name?: string
    model_auc?: number
    risk_distribution?: Record<string, number>
    revenue_at_risk?: number
    expected_revenue_at_risk?: number
    customers_scored?: number
    churn_risk_by_rfm_segment?: Record<string, {
      customers_scored: number
      avg_churn_probability: number
      high_risk_count: number
      high_risk_pct: number
    }>
    top_at_risk_customers?: any[]
    _target_lists?: { high_risk?: string[]; medium_risk?: string[] }
  }
  metadata: Record<string, any>
  report_md: string
  grounding: Grounding
}

export type Campaign = {
  name: string
  target_segment: string
  objective: string
  channel: string
  offer: string
  message_angle: string
  budget_allocation_pct: number | null
  priority: string
  primary_kpi: string
  expected_impact: string
}

export type CampaignPlanResult = {
  campaigns: Campaign[]
  summary: string
  error: string | null
}

export type AdCopyResult = {
  headlines: string[]
  primary_text: string[]
  cta: string[]
  email_subject: string
  email_preview: string
  sms: string
  hashtags: string[]
  notes: string
  error: string | null
}

export type ChatMessage = { role: string; content: string }

export function useRunMarketing(projectId: string) {
  const { call } = useApi()
  return useMutation({
    mutationFn: (input: {
      objective: string
      budget: string
      channels: string[]
      brand_voice: string
      business_context: string
      forecast_outputs?: Record<string, any>[] | null
      churn_result?: ChurnRunResult | null
    }) => call<MarketingRunResult>(`/api/v1/projects/${projectId}/marketing/run`, { method: 'POST', json: input }),
  })
}

export function useGenerateCampaignPlan(projectId: string) {
  const { call } = useApi()
  return useMutation({
    mutationFn: (input: {
      marketing_payload: Record<string, any>
      objective: string
      budget: string
      channels: string[]
      brand_voice: string
      business_context: string
    }) => call<CampaignPlanResult>(`/api/v1/projects/${projectId}/marketing/campaigns`, { method: 'POST', json: input }),
  })
}

export function useGenerateAdCopy(projectId: string) {
  const { call } = useApi()
  return useMutation({
    mutationFn: (input: {
      segment: string
      channel: string
      platform: string
      offer: string
      brand_voice: string
      segment_stats?: Record<string, any>
      playbook?: string
    }) => call<AdCopyResult>(`/api/v1/projects/${projectId}/marketing/ad-copy`, { method: 'POST', json: input }),
  })
}

export function useMarketingChat(projectId: string) {
  const { call } = useApi()
  return useMutation({
    mutationFn: (input: {
      message: string
      report_md: string
      marketing_payload: Record<string, any>
      history: ChatMessage[]
    }) => call<{ answer: string }>(`/api/v1/projects/${projectId}/marketing/chat`, { method: 'POST', json: input }),
  })
}
