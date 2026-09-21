'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useApi } from '@/lib/api/use-api'

export type Project = {
  id: string
  name: string
  slug: string
  data_source_mode: 'files' | 'database' | 'google_sheet'
  status: string
  created_at: string
  updated_at: string
}

export function useProjects() {
  const { call, ready } = useApi()
  return useQuery({
    queryKey: ['projects'],
    queryFn: () => call<Project[]>('/api/v1/projects'),
    enabled: ready,
  })
}

export function useCreateProject() {
  const { call } = useApi()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: { name: string; data_source_mode?: Project['data_source_mode'] }) =>
      call<Project>('/api/v1/projects', { method: 'POST', json: input }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['projects'] })
    },
  })
}

export function useDeleteProject() {
  const { call } = useApi()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (projectId: string) =>
      call<void>(`/api/v1/projects/${projectId}`, { method: 'DELETE' }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['projects'] })
    },
  })
}
