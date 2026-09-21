'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useApi } from '@/lib/api/use-api'

export type Severity = 'critical' | 'high' | 'medium' | 'low' | 'info'
export type AlertStatus = 'new' | 'acknowledged' | 'resolved' | 'muted'

export type ChannelField = {
  key: string
  label: string
  placeholder?: string
  options?: string[]
  /** Only shown when the channel's `provider` config matches. */
  provider?: string
}

export type ChannelTypeInfo = {
  type: string
  label: string
  description: string
  destination_label: string
  destination_placeholder: string
  needs_destination: boolean
  secret_fields: ChannelField[]
  config_fields: ChannelField[]
}

export type Channel = {
  id: string
  type: string
  name: string
  destination: string
  config: Record<string, any>
  is_active: boolean
  /** Credentials are never returned — only whether some are stored. */
  has_credentials: boolean
  verified_at: string | null
  last_error: string | null
  created_at: string
}

export type Schedule = {
  id: string
  project_id: string
  project_name: string
  name: string
  is_active: boolean
  frequency: string
  hour: number
  minute: number
  day_of_week: number
  day_of_month: number
  cron_expression: string | null
  timezone: string
  analyses: string[]
  rules: Record<string, any>
  min_severity: Severity
  cooldown_hours: number
  max_alerts_per_run: number
  channel_ids: string[]
  language: string
  quiet_hours_start: number | null
  quiet_hours_end: number | null
  send_when_nothing_found: boolean
  last_run_at: string | null
  last_status: string | null
  next_run_at: string | null
  created_at: string
  /** Rendered server-side, so the UI never reimplements cron semantics. */
  description: string
}

export type MonitorRun = {
  id: string
  schedule_id: string
  schedule_name: string
  project_id: string
  project_name: string
  status: 'pending' | 'running' | 'success' | 'failed' | 'skipped'
  trigger: 'schedule' | 'manual'
  started_at: string
  finished_at: string | null
  duration_ms: number | null
  alerts_found: number
  alerts_delivered: number
  money_at_stake: number
  error: string | null
}

export type Alert = {
  id: string
  run_id: string
  project_id: string
  project_name: string
  fingerprint: string
  rule: string
  severity: Severity
  title: string
  body_md: string
  metric: string
  money_at_stake: number
  current_value: number | null
  prior_value: number | null
  change_pct: number | null
  evidence: Record<string, any>
  root_cause: Record<string, any>
  status: AlertStatus
  read_at: string | null
  created_at: string
}

export type RunDetail = MonitorRun & {
  briefing_md: string
  detail: Record<string, any>
  alerts: Alert[]
  deliveries: {
    id: string
    channel_type: string
    status: string
    destination: string
    error: string | null
    attempts: number
    created_at: string | null
  }[]
}

export type Preview = {
  available: boolean
  reason: string
  subject: string
  briefing_md: string
  text: string
  money_at_stake: number
  findings: {
    rule: string
    severity: Severity
    title: string
    body: string
    money_at_stake: number
    metric: string
  }[]
  suppressed: { rule: string; severity: Severity; title: string }[]
  root_cause: Record<string, any>
  would_deliver: boolean
  channels: { id: string; type: string; name: string; active: boolean }[]
}

export type SchedulerStatus = {
  running: boolean
  reason: string
  jobs: { id: string; name: string; next_run_at: string | null }[]
}

export type AlertSummary = {
  unread: number
  recent: Alert[]
}

// ── Channels ───────────────────────────────────────────────────────────────

export function useChannelTypes() {
  const { call, ready } = useApi()
  return useQuery({
    queryKey: ['monitoring', 'channel-types'],
    queryFn: () => call<ChannelTypeInfo[]>('/api/v1/monitoring/channel-types'),
    enabled: ready,
    staleTime: Infinity,
  })
}

export function useChannels() {
  const { call, ready } = useApi()
  return useQuery({
    queryKey: ['monitoring', 'channels'],
    queryFn: () => call<Channel[]>('/api/v1/monitoring/channels'),
    enabled: ready,
  })
}

export function useCreateChannel() {
  const { call } = useApi()
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (input: {
      type: string
      name: string
      destination?: string
      credentials?: Record<string, string>
      config?: Record<string, any>
      is_active?: boolean
    }) => call<Channel>('/api/v1/monitoring/channels', { method: 'POST', json: input }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['monitoring', 'channels'] }),
  })
}

export function useUpdateChannel() {
  const { call } = useApi()
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, ...input }: { id: string } & Record<string, any>) =>
      call<Channel>(`/api/v1/monitoring/channels/${id}`, { method: 'PATCH', json: input }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['monitoring', 'channels'] }),
  })
}

export function useDeleteChannel() {
  const { call } = useApi()
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) =>
      call<void>(`/api/v1/monitoring/channels/${id}`, { method: 'DELETE' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['monitoring', 'channels'] }),
  })
}

export function useTestChannel() {
  const { call } = useApi()
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) =>
      call<{ ok: boolean; channel_type: string; destination: string; error: string | null }>(
        `/api/v1/monitoring/channels/${id}/test`,
        { method: 'POST' }
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['monitoring', 'channels'] }),
  })
}

// ── Schedules ──────────────────────────────────────────────────────────────

export function useSchedules() {
  const { call, ready } = useApi()
  return useQuery({
    queryKey: ['monitoring', 'schedules'],
    queryFn: () => call<Schedule[]>('/api/v1/monitoring/schedules'),
    enabled: ready,
  })
}

export function useCreateSchedule(projectId: string) {
  const { call } = useApi()
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (input: Record<string, any>) =>
      call<Schedule>(`/api/v1/monitoring/projects/${projectId}/schedules`, {
        method: 'POST',
        json: input,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['monitoring'] }),
  })
}

export function useUpdateSchedule() {
  const { call } = useApi()
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, ...input }: { id: string } & Record<string, any>) =>
      call<Schedule>(`/api/v1/monitoring/schedules/${id}`, { method: 'PATCH', json: input }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['monitoring'] }),
  })
}

export function useDeleteSchedule() {
  const { call } = useApi()
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) =>
      call<void>(`/api/v1/monitoring/schedules/${id}`, { method: 'DELETE' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['monitoring'] }),
  })
}

export function usePreviewSchedule() {
  const { call } = useApi()
  return useMutation({
    mutationFn: (id: string) =>
      call<Preview>(`/api/v1/monitoring/schedules/${id}/preview`, { method: 'POST' }),
  })
}

export function useRunScheduleNow() {
  const { call } = useApi()
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) =>
      call<MonitorRun>(`/api/v1/monitoring/schedules/${id}/run`, { method: 'POST' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['monitoring'] }),
  })
}

// ── Runs and alerts ────────────────────────────────────────────────────────

export function useRuns(limit = 50) {
  const { call, ready } = useApi()
  return useQuery({
    queryKey: ['monitoring', 'runs', limit],
    queryFn: () => call<MonitorRun[]>(`/api/v1/monitoring/runs?limit=${limit}`),
    enabled: ready,
  })
}

export function useRun(runId: string | null) {
  const { call, ready } = useApi()
  return useQuery({
    queryKey: ['monitoring', 'run', runId],
    queryFn: () => call<RunDetail>(`/api/v1/monitoring/runs/${runId}`),
    enabled: ready && Boolean(runId),
  })
}

export function useAlerts(status?: AlertStatus) {
  const { call, ready } = useApi()
  return useQuery({
    queryKey: ['monitoring', 'alerts', status ?? 'all'],
    queryFn: () =>
      call<Alert[]>(
        `/api/v1/monitoring/alerts${status ? `?status_filter=${status}` : ''}`
      ),
    enabled: ready,
  })
}

/** Drives the header bell. Polled, because alerts arrive from a scheduler the
 * browser has no other way of hearing from. */
export function useAlertSummary() {
  const { call, ready } = useApi()
  return useQuery({
    queryKey: ['monitoring', 'alert-summary'],
    queryFn: () => call<AlertSummary>('/api/v1/monitoring/alerts/summary'),
    enabled: ready,
    refetchInterval: 60_000,
    refetchOnWindowFocus: true,
  })
}

export function useUpdateAlert() {
  const { call } = useApi()
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, status }: { id: string; status: AlertStatus }) =>
      call<Alert>(`/api/v1/monitoring/alerts/${id}`, { method: 'PATCH', json: { status } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['monitoring'] }),
  })
}

export function useMarkAllAlertsRead() {
  const { call } = useApi()
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () =>
      call<AlertSummary>('/api/v1/monitoring/alerts/read-all', { method: 'POST' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['monitoring'] }),
  })
}

export function useSchedulerStatus() {
  const { call, ready } = useApi()
  return useQuery({
    queryKey: ['monitoring', 'scheduler'],
    queryFn: () => call<SchedulerStatus>('/api/v1/monitoring/scheduler'),
    enabled: ready,
    refetchInterval: 120_000,
  })
}
