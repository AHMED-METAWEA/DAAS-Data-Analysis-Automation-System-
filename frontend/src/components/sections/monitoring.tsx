'use client'

import * as React from 'react'
import ReactMarkdown from 'react-markdown'
import { useTranslations } from 'next-intl'
import { cn } from '@/lib/utils'
import { useAppStore } from '@/lib/store'
import {
  useAlerts, useChannelTypes, useChannels, useCreateChannel, useCreateSchedule,
  useDeleteChannel, useDeleteSchedule, usePreviewSchedule, useRun, useRunScheduleNow,
  useRuns, useSchedulerStatus, useSchedules, useTestChannel, useUpdateAlert,
  useUpdateChannel, useUpdateSchedule, useMarkAllAlertsRead,
  type Alert, type Channel, type ChannelTypeInfo, type Schedule, type Severity,
} from '@/lib/queries/monitoring'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { SectionScroll, SectionHeader } from '@/components/shared/layout'
import { EmptyState, ErrorState, LoadingState } from '@/components/shared/states'
import * as Icons from 'lucide-react'

const SEVERITY_VARIANT: Record<Severity, 'danger' | 'warning' | 'info' | 'default'> = {
  critical: 'danger', high: 'danger', medium: 'warning', low: 'info', info: 'default',
}

const CHANNEL_ICON: Record<string, string> = {
  in_app: 'Bell', email: 'Mail', telegram: 'Send', whatsapp: 'MessageCircle',
}

function fmtMoney(value: number): string {
  if (!value) return '—'
  return value.toLocaleString(undefined, { maximumFractionDigits: 0 })
}

function fmtDate(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

/** The scheduler's own health. A monitoring feature that cannot say whether it
 * is running has the problem it exists to solve. */
function SchedulerBanner({ t }: { t: ReturnType<typeof useTranslations> }) {
  const status = useSchedulerStatus()
  if (!status.data) return null
  if (status.data.running) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-success/25 bg-success/5 px-3 py-2">
        <span className="relative flex size-2">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-success opacity-75" />
          <span className="relative inline-flex size-2 rounded-full bg-success" />
        </span>
        <span className="text-sm">
          {t('schedulerRunning', { count: status.data.jobs.length })}
        </span>
      </div>
    )
  }
  return (
    <div className="flex items-start gap-2 rounded-lg border border-warning/30 bg-warning/5 px-3 py-2">
      <Icons.AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-warning" />
      <span className="text-sm">{status.data.reason}</span>
    </div>
  )
}

// ── Schedules ──────────────────────────────────────────────────────────────

const FREQUENCIES = ['daily', 'weekdays', 'weekly', 'monthly', 'hourly', 'custom'] as const
const SEVERITIES: Severity[] = ['critical', 'high', 'medium', 'low', 'info']
const ANALYSES = ['insights', 'root_cause'] as const
const WEEKDAYS = [0, 1, 2, 3, 4, 5, 6]

/** Timezones offered first. The whole point of storing a zone per schedule is
 * that 07:00 in Cairo and 07:00 in Dubai are different instants. */
const COMMON_TIMEZONES = [
  'Africa/Cairo', 'Asia/Dubai', 'Asia/Riyadh', 'Europe/London', 'Europe/Berlin',
  'America/New_York', 'Asia/Kolkata', 'UTC',
]

type ScheduleForm = Partial<Schedule> & { channel_ids: string[]; analyses: string[] }

function ScheduleEditor({
  initial, channels, channelTypes, projectId, onDone, t,
}: {
  initial: Schedule | null
  channels: Channel[]
  channelTypes: ChannelTypeInfo[]
  projectId: string
  onDone: () => void
  t: ReturnType<typeof useTranslations>
}) {
  const create = useCreateSchedule(projectId)
  const update = useUpdateSchedule()
  // Adding a delivery channel from inside the schedule form. Without this the
  // first schedule anyone creates dead-ends: the "where it goes" step says
  // there are no channels, and the only way to make one is to abandon the
  // half-filled form, switch tabs, and start over.
  const [addingChannel, setAddingChannel] = React.useState(false)
  const [form, setForm] = React.useState<ScheduleForm>(() => ({
    name: initial?.name ?? 'Daily check',
    frequency: initial?.frequency ?? 'daily',
    hour: initial?.hour ?? 7,
    minute: initial?.minute ?? 0,
    day_of_week: initial?.day_of_week ?? 0,
    day_of_month: initial?.day_of_month ?? 1,
    cron_expression: initial?.cron_expression ?? '',
    timezone: initial?.timezone ?? Intl.DateTimeFormat().resolvedOptions().timeZone ?? 'UTC',
    analyses: initial?.analyses ?? ['insights', 'root_cause'],
    min_severity: initial?.min_severity ?? 'medium',
    cooldown_hours: initial?.cooldown_hours ?? 24,
    max_alerts_per_run: initial?.max_alerts_per_run ?? 5,
    channel_ids: initial?.channel_ids ?? [],
    language: initial?.language ?? 'en',
    quiet_hours_start: initial?.quiet_hours_start ?? null,
    quiet_hours_end: initial?.quiet_hours_end ?? null,
    send_when_nothing_found: initial?.send_when_nothing_found ?? false,
    is_active: initial?.is_active ?? true,
    rules: initial?.rules ?? {},
  }))

  const set = <K extends keyof ScheduleForm>(key: K, value: ScheduleForm[K]) =>
    setForm((f) => ({ ...f, [key]: value }))

  const targets = (form.rules?.targets ?? {}) as Record<string, { min?: number; max?: number }>
  const setTarget = (metric: string, bound: 'min' | 'max', raw: string) => {
    const next = { ...targets }
    const value = raw.trim() === '' ? undefined : Number(raw)
    const entry = { ...(next[metric] ?? {}) }
    if (value === undefined || Number.isNaN(value)) delete entry[bound]
    else entry[bound] = value
    if (Object.keys(entry).length === 0) delete next[metric]
    else next[metric] = entry
    set('rules', { ...(form.rules ?? {}), targets: next })
  }

  const submit = () => {
    const payload = { ...form }
    if (payload.frequency !== 'custom') delete payload.cron_expression
    if (initial) update.mutate({ id: initial.id, ...payload }, { onSuccess: onDone })
    else create.mutate(payload, { onSuccess: onDone })
  }

  const pending = create.isPending || update.isPending
  const error = (create.error ?? update.error) as Error | null

  return (
    <div className="space-y-5">
      <div className="space-y-1.5">
        <Label className="text-sm">{t('scheduleName')}</Label>
        <Input value={form.name ?? ''} onChange={(e) => set('name', e.target.value)} />
      </div>

      {/* ── When ─────────────────────────────────────────────────────── */}
      <fieldset className="space-y-3 rounded-lg border border-border p-3">
        <legend className="px-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          {t('when')}
        </legend>
        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1.5">
            <Label className="text-sm">{t('frequency')}</Label>
            <Select value={form.frequency} onValueChange={(v) => set('frequency', v)}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                {FREQUENCIES.map((f) => (
                  <SelectItem key={f} value={f}>{t(`freq.${f}`)}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label className="text-sm">{t('timezone')}</Label>
            <Select value={form.timezone} onValueChange={(v) => set('timezone', v)}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                {Array.from(new Set([form.timezone ?? 'UTC', ...COMMON_TIMEZONES])).map((tz) => (
                  <SelectItem key={tz} value={tz}>{tz}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        {form.frequency === 'custom' ? (
          <div className="space-y-1.5">
            <Label className="text-sm">{t('cronExpression')}</Label>
            <Input
              placeholder="0 7 * * 1-5"
              className="font-mono"
              value={form.cron_expression ?? ''}
              onChange={(e) => set('cron_expression', e.target.value)}
            />
            <p className="text-xs text-muted-foreground">{t('cronHint')}</p>
          </div>
        ) : (
          <div className="grid grid-cols-3 gap-3">
            {form.frequency !== 'hourly' && (
              <div className="space-y-1.5">
                <Label className="text-sm">{t('hour')}</Label>
                <Input
                  type="number" min={0} max={23}
                  value={form.hour ?? 7}
                  onChange={(e) => set('hour', Number(e.target.value))}
                />
              </div>
            )}
            <div className="space-y-1.5">
              <Label className="text-sm">{t('minute')}</Label>
              <Input
                type="number" min={0} max={59}
                value={form.minute ?? 0}
                onChange={(e) => set('minute', Number(e.target.value))}
              />
            </div>
            {form.frequency === 'weekly' && (
              <div className="space-y-1.5">
                <Label className="text-sm">{t('dayOfWeek')}</Label>
                <Select
                  value={String(form.day_of_week ?? 0)}
                  onValueChange={(v) => set('day_of_week', Number(v))}
                >
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {WEEKDAYS.map((d) => (
                      <SelectItem key={d} value={String(d)}>{t(`weekday.${d}`)}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}
            {form.frequency === 'monthly' && (
              <div className="space-y-1.5">
                <Label className="text-sm">{t('dayOfMonth')}</Label>
                <Input
                  type="number" min={1} max={28}
                  value={form.day_of_month ?? 1}
                  onChange={(e) => set('day_of_month', Number(e.target.value))}
                />
                <p className="text-xs text-muted-foreground">{t('dayOfMonthHint')}</p>
              </div>
            )}
          </div>
        )}
      </fieldset>

      {/* ── What to look for ─────────────────────────────────────────── */}
      <fieldset className="space-y-3 rounded-lg border border-border p-3">
        <legend className="px-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          {t('whatToLookFor')}
        </legend>

        <div className="space-y-1.5">
          <Label className="text-sm">{t('analyses')}</Label>
          <div className="flex flex-wrap gap-1.5">
            {ANALYSES.map((a) => {
              const on = form.analyses.includes(a)
              return (
                <button
                  key={a}
                  onClick={() => set('analyses', on
                    ? form.analyses.filter((x) => x !== a)
                    : [...form.analyses, a])}
                  className={cn(
                    'rounded-md px-2.5 py-1 text-xs ring-1 ring-inset transition-colors',
                    on ? 'bg-primary/12 text-primary ring-primary/25'
                       : 'bg-muted/30 text-muted-foreground ring-border hover:bg-muted/50'
                  )}
                >
                  {t(`analysis.${a}`)}
                </button>
              )
            })}
          </div>
        </div>

        <div className="grid grid-cols-3 gap-3">
          <div className="space-y-1.5">
            <Label className="text-sm">{t('minSeverity')}</Label>
            <Select
              value={form.min_severity}
              onValueChange={(v) => set('min_severity', v as Severity)}
            >
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                {SEVERITIES.map((s) => (
                  <SelectItem key={s} value={s}>{t(`severity.${s}`)}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label className="text-sm">{t('cooldown')}</Label>
            <Input
              type="number" min={0} max={720}
              value={form.cooldown_hours ?? 24}
              onChange={(e) => set('cooldown_hours', Number(e.target.value))}
            />
          </div>
          <div className="space-y-1.5">
            <Label className="text-sm">{t('maxAlerts')}</Label>
            <Input
              type="number" min={1} max={25}
              value={form.max_alerts_per_run ?? 5}
              onChange={(e) => set('max_alerts_per_run', Number(e.target.value))}
            />
          </div>
        </div>
        <p className="text-xs leading-snug text-muted-foreground">{t('cooldownHint')}</p>

        <div className="space-y-2">
          <Label className="text-sm">{t('targets')}</Label>
          <p className="text-xs leading-snug text-muted-foreground">{t('targetsHint')}</p>
          {(['revenue', 'gross_margin_pct', 'repeat_rate_pct', 'orders'] as const).map((metric) => (
            <div key={metric} className="grid grid-cols-[1fr_auto_auto] items-center gap-2">
              <span className="text-sm">{t(`metric.${metric}`)}</span>
              <Input
                className="w-28" placeholder={t('minPlaceholder')}
                defaultValue={targets[metric]?.min ?? ''}
                onBlur={(e) => setTarget(metric, 'min', e.target.value)}
              />
              <Input
                className="w-28" placeholder={t('maxPlaceholder')}
                defaultValue={targets[metric]?.max ?? ''}
                onBlur={(e) => setTarget(metric, 'max', e.target.value)}
              />
            </div>
          ))}
        </div>
      </fieldset>

      {/* ── Where ────────────────────────────────────────────────────── */}
      <fieldset className="space-y-3 rounded-lg border border-border p-3">
        <legend className="px-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          {t('where')}
        </legend>

        <p className="text-xs leading-snug text-muted-foreground">{t('whereHint')}</p>

        {channels.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t('noChannelsYet')}</p>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {channels.map((c) => {
              const on = form.channel_ids.includes(c.id)
              const Icon = (Icons as any)[CHANNEL_ICON[c.type] ?? 'Bell'] ?? Icons.Bell
              // in_app needs no credentials and cannot fail to deliver, so the
              // untested warning would be noise there.
              const untested = !c.verified_at && c.type !== 'in_app'
              return (
                <button
                  key={c.id}
                  onClick={() => set('channel_ids', on
                    ? form.channel_ids.filter((x) => x !== c.id)
                    : [...form.channel_ids, c.id])}
                  className={cn(
                    'flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs ring-1 ring-inset transition-colors',
                    on ? 'bg-primary/12 text-primary ring-primary/25'
                       : 'bg-muted/30 text-muted-foreground ring-border hover:bg-muted/50'
                  )}
                >
                  <Icon className="size-3" /> {c.name}
                  {c.last_error
                    ? <Icons.TriangleAlert className="size-3 text-destructive" />
                    : untested && <Icons.CircleHelp className="size-3 opacity-60" />}
                </button>
              )
            })}
          </div>
        )}

        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" variant="outline" onClick={() => setAddingChannel(true)}>
            <Icons.Plus className="size-3.5" /> {t('addChannelInline')}
          </Button>
          {form.channel_ids.some((id) => {
            const c = channels.find((x) => x.id === id)
            return c && !c.verified_at && c.type !== 'in_app'
          }) && (
            <p className="text-xs leading-snug text-warning">{t('verifyHint')}</p>
          )}
        </div>

        {/* Nested inside the schedule dialog on purpose: the half-filled form
            stays mounted behind it, so nothing typed so far is lost. */}
        <Dialog open={addingChannel} onOpenChange={setAddingChannel}>
          <DialogContent className="max-h-[85vh] max-w-lg overflow-y-auto">
            <DialogHeader><DialogTitle>{t('addChannel')}</DialogTitle></DialogHeader>
            <ChannelEditor
              types={channelTypes}
              t={t}
              onDone={(created) => {
                setAddingChannel(false)
                // Selecting it is the whole point of adding it here.
                if (created) set('channel_ids', [...form.channel_ids, created.id])
              }}
            />
          </DialogContent>
        </Dialog>

        <div className="grid grid-cols-3 gap-3">
          <div className="space-y-1.5">
            <Label className="text-sm">{t('language')}</Label>
            <Select value={form.language} onValueChange={(v) => set('language', v)}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="en">English</SelectItem>
                <SelectItem value="ar">العربية</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label className="text-sm">{t('quietFrom')}</Label>
            <Input
              type="number" min={0} max={23} placeholder="—"
              value={form.quiet_hours_start ?? ''}
              onChange={(e) => set('quiet_hours_start',
                e.target.value === '' ? null : Number(e.target.value))}
            />
          </div>
          <div className="space-y-1.5">
            <Label className="text-sm">{t('quietTo')}</Label>
            <Input
              type="number" min={0} max={23} placeholder="—"
              value={form.quiet_hours_end ?? ''}
              onChange={(e) => set('quiet_hours_end',
                e.target.value === '' ? null : Number(e.target.value))}
            />
          </div>
        </div>
        <p className="text-xs leading-snug text-muted-foreground">{t('quietHoursHint')}</p>

        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <Label className="text-sm">{t('sendWhenQuiet')}</Label>
            <p className="mt-0.5 text-xs leading-snug text-muted-foreground">
              {t('sendWhenQuietHint')}
            </p>
          </div>
          <Switch
            checked={form.send_when_nothing_found ?? false}
            onCheckedChange={(v) => set('send_when_nothing_found', v)}
          />
        </div>
      </fieldset>

      {error && <p className="text-sm text-destructive">{error.message}</p>}

      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={onDone}>{t('cancel')}</Button>
        <Button onClick={submit} disabled={pending}>
          {pending && <Icons.Loader2 className="size-3.5 animate-spin" />}
          {initial ? t('saveChanges') : t('createSchedule')}
        </Button>
      </div>
    </div>
  )
}

function ScheduleCard({
  schedule, onEdit, t,
}: {
  schedule: Schedule
  onEdit: () => void
  t: ReturnType<typeof useTranslations>
}) {
  const update = useUpdateSchedule()
  const remove = useDeleteSchedule()
  const runNow = useRunScheduleNow()
  const preview = usePreviewSchedule()
  const [previewOpen, setPreviewOpen] = React.useState(false)

  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="text-md font-semibold">{schedule.name}</h3>
            <Badge variant="outline">{schedule.project_name}</Badge>
            {schedule.last_status === 'failed' && (
              <Badge variant="danger">{t('lastRunFailed')}</Badge>
            )}
          </div>
          <p className="mt-1 text-sm text-muted-foreground">{schedule.description}</p>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {t('nextRun')}: {fmtDate(schedule.next_run_at)}
            {schedule.last_run_at && ` · ${t('lastRun')}: ${fmtDate(schedule.last_run_at)}`}
          </p>
        </div>
        <Switch
          checked={schedule.is_active}
          onCheckedChange={(v) => update.mutate({ id: schedule.id, is_active: v })}
        />
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        <Badge variant="outline">{t(`severity.${schedule.min_severity}`)}+</Badge>
        <Badge variant="outline">{t('cooldownBadge', { hours: schedule.cooldown_hours })}</Badge>
        <Badge variant="outline">{schedule.language === 'ar' ? 'العربية' : 'English'}</Badge>
        {schedule.analyses.map((a) => (
          <Badge key={a} variant="brand">{t(`analysis.${a}`)}</Badge>
        ))}
        {schedule.channel_ids.length === 0 && (
          <Badge variant="warning">{t('inAppOnly')}</Badge>
        )}
      </div>

      <div className="mt-3 flex flex-wrap gap-2">
        <Button size="sm" variant="outline" onClick={onEdit}>
          <Icons.Pencil className="size-3.5" /> {t('edit')}
        </Button>
        <Button
          size="sm" variant="outline"
          onClick={() => { setPreviewOpen(true); preview.mutate(schedule.id) }}
        >
          <Icons.Eye className="size-3.5" /> {t('preview')}
        </Button>
        <Button
          size="sm" variant="outline"
          onClick={() => runNow.mutate(schedule.id)}
          disabled={runNow.isPending}
        >
          {runNow.isPending
            ? <Icons.Loader2 className="size-3.5 animate-spin" />
            : <Icons.Play className="size-3.5" />}
          {t('runNow')}
        </Button>
        <Button
          size="sm" variant="ghost"
          className="text-destructive hover:text-destructive"
          onClick={() => remove.mutate(schedule.id)}
        >
          <Icons.Trash2 className="size-3.5" /> {t('delete')}
        </Button>
      </div>

      {runNow.isError && (
        <p className="mt-2 text-sm text-destructive">{(runNow.error as Error).message}</p>
      )}
      {runNow.isSuccess && (
        <p className="mt-2 text-sm text-muted-foreground">
          {t('runFinished', {
            found: runNow.data.alerts_found,
            delivered: runNow.data.alerts_delivered,
          })}
        </p>
      )}

      <Dialog open={previewOpen} onOpenChange={setPreviewOpen}>
        <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
          <DialogHeader><DialogTitle>{t('previewTitle')}</DialogTitle></DialogHeader>
          {preview.isPending && <LoadingState label={t('previewRunning')} />}
          {preview.data && !preview.data.available && (
            <p className="text-sm text-muted-foreground">{preview.data.reason}</p>
          )}
          {preview.data?.available && (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant={preview.data.would_deliver ? 'success' : 'default'}>
                  {preview.data.would_deliver ? t('wouldSend') : t('wouldNotSend')}
                </Badge>
                {preview.data.money_at_stake > 0 && (
                  <Badge variant="warning">
                    {t('atStake', { amount: fmtMoney(preview.data.money_at_stake) })}
                  </Badge>
                )}
                {preview.data.suppressed.length > 0 && (
                  <Badge variant="outline">
                    {t('suppressedCount', { n: preview.data.suppressed.length })}
                  </Badge>
                )}
              </div>
              <div
                dir={schedule.language === 'ar' ? 'rtl' : 'ltr'}
                className="rounded-lg border border-border bg-muted/20 p-4"
              >
                <div className="prose prose-sm dark:prose-invert max-w-none text-base">
                  <ReactMarkdown>{preview.data.briefing_md}</ReactMarkdown>
                </div>
              </div>
              <p className="text-xs text-muted-foreground">{t('previewNote')}</p>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </Card>
  )
}

// ── Channels ───────────────────────────────────────────────────────────────

function ChannelEditor({
  types, onDone, t, initialType,
}: {
  types: ChannelTypeInfo[]
  /** Receives the created channel so a caller that opened this mid-flow — the
   *  schedule editor — can select it immediately instead of making the user
   *  find it again in a list. */
  onDone: (created?: Channel) => void
  t: ReturnType<typeof useTranslations>
  initialType?: string
}) {
  const create = useCreateChannel()
  const [type, setType] = React.useState(initialType ?? 'telegram')
  const [name, setName] = React.useState('')
  const [destination, setDestination] = React.useState('')
  const [credentials, setCredentials] = React.useState<Record<string, string>>({})
  const [config, setConfig] = React.useState<Record<string, string>>({})

  const info = types.find((x) => x.type === type)
  const provider = config.provider ?? 'meta'
  // WhatsApp's two backends need different credentials; showing both sets at
  // once is how someone fills in the Twilio SID for a Meta channel.
  const relevant = (fields: { provider?: string }[]) =>
    fields.filter((f) => !f.provider || f.provider === provider)

  return (
    <div className="space-y-4">
      <div className="space-y-1.5">
        <Label className="text-sm">{t('channelType')}</Label>
        <Select value={type} onValueChange={(v) => { setType(v); setCredentials({}); setConfig({}) }}>
          <SelectTrigger><SelectValue /></SelectTrigger>
          <SelectContent>
            {types.map((x) => <SelectItem key={x.type} value={x.type}>{x.label}</SelectItem>)}
          </SelectContent>
        </Select>
        {info && <p className="text-xs leading-snug text-muted-foreground">{info.description}</p>}
      </div>

      <div className="space-y-1.5">
        <Label className="text-sm">{t('channelName')}</Label>
        <Input value={name} onChange={(e) => setName(e.target.value)} placeholder={info?.label} />
      </div>

      {info?.needs_destination && (
        <div className="space-y-1.5">
          <Label className="text-sm">{info.destination_label}</Label>
          <Input
            value={destination}
            onChange={(e) => setDestination(e.target.value)}
            placeholder={info.destination_placeholder}
          />
        </div>
      )}

      {info && relevant(info.config_fields).map((f: any) => (
        <div key={f.key} className="space-y-1.5">
          <Label className="text-sm">{f.label}</Label>
          {f.options ? (
            <Select
              value={config[f.key] ?? f.options[0]}
              onValueChange={(v) => setConfig((c) => ({ ...c, [f.key]: v }))}
            >
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                {f.options.map((o: string) => <SelectItem key={o} value={o}>{o}</SelectItem>)}
              </SelectContent>
            </Select>
          ) : (
            <Input
              value={config[f.key] ?? ''}
              placeholder={f.placeholder}
              onChange={(e) => setConfig((c) => ({ ...c, [f.key]: e.target.value }))}
            />
          )}
        </div>
      ))}

      {info && relevant(info.secret_fields).map((f: any) => (
        <div key={f.key} className="space-y-1.5">
          <Label className="text-sm">{f.label}</Label>
          <Input
            type="password"
            autoComplete="new-password"
            value={credentials[f.key] ?? ''}
            placeholder={f.placeholder}
            onChange={(e) => setCredentials((c) => ({ ...c, [f.key]: e.target.value }))}
          />
        </div>
      ))}

      {info && (info.secret_fields.length > 0) && (
        <p className="text-xs leading-snug text-muted-foreground">{t('secretsNote')}</p>
      )}

      {create.isError && (
        <p className="text-sm text-destructive">{(create.error as Error).message}</p>
      )}

      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={() => onDone()}>{t('cancel')}</Button>
        <Button
          disabled={create.isPending}
          onClick={() => create.mutate(
            { type, name: name || info?.label || type, destination, credentials, config },
            { onSuccess: (created) => onDone(created) },
          )}
        >
          {create.isPending && <Icons.Loader2 className="size-3.5 animate-spin" />}
          {t('addChannel')}
        </Button>
      </div>
    </div>
  )
}

function ChannelCard({ channel, t }: { channel: Channel; t: ReturnType<typeof useTranslations> }) {
  const test = useTestChannel()
  const remove = useDeleteChannel()
  const update = useUpdateChannel()
  const Icon = (Icons as any)[CHANNEL_ICON[channel.type] ?? 'Bell'] ?? Icons.Bell

  return (
    <Card className="p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary ring-1 ring-inset ring-primary/20">
            <Icon className="size-4" />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h3 className="text-base font-semibold">{channel.name}</h3>
              {channel.verified_at && (
                <Badge variant="success">
                  <Icons.Check className="size-3" /> {t('verified')}
                </Badge>
              )}
              {channel.last_error && <Badge variant="danger">{t('channelError')}</Badge>}
            </div>
            {channel.destination && (
              <p className="mt-0.5 truncate font-mono text-xs text-muted-foreground">
                {channel.destination}
              </p>
            )}
            {channel.last_error && (
              <p className="mt-1 text-xs leading-snug text-destructive">{channel.last_error}</p>
            )}
          </div>
        </div>
        <Switch
          checked={channel.is_active}
          onCheckedChange={(v) => update.mutate({ id: channel.id, is_active: v })}
        />
      </div>

      <div className="mt-3 flex gap-2">
        <Button size="sm" variant="outline" onClick={() => test.mutate(channel.id)} disabled={test.isPending}>
          {test.isPending
            ? <Icons.Loader2 className="size-3.5 animate-spin" />
            : <Icons.Send className="size-3.5" />}
          {t('sendTest')}
        </Button>
        <Button
          size="sm" variant="ghost"
          className="text-destructive hover:text-destructive"
          onClick={() => remove.mutate(channel.id)}
        >
          <Icons.Trash2 className="size-3.5" /> {t('delete')}
        </Button>
      </div>

      {test.data && (
        <p className={cn('mt-2 text-sm', test.data.ok ? 'text-success' : 'text-destructive')}>
          {test.data.ok ? t('testSent') : test.data.error}
        </p>
      )}
    </Card>
  )
}

// ── Alerts ─────────────────────────────────────────────────────────────────

function AlertRow({ alert, t }: { alert: Alert; t: ReturnType<typeof useTranslations> }) {
  const update = useUpdateAlert()
  const [open, setOpen] = React.useState(false)
  const rc = alert.root_cause as any

  return (
    <Card className={cn('p-4', alert.status === 'new' && 'border-primary/25')}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant={SEVERITY_VARIANT[alert.severity]}>{t(`severity.${alert.severity}`)}</Badge>
            <Badge variant="outline">{alert.project_name}</Badge>
            {alert.status === 'new' && <span className="size-1.5 rounded-full bg-primary" />}
          </div>
          <h3 className="mt-1.5 text-base font-semibold leading-snug">{alert.title}</h3>
          <p className="mt-1 text-xs text-muted-foreground">
            {fmtDate(alert.created_at)}
            {alert.money_at_stake > 0 && ` · ${t('atStake', { amount: fmtMoney(alert.money_at_stake) })}`}
          </p>
        </div>
        <div className="flex shrink-0 gap-1.5">
          <Button size="sm" variant="ghost" onClick={() => setOpen((v) => !v)}>
            <Icons.ChevronDown className={cn('size-3.5 transition-transform', open && 'rotate-180')} />
          </Button>
          {alert.status === 'new' && (
            <Button
              size="sm" variant="outline"
              onClick={() => update.mutate({ id: alert.id, status: 'acknowledged' })}
            >
              {t('acknowledge')}
            </Button>
          )}
          {alert.status !== 'resolved' && (
            <Button
              size="sm" variant="ghost"
              onClick={() => update.mutate({ id: alert.id, status: 'resolved' })}
            >
              <Icons.Check className="size-3.5" />
            </Button>
          )}
        </div>
      </div>

      {open && (
        <div className="mt-3 space-y-3 border-t border-border pt-3">
          <p className="text-sm leading-relaxed">{alert.body_md}</p>
          {rc?.available && rc.explanations?.length > 0 && (
            <div className="rounded-lg border border-border bg-muted/20 p-3">
              <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                {t('rootCauseAttached')}
              </p>
              <p className="mt-1.5 text-sm">
                <strong>{rc.explanations[0].slice_label}</strong>
                {' — '}
                {t('explainsFromRows', {
                  share: Math.abs(rc.explanations[0].explanatory_power_pct).toFixed(1),
                  rows: rc.explanations[0].rows_share_pct.toFixed(1),
                })}
              </p>
              {rc.narrative && (
                <div className="prose prose-sm dark:prose-invert mt-2 max-w-none text-sm">
                  <ReactMarkdown>{rc.narrative}</ReactMarkdown>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </Card>
  )
}

// ── Section ────────────────────────────────────────────────────────────────

export function Monitoring() {
  const t = useTranslations('monitoring')
  const projectId = useAppStore((s) => s.activeProjectId)
  const schedules = useSchedules()
  const channels = useChannels()
  const channelTypes = useChannelTypes()
  const runs = useRuns()
  const alerts = useAlerts()
  const markAllRead = useMarkAllAlertsRead()

  const [editing, setEditing] = React.useState<Schedule | null | 'new'>(null)
  const [addingChannel, setAddingChannel] = React.useState(false)
  const [openRunId, setOpenRunId] = React.useState<string | null>(null)
  const runDetail = useRun(openRunId)

  const unread = (alerts.data ?? []).filter((a) => a.status === 'new').length

  return (
    <SectionScroll>
      <SectionHeader
        title={t('title')}
        description={t('description')}
        icon={<Icons.RadioTower className="size-4.5" />}
        actions={
          <Button size="sm" onClick={() => setEditing('new')} disabled={!projectId}>
            <Icons.Plus className="size-3.5" /> {t('newSchedule')}
          </Button>
        }
      />

      <div className="mb-4"><SchedulerBanner t={t} /></div>

      <Tabs defaultValue="alerts">
        <TabsList>
          <TabsTrigger value="alerts">
            {t('tabAlerts')}{unread > 0 && ` (${unread})`}
          </TabsTrigger>
          <TabsTrigger value="schedules">
            {t('tabSchedules')} ({schedules.data?.length ?? 0})
          </TabsTrigger>
          <TabsTrigger value="channels">
            {t('tabChannels')} ({channels.data?.length ?? 0})
          </TabsTrigger>
          <TabsTrigger value="runs">{t('tabRuns')}</TabsTrigger>
        </TabsList>

        {/* ── Alerts ───────────────────────────────────────────────── */}
        <TabsContent value="alerts" className="mt-4 space-y-3">
          {unread > 0 && (
            <div className="flex justify-end">
              <Button size="sm" variant="outline" onClick={() => markAllRead.mutate()}>
                <Icons.CheckCheck className="size-3.5" /> {t('markAllRead')}
              </Button>
            </div>
          )}
          {alerts.isLoading && <LoadingState />}
          {alerts.isError && <ErrorState body={(alerts.error as Error).message} />}
          {alerts.data?.length === 0 && (
            <EmptyState icon="BellOff" title={t('noAlerts')} description={t('noAlertsHint')} />
          )}
          {alerts.data?.map((a) => <AlertRow key={a.id} alert={a} t={t} />)}
        </TabsContent>

        {/* ── Schedules ────────────────────────────────────────────── */}
        <TabsContent value="schedules" className="mt-4 space-y-3">
          {schedules.isLoading && <LoadingState />}
          {schedules.data?.length === 0 && (
            <EmptyState
              icon="CalendarClock"
              title={t('noSchedules')}
              description={t('noSchedulesHint')}
              actionLabel={projectId ? t('newSchedule') : undefined}
              onAction={() => setEditing('new')}
            />
          )}
          {schedules.data?.map((s) => (
            <ScheduleCard key={s.id} schedule={s} onEdit={() => setEditing(s)} t={t} />
          ))}
        </TabsContent>

        {/* ── Channels ─────────────────────────────────────────────── */}
        <TabsContent value="channels" className="mt-4 space-y-3">
          <div className="flex justify-end">
            <Button size="sm" variant="outline" onClick={() => setAddingChannel(true)}>
              <Icons.Plus className="size-3.5" /> {t('addChannel')}
            </Button>
          </div>
          {channels.data?.length === 0 && (
            <EmptyState icon="Send" title={t('noChannels')} description={t('noChannelsHint')} />
          )}
          {channels.data?.map((c) => <ChannelCard key={c.id} channel={c} t={t} />)}
        </TabsContent>

        {/* ── Runs ─────────────────────────────────────────────────── */}
        <TabsContent value="runs" className="mt-4 space-y-2">
          {runs.isLoading && <LoadingState />}
          {runs.data?.length === 0 && (
            <EmptyState icon="History" title={t('noRuns')} description={t('noRunsHint')} />
          )}
          {runs.data?.map((r) => (
            <Card
              key={r.id}
              className="cursor-pointer p-3 transition-colors hover:bg-accent/30"
              onClick={() => setOpenRunId(r.id)}
            >
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant={
                  r.status === 'success' ? 'success' : r.status === 'failed' ? 'danger' : 'default'
                }>
                  {t(`runStatus.${r.status}`)}
                </Badge>
                <span className="text-sm font-medium">{r.schedule_name}</span>
                <span className="text-xs text-muted-foreground">{r.project_name}</span>
                {r.trigger === 'manual' && <Badge variant="outline">{t('manual')}</Badge>}
                <span className="ms-auto text-xs text-muted-foreground">
                  {fmtDate(r.started_at)}
                  {r.duration_ms !== null && ` · ${(r.duration_ms / 1000).toFixed(1)}s`}
                </span>
              </div>
              <p className="mt-1 text-xs text-muted-foreground">
                {t('runSummary', { found: r.alerts_found, delivered: r.alerts_delivered })}
                {r.money_at_stake > 0 && ` · ${t('atStake', { amount: fmtMoney(r.money_at_stake) })}`}
              </p>
              {r.error && <p className="mt-1 text-xs text-destructive">{r.error}</p>}
            </Card>
          ))}
        </TabsContent>
      </Tabs>

      {/* Schedule editor */}
      <Dialog open={editing !== null} onOpenChange={(o) => !o && setEditing(null)}>
        <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editing === 'new' ? t('newSchedule') : t('editSchedule')}</DialogTitle>
          </DialogHeader>
          {editing !== null && (
            <ScheduleEditor
              initial={editing === 'new' ? null : editing}
              channels={channels.data ?? []}
              channelTypes={channelTypes.data ?? []}
              projectId={editing === 'new' ? projectId : (editing as Schedule).project_id}
              onDone={() => setEditing(null)}
              t={t}
            />
          )}
        </DialogContent>
      </Dialog>

      {/* Channel editor */}
      <Dialog open={addingChannel} onOpenChange={setAddingChannel}>
        <DialogContent className="max-h-[85vh] max-w-lg overflow-y-auto">
          <DialogHeader><DialogTitle>{t('addChannel')}</DialogTitle></DialogHeader>
          <ChannelEditor
            types={channelTypes.data ?? []}
            onDone={() => setAddingChannel(false)}
            t={t}
          />
        </DialogContent>
      </Dialog>

      {/* Run detail */}
      <Dialog open={openRunId !== null} onOpenChange={(o) => !o && setOpenRunId(null)}>
        <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
          <DialogHeader><DialogTitle>{t('runDetail')}</DialogTitle></DialogHeader>
          {runDetail.isLoading && <LoadingState />}
          {runDetail.data && (
            <div className="space-y-4">
              {runDetail.data.deliveries.length > 0 && (
                <div className="space-y-1.5">
                  <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                    {t('delivery')}
                  </p>
                  {runDetail.data.deliveries.map((d) => (
                    <div key={d.id} className="flex items-center gap-2 text-sm">
                      <Badge variant={d.status === 'sent' ? 'success' : d.status === 'failed' ? 'danger' : 'default'}>
                        {d.channel_type}
                      </Badge>
                      <span className="text-muted-foreground">{d.destination || '—'}</span>
                      {d.error && <span className="text-destructive">{d.error}</span>}
                    </div>
                  ))}
                </div>
              )}
              {runDetail.data.briefing_md && (
                <div className="rounded-lg border border-border bg-muted/20 p-4">
                  <div className="prose prose-sm dark:prose-invert max-w-none text-base">
                    <ReactMarkdown>{runDetail.data.briefing_md}</ReactMarkdown>
                  </div>
                </div>
              )}
              {(runDetail.data.detail?.findings_suppressed ?? 0) > 0 && (
                <p className="text-xs text-muted-foreground">
                  {t('suppressedInRun', { n: runDetail.data.detail.findings_suppressed })}
                </p>
              )}
            </div>
          )}
        </DialogContent>
      </Dialog>
    </SectionScroll>
  )
}
