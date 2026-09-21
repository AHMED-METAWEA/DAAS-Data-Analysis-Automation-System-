'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useApi } from '@/lib/api/use-api'

export type ReportSummary = {
  id: string
  project_id: string
  project_name: string
  type: string
  title: string
  created_at: string
}

export type ReportDetail = ReportSummary & {
  markdown: string
  grounding: Record<string, unknown>
}

export function useMyReports() {
  const { call, ready } = useApi()
  return useQuery({
    queryKey: ['reports'],
    queryFn: () => call<ReportSummary[]>('/api/v1/reports'),
    enabled: ready,
  })
}

export function useReport(reportId: string | null) {
  const { call, ready } = useApi()
  return useQuery({
    queryKey: ['report', reportId],
    queryFn: () => call<ReportDetail>(`/api/v1/reports/${reportId}`),
    enabled: ready && Boolean(reportId),
  })
}

export function useSaveReport(projectId: string) {
  const { call } = useApi()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: { type: string; title: string; markdown: string; grounding?: Record<string, unknown> }) =>
      call<ReportDetail>(`/api/v1/projects/${projectId}/reports`, { method: 'POST', json: input }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['reports'] })
    },
  })
}
