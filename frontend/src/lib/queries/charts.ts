'use client'

import { useMutation } from '@tanstack/react-query'
import { useApi } from '@/lib/api/use-api'

export type ExplainChartInput = {
  figure: { data: any[]; layout: Record<string, any> }
  title?: string
  context?: string
}

export type ExplainChartResult = {
  explanation: string
}

export function useExplainChart(projectId: string) {
  const { call } = useApi()
  return useMutation({
    mutationFn: (input: ExplainChartInput) =>
      call<ExplainChartResult>(`/api/v1/projects/${projectId}/charts/explain`, { method: 'POST', json: input }),
  })
}
