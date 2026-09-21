'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useApi } from '@/lib/api/use-api'

export type ProviderStatus = {
  id: string
  name: string
  configured: boolean
  honors_model_override: boolean
}

export type ProvidersStatusResult = {
  fallback_order: ProviderStatus[]
  any_configured: boolean
}

export function useProvidersStatus() {
  const { call, ready } = useApi()
  return useQuery({
    queryKey: ['settings-providers'],
    queryFn: () => call<ProvidersStatusResult>('/api/v1/settings/providers'),
    enabled: ready,
  })
}

export type AgentPurpose = {
  purpose: string
  label: string
  default_model: string
}

export type ModelPreferencesResult = {
  purposes: AgentPurpose[]
  available_models: string[]
  preferences: Record<string, string>
}

export function useModelPreferences() {
  const { call, ready } = useApi()
  return useQuery({
    queryKey: ['settings-models'],
    queryFn: () => call<ModelPreferencesResult>('/api/v1/settings/models'),
    enabled: ready,
  })
}

export function useUpdateModelPreferences() {
  const { call } = useApi()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (preferences: Record<string, string | null>) =>
      call<ModelPreferencesResult>('/api/v1/settings/models', { method: 'PATCH', json: { preferences } }),
    onSuccess: (data) => {
      queryClient.setQueryData(['settings-models'], data)
    },
  })
}
