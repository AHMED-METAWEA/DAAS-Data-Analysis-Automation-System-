'use client'

import { useMutation } from '@tanstack/react-query'
import { useApi } from '@/lib/api/use-api'

export type TablePreview = {
  name: string
  rows: number
  columns: string[]
  preview: Record<string, unknown>[]
}

export type IngestResponse = {
  pipeline_session_id: string
  project_id: string
  primary_table: string
  tables: TablePreview[]
}

export function useIngestFiles(projectId: string) {
  const { call } = useApi()
  return useMutation({
    mutationFn: (files: File[]) => {
      const form = new FormData()
      for (const f of files) form.append('files', f)
      return call<IngestResponse>(`/api/v1/projects/${projectId}/ingest/files`, {
        method: 'POST',
        body: form,
      })
    },
  })
}

export function useIngestGoogleSheet(projectId: string) {
  const { call } = useApi()
  return useMutation({
    mutationFn: (input: { service_account_json: string; sheet_url_or_id: string }) =>
      call<IngestResponse>(`/api/v1/projects/${projectId}/ingest/gsheet`, {
        method: 'POST',
        json: input,
      }),
  })
}

export type DbLinkInput = {
  dialect: 'postgresql' | 'mysql' | 'mssql'
  host: string
  port: number
  database: string
  username: string
  password: string
}

export function useTestDbLink() {
  const { call } = useApi()
  return useMutation({
    mutationFn: (input: DbLinkInput) =>
      call<{ status: string }>('/api/v1/ingestion/db-link/test', {
        method: 'POST',
        json: input,
      }),
  })
}

export function useIngestDbLink(projectId: string) {
  const { call } = useApi()
  return useMutation({
    mutationFn: (input: DbLinkInput & { tables?: string[]; save_connection?: boolean }) =>
      call<IngestResponse>(`/api/v1/projects/${projectId}/ingest/db-link`, {
        method: 'POST',
        json: input,
      }),
  })
}
