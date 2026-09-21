'use client'

import { useMutation } from '@tanstack/react-query'
import { useApi } from '@/lib/api/use-api'

export type AskMessage = { role: string; content: string }

export type AskResult = {
  answer: string
  output: string
  error: string | null
  figure: { data: any[]; layout: Record<string, any> } | null
  preview: Record<string, unknown>[] | null
}

export function useAskDaas(projectId: string) {
  const { call } = useApi()
  return useMutation({
    mutationFn: (input: { message: string; history: AskMessage[] }) =>
      call<AskResult>(`/api/v1/projects/${projectId}/ask`, { method: 'POST', json: input }),
  })
}
