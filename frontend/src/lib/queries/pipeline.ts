'use client'

import { useMutation, useQuery } from '@tanstack/react-query'
import { useApi } from '@/lib/api/use-api'
import type { TablePreview } from '@/lib/queries/ingestion'

export type RelationshipCandidate = {
  table_a: string
  column_a: string
  table_b: string
  column_b: string
  confidence: number
  evidence: Record<string, number>
  source: string
  reasoning: string
  validation: {
    pk_uniqueness: number
    fk_coverage: number
    orphan_ratio: number
    dtype_compatible: boolean
  }
}

export type SchemaDiscoveryResult = {
  tables: { name: string; row_count: number; column_count: number; columns: string[] }[]
  auto: RelationshipCandidate[]
  review: RelationshipCandidate[]
  manual: RelationshipCandidate[]
}

export type RelationshipDecision = {
  table_a: string
  column_a: string
  table_b: string
  column_b: string
  confidence: number
  evidence: Record<string, number>
  status: 'approved' | 'rejected'
}

export type RelationshipOut = RelationshipDecision & { approved_by: string }

export function decisionKey(c: { table_a: string; column_a: string; table_b: string; column_b: string }) {
  return `${c.table_a}.${c.column_a}->${c.table_b}.${c.column_b}`
}

export type CleaningPlanStep = {
  id: string
  description: string
  /** 'detector' = derived deterministically from measured defects, 'generated' = LLM-refined,
   *  'manual' = typed by the user, 'fallback' = produced after an LLM failure. */
  source: 'generated' | 'manual' | 'detector' | 'fallback'
  /** Name of the deterministic operator this step runs. Empty means a free-text
   *  instruction, which is executed by generated code instead. */
  op?: string
  columns?: string[]
  params?: Record<string, any>
}

export type PlanResult = {
  table_name: string
  profile: Record<string, any>
  cleaning_plan: CleaningPlanStep[]
}

export type CleanResult = {
  table_name: string
  generated_code: string
  success: boolean
  error: string | null
  validation_report: Record<string, any>
  transformation_log: string[]
  retry_count: number
  preview: TablePreview | null
  /** Measured per-step audit trail: counts computed by diffing the table
   *  before and after each step, not reported by the code that changed it. */
  ledger: Record<string, any> | null
}

export type TableCleaningStatus = {
  table_name: string
  success: boolean
  error: string | null
  validation_report: Record<string, any>
  transformation_log: string[]
  retry_count: number
}

export type ReconciliationCheck = {
  table_a: string
  column_a: string
  table_b: string
  column_b: string
  orphan_rate_before: number
  orphan_rate_after: number
  within_tolerance: boolean
}

export type CleanRemainingResult = {
  results: TableCleaningStatus[]
  reconciliation: ReconciliationCheck[]
}

export type IntegrityResult = {
  passed: boolean
  report: Record<string, any>
}

export type SaveResult = {
  saved: boolean
  schema_name: string
  tables: string[]
}

export function useSchemaDiscovery(sessionId: string) {
  const { call } = useApi()
  return useMutation({
    mutationFn: () => call<SchemaDiscoveryResult>(`/api/v1/pipeline/${sessionId}/schema-discovery`, { method: 'POST' }),
  })
}

export function useSubmitRelationships(sessionId: string) {
  const { call } = useApi()
  return useMutation({
    mutationFn: (decisions: RelationshipDecision[]) =>
      call<RelationshipOut[]>(`/api/v1/pipeline/${sessionId}/relationships`, { method: 'POST', json: { decisions } }),
  })
}

export function usePlanTable(sessionId: string, tableName: string) {
  const { call } = useApi()
  return useMutation({
    mutationFn: () => call<PlanResult>(`/api/v1/pipeline/${sessionId}/tables/${tableName}/plan`, { method: 'POST', json: {} }),
  })
}

export function useEditPlan(sessionId: string, tableName: string) {
  const { call } = useApi()
  return useMutation({
    mutationFn: (cleaning_plan: CleaningPlanStep[]) =>
      call<PlanResult>(`/api/v1/pipeline/${sessionId}/tables/${tableName}/plan`, { method: 'PATCH', json: { cleaning_plan } }),
  })
}

export function usePlanStepOpinion(sessionId: string, tableName: string) {
  const { call } = useApi()
  return useMutation({
    mutationFn: (step_description: string) =>
      call<{ opinion: string }>(`/api/v1/pipeline/${sessionId}/tables/${tableName}/plan/opinion`, {
        method: 'POST', json: { step_description },
      }),
  })
}

export function useCleanTable(sessionId: string, tableName: string) {
  const { call } = useApi()
  return useMutation({
    mutationFn: () => call<CleanResult>(`/api/v1/pipeline/${sessionId}/tables/${tableName}/clean`, { method: 'POST', json: {} }),
  })
}

export function useCleanRemaining(sessionId: string) {
  const { call } = useApi()
  return useMutation({
    mutationFn: () => call<CleanRemainingResult>(`/api/v1/pipeline/${sessionId}/clean-remaining`, { method: 'POST' }),
  })
}

export function useReconciliation(sessionId: string, enabled: boolean) {
  const { call, ready } = useApi()
  return useQuery({
    queryKey: ['reconciliation', sessionId],
    queryFn: () => call<ReconciliationCheck[]>(`/api/v1/pipeline/${sessionId}/reconciliation`),
    enabled: ready && enabled,
  })
}

export function useIntegrity(sessionId: string) {
  const { call } = useApi()
  return useMutation({
    mutationFn: () => call<IntegrityResult>(`/api/v1/pipeline/${sessionId}/integrity`, { method: 'POST' }),
  })
}

export function useSaveToProject(sessionId: string) {
  const { call } = useApi()
  return useMutation({
    mutationFn: () => call<SaveResult>(`/api/v1/pipeline/${sessionId}/save`, { method: 'POST' }),
  })
}
