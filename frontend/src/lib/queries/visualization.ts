'use client'

import { useMutation, useQuery } from '@tanstack/react-query'
import { useApi } from '@/lib/api/use-api'

export type KpiCard = {
  label: string
  value: string
  direction?: string | null
}

export type Chart = {
  title: string
  insight: string
  width: 'half' | 'full'
  figure: { data: any[]; layout: Record<string, any> }
}

export type DashboardResult = {
  kpis: KpiCard[]
  charts: Chart[]
  revenue_label: string
  row_count: number
}

export function useDashboard(projectId: string) {
  const { call, ready } = useApi()
  return useQuery({
    queryKey: ['dashboard', projectId],
    queryFn: () => call<DashboardResult>(`/api/v1/projects/${projectId}/dashboard`),
    enabled: ready && Boolean(projectId),
  })
}

export type SnapshotResult = {
  kpis: KpiCard[]
  row_count: number
  column_count: number
}

export function useDashboardSnapshot(projectId: string) {
  const { call, ready } = useApi()
  return useQuery({
    queryKey: ['dashboard-snapshot', projectId],
    queryFn: () => call<SnapshotResult>(`/api/v1/projects/${projectId}/dashboard/snapshot`),
    enabled: ready && Boolean(projectId),
  })
}

export function useAskForChart(projectId: string) {
  const { call } = useApi()
  return useMutation({
    mutationFn: (query: string) =>
      call<{ figures: Chart['figure'][]; output: string; error: string | null }>(
        `/api/v1/projects/${projectId}/dashboard/charts/ask`,
        { method: 'POST', json: { query } }
      ),
  })
}

export function useAutoCharts(projectId: string) {
  const { call } = useApi()
  return useMutation({
    mutationFn: (focus_context: string) =>
      call<{ charts: Chart[] }>(`/api/v1/projects/${projectId}/dashboard/charts/auto`, {
        method: 'POST',
        json: { focus_context },
      }),
  })
}

export function dashboardExportUrl(projectId: string): string {
  // 127.0.0.1, not localhost — see the note in lib/api/client.ts (IPv4/IPv6 loopback).
  const base = process.env.NEXT_PUBLIC_API_URL ?? 'http://127.0.0.1:8000'
  return `${base}/api/v1/projects/${projectId}/dashboard/export-html`
}
