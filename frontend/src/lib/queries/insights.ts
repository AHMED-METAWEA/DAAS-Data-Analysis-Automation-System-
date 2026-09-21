'use client'

import { useMutation } from '@tanstack/react-query'
import { useApi } from '@/lib/api/use-api'
import type { DashboardResult } from '@/lib/queries/visualization'

export type Grounding = {
  status: 'none' | 'clean' | 'partial' | 'weak'
  label: string
  total: number
  verified_count: number
  coverage: number
}

/** One computed figure the report was allowed to cite, with its derivation. */
export type Figure = {
  key: string
  label: string
  value: number
  unit: string
  display: string
  formula: string
  period: string
  quality: 'exact' | 'scenario'
  assumption: string
}

/** How every number in the report got there. */
export type Verification = {
  status: 'verified' | 'unverified'
  label: string
  figures_cited_from_engine: number
  figures_typed_by_model: number
  typed_and_verified: number
  citation_rate: number
  unverified_figures: { figure: string; value: number; context: string }[]
  unknown_citations: string[]
  unsupported_currency_claims: string[]
  misvalued_actions: string[]
  ambiguous_citations: string[]
  corrected: boolean
}

/** A finding, ranked by the money it puts at stake. */
export type EvidenceItem = {
  rank: number
  tag: 'THREAT' | 'LEAK' | 'RISK' | 'OPPORTUNITY' | 'STRENGTH' | 'CAVEAT'
  section: string
  money_at_stake: number
  text: string
}

export type InsightsResult = {
  report_md: string
  template: string
  grounding: Grounding
  analytics: Record<string, any>
  dashboard: DashboardResult
  verification?: Verification | null
  figures?: Figure[]
  evidence?: EvidenceItem[]
  blind_spots?: string[]
  decision?: Record<string, any>
}

export function useGenerateInsights(projectId: string) {
  const { call } = useApi()
  return useMutation({
    mutationFn: (input: { business_context: string; template_override?: string | null }) =>
      call<InsightsResult>(`/api/v1/projects/${projectId}/insights`, { method: 'POST', json: input }),
  })
}

export type ChatMessage = { role: 'user' | 'assistant'; content: string }

export type ChatResult = {
  answer: string
  output: string
  error: string | null
  figure: { data: any[]; layout: Record<string, any> } | null
  preview: Record<string, unknown>[] | null
}

export function useInsightsChat(projectId: string) {
  const { call } = useApi()
  return useMutation({
    mutationFn: (input: { message: string; report_md: string; template_label: string; history: ChatMessage[] }) =>
      call<ChatResult>(`/api/v1/projects/${projectId}/insights/chat`, { method: 'POST', json: input }),
  })
}
