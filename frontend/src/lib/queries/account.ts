'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useApi } from '@/lib/api/use-api'

export type Profile = {
  id: string
  email: string
  name: string
  role: string
  organization: string | null
  timezone: string | null
  bio: string | null
}

export function useProfile() {
  const { call, ready } = useApi()
  return useQuery({
    queryKey: ['account-profile'],
    queryFn: () => call<Profile>('/api/v1/auth/me'),
    enabled: ready,
  })
}

export function useUpdateProfile() {
  const { call } = useApi()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: { name?: string; role?: string; organization?: string; timezone?: string; bio?: string }) =>
      call<Profile>('/api/v1/account/profile', { method: 'PATCH', json: input }),
    onSuccess: (data) => {
      queryClient.setQueryData(['account-profile'], data)
    },
  })
}

export function useChangePassword() {
  const { call } = useApi()
  return useMutation({
    mutationFn: (input: { current_password: string; new_password: string }) =>
      call<void>('/api/v1/account/password', { method: 'POST', json: input }),
  })
}

export type ApiKeySummary = {
  id: string
  name: string
  prefix: string
  revoked: boolean
  created_at: string
  last_used_at: string | null
}

export type ApiKeyCreated = ApiKeySummary & { key: string }

export function useApiKeys() {
  const { call, ready } = useApi()
  return useQuery({
    queryKey: ['account-api-keys'],
    queryFn: () => call<ApiKeySummary[]>('/api/v1/account/api-keys'),
    enabled: ready,
  })
}

export function useCreateApiKey() {
  const { call } = useApi()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: { name: string }) =>
      call<ApiKeyCreated>('/api/v1/account/api-keys', { method: 'POST', json: input }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['account-api-keys'] })
    },
  })
}

export function useRevokeApiKey() {
  const { call } = useApi()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (keyId: string) => call<void>(`/api/v1/account/api-keys/${keyId}`, { method: 'DELETE' }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['account-api-keys'] })
    },
  })
}
