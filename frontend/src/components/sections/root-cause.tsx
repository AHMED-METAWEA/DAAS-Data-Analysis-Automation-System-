'use client'

import * as React from 'react'
import ReactMarkdown from 'react-markdown'
import { useLocale, useTranslations } from 'next-intl'
import { cn } from '@/lib/utils'
import { useAppStore } from '@/lib/store'
import {
  useRootCauseOptions,
  useRunRootCause,
  type RcExplanation,
  type RootCauseResult,
} from '@/lib/queries/rootcause'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Slider } from '@/components/ui/slider'
import { Input } from '@/components/ui/input'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  Accordion, AccordionContent, AccordionItem, AccordionTrigger,
} from '@/components/ui/accordion'
import {
  Tooltip, TooltipContent, TooltipProvider, TooltipTrigger,
} from '@/components/ui/tooltip'
import { SectionScroll, SectionHeader } from '@/components/shared/layout'
import { EmptyState, ErrorState, LoadingState } from '@/components/shared/states'
import { SaveReportButton } from '@/components/shared/save-report-button'
import * as Icons from 'lucide-react'

function num(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return value.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

function signed(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${value > 0 ? '+' : ''}${num(value, digits)}`
}

/** The headline number: how much of the change this slice carries, per unit of
 * its own size. "23×" is the sentence "it did 23 times its share of the damage". */
function LiftBadge({ value }: { value: number }) {
  const variant = value >= 8 ? 'danger' : value >= 3 ? 'warning' : 'default'
  return <Badge variant={variant}>{num(value, 1)}×</Badge>
}

function ExplanationCard({
  explanation,
  correctedThreshold,
  t,
}: {
  explanation: RcExplanation
  correctedThreshold: number
  t: ReturnType<typeof useTranslations>
}) {
  const e = explanation
  const share = Math.min(Math.abs(e.explanatory_power_pct), 100)
  const falling = e.direction === 'decline'

  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs font-mono text-muted-foreground">#{e.rank}</span>
            <h3 className="text-md font-semibold tracking-tight">{e.slice_label}</h3>
            {e.robust === true && (
              <Badge variant="success">
                <Icons.ShieldCheck className="size-3" /> {t('robust')}
              </Badge>
            )}
            {e.robust === false && (
              <TooltipProvider delayDuration={150}>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <span>
                      <Badge variant="warning">
                        <Icons.FlaskConical className="size-3" /> {t('lead')}
                      </Badge>
                    </span>
                  </TooltipTrigger>
                  <TooltipContent className="max-w-[280px]">
                    {t('leadTooltip', { z: num(correctedThreshold, 1) })}
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
            )}
          </div>
          <p className="mt-1 text-xs font-mono text-muted-foreground">
            {e.slice_expression}
          </p>
        </div>
        <div className="text-end shrink-0">
          <p className={cn('text-xl font-semibold tabular-nums leading-none',
            falling ? 'text-destructive' : 'text-success')}>
            {signed(e.delta, 2)}
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            {num(e.prior_value)} → {num(e.current_value)}
          </p>
        </div>
      </div>

      {/* Share of the change, against the share of the business it is */}
      <div className="mt-4 space-y-1.5">
        <div className="flex items-center justify-between text-xs">
          <span className="text-muted-foreground">
            {t('explainsShare', { pct: num(Math.abs(e.explanatory_power_pct), 1) })}
          </span>
          <span className="text-muted-foreground">
            {t('fromRows', { pct: num(e.rows_share_pct, 1) })}
          </span>
        </div>
        <div className="relative h-2 w-full overflow-hidden rounded-full bg-muted">
          <div
            className={cn('absolute inset-y-0 start-0 rounded-full',
              falling ? 'bg-destructive/70' : 'bg-success/70')}
            style={{ width: `${share}%` }}
          />
          {/* The slice's own footprint, drawn on the same axis — the gap between
              the two bars IS the finding. */}
          <div
            className="absolute inset-y-0 start-0 rounded-full bg-foreground/25"
            style={{ width: `${Math.min(e.rows_share_pct, 100)}%` }}
          />
        </div>
      </div>

      <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Metric label={t('lift')} value={<LiftBadge value={e.concentration} />} hint={t('liftHint')} />
        <Metric
          label={t('vsExpected')}
          value={<span className="tabular-nums">{signed(e.excess, 0)}</span>}
          hint={t('vsExpectedHint', { expected: num(e.expected_current, 0) })}
        />
        <Metric
          label={t('restOfBusiness')}
          value={
            <span className={cn('tabular-nums',
              Math.abs(e.rest_change_pct ?? 0) < 2 ? 'text-muted-foreground' : '')}>
              {e.rest_change_pct === null ? '—' : `${signed(e.rest_change_pct, 1)}%`}
            </span>
          }
          hint={t('restOfBusinessHint')}
        />
        <Metric
          label={t('signal')}
          value={
            <span className="tabular-nums">
              {e.signal_to_noise === null ? '—' : `${num(Math.min(e.signal_to_noise, 99), 1)}σ`}
            </span>
          }
          hint={t('signalHint')}
        />
      </div>

      {e.bridge?.available && (
        <div className="mt-4 rounded-lg border border-border bg-muted/20 p-3">
          <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            {t('withinSlice')}
          </p>
          <p className="mt-1.5 text-sm leading-relaxed">
            {t('bridgeSentence', {
              driver: e.bridge.primary_driver === 'order count'
                ? t('driverOrderCount') : t('driverOrderSize'),
              volume: num(e.bridge.volume_effect ?? 0, 0),
              basket: num(e.bridge.basket_effect ?? 0, 0),
              orders: signed(e.bridge.orders_change ?? 0, 0),
            })}
          </p>
        </div>
      )}
    </Card>
  )
}

function Metric({ label, value, hint }: { label: string; value: React.ReactNode; hint?: string }) {
  return (
    <TooltipProvider delayDuration={200}>
      <Tooltip>
        <TooltipTrigger asChild>
          <div className="cursor-default">
            <p className="text-2xs uppercase tracking-wider text-muted-foreground">{label}</p>
            <div className="mt-1 text-md font-semibold">{value}</div>
          </div>
        </TooltipTrigger>
        {hint && <TooltipContent className="max-w-[260px]">{hint}</TooltipContent>}
      </Tooltip>
    </TooltipProvider>
  )
}

/** The refinement chain: how each added condition sharpened the explanation.
 * Showing the search narrow is what makes the conclusion arguable instead of
 * oracular. */
function DrillPath({ result, t }: { result: RootCauseResult; t: ReturnType<typeof useTranslations> }) {
  if (!result.drill_path.length) return null
  return (
    <Card className="p-4">
      <h3 className="text-base font-semibold">{t('drillPath')}</h3>
      <p className="mt-0.5 text-sm text-muted-foreground">{t('drillPathHint')}</p>
      <ol className="mt-3 space-y-2">
        {result.drill_path.map((step, i) => (
          <li key={i} className="flex items-center gap-3">
            <span className="flex size-6 shrink-0 items-center justify-center rounded-md bg-primary/10 text-xs font-semibold text-primary ring-1 ring-inset ring-primary/20">
              {i + 1}
            </span>
            <span className="min-w-0 flex-1 truncate font-mono text-sm">{step.added}</span>
            <span className="shrink-0 tabular-nums text-sm text-muted-foreground">
              {num(Math.abs(step.explanatory_power_pct), 1)}% / {num(step.rows_share_pct, 1)}%
            </span>
            <LiftBadge value={step.concentration} />
          </li>
        ))}
      </ol>
    </Card>
  )
}

/** Single-dimension view — "by product it takes 2 items to explain two thirds
 * of the drop; by region it takes 5". The justification for drilling. */
function DimensionTable({ result, t }: { result: RootCauseResult; t: ReturnType<typeof useTranslations> }) {
  if (!result.per_dimension.length) return null
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">{t('perDimensionHint')}</p>
      <Accordion type="single" collapsible className="space-y-2">
        {result.per_dimension.map((dim) => (
          <AccordionItem
            key={dim.dimension}
            value={dim.dimension}
            className="rounded-lg border border-border bg-card px-3"
          >
            <AccordionTrigger className="py-3 hover:no-underline">
              <div className="flex flex-1 flex-wrap items-center gap-2 pe-3 text-start">
                <span className="font-mono text-sm font-medium">{dim.dimension}</span>
                {dim.role && <Badge variant="outline">{dim.role}</Badge>}
                <span className="ms-auto text-xs text-muted-foreground">
                  {dim.elements_for_two_thirds === null
                    ? t('noElements')
                    : t('elementsForTwoThirds', { n: dim.elements_for_two_thirds })}
                </span>
              </div>
            </AccordionTrigger>
            <AccordionContent className="pb-3">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-2xs uppercase tracking-wider text-muted-foreground">
                    <th className="pb-1.5 text-start font-medium">{t('value')}</th>
                    <th className="pb-1.5 text-end font-medium">{t('prior')}</th>
                    <th className="pb-1.5 text-end font-medium">{t('current')}</th>
                    <th className="pb-1.5 text-end font-medium">{t('change')}</th>
                    <th className="pb-1.5 text-end font-medium">{t('shareOfChange')}</th>
                  </tr>
                </thead>
                <tbody className="tabular-nums">
                  {dim.top_elements.map((row) => (
                    <tr key={row.value} className="border-t border-border/60">
                      <td className="py-1.5 pe-2 font-medium">{row.value}</td>
                      <td className="py-1.5 text-end text-muted-foreground">{num(row.prior, 0)}</td>
                      <td className="py-1.5 text-end text-muted-foreground">{num(row.current, 0)}</td>
                      <td className={cn('py-1.5 text-end', row.delta < 0 ? 'text-destructive' : 'text-success')}>
                        {signed(row.delta, 0)}
                      </td>
                      <td className="py-1.5 text-end">{num(Math.abs(row.explanatory_power_pct), 1)}%</td>
                    </tr>
                  ))}
                  {dim.offsetting_elements.map((row) => (
                    <tr key={`off-${row.value}`} className="border-t border-border/60 opacity-60">
                      <td className="py-1.5 pe-2">
                        {row.value}
                        <Badge variant="outline" className="ms-2">{t('offsetting')}</Badge>
                      </td>
                      <td className="py-1.5 text-end text-muted-foreground">{num(row.prior, 0)}</td>
                      <td className="py-1.5 text-end text-muted-foreground">{num(row.current, 0)}</td>
                      <td className={cn('py-1.5 text-end', row.delta < 0 ? 'text-destructive' : 'text-success')}>
                        {signed(row.delta, 0)}
                      </td>
                      <td className="py-1.5 text-end">{num(Math.abs(row.explanatory_power_pct), 1)}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </AccordionContent>
          </AccordionItem>
        ))}
      </Accordion>
    </div>
  )
}

/** What the search actually did. Published rather than summarised away: a
 * drill-down that quietly stopped early would present a partial answer as a
 * complete one. */
function SearchStats({ result, t }: { result: RootCauseResult; t: ReturnType<typeof useTranslations> }) {
  const s = result.stats
  const rows: [string, string][] = [
    [t('statSlices'), `${s.nodes_evaluated.toLocaleString()} / ${s.exhaustive_combinations.toLocaleString()}`],
    [t('statReduction'), s.search_reduction ? `${num(s.search_reduction, 1)}×` : '—'],
    [t('statElapsed'), `${num(s.elapsed_seconds * 1000, 0)} ms`],
    [t('statTested'), s.slices_tested.toLocaleString()],
    [t('statThreshold'), `${num(s.corrected_threshold, 2)}σ`],
    [t('statPrunedSupport'), s.pruned_by_support.toLocaleString()],
    [t('statPrunedBound'), s.pruned_by_magnitude_bound.toLocaleString()],
    [t('statPrunedBeam'), s.pruned_by_beam.toLocaleString()],
    [t('statPrunedRedundant'), s.pruned_by_redundancy.toLocaleString()],
    [t('statDuplicates'), s.duplicate_paths.toLocaleString()],
  ]
  return (
    <Card className="p-4">
      <h3 className="text-base font-semibold">{t('searchStats')}</h3>
      <p className="mt-0.5 text-sm text-muted-foreground">{t('searchStatsHint')}</p>
      <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-3">
        {rows.map(([label, value]) => (
          <div key={label} className="flex items-baseline justify-between gap-2 border-b border-border/50 pb-1.5">
            <dt className="text-xs text-muted-foreground">{label}</dt>
            <dd className="shrink-0 tabular-nums text-sm font-medium">{value}</dd>
          </div>
        ))}
      </dl>
      {s.dimensions_skipped.length > 0 && (
        <div className="mt-4">
          <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            {t('notSearched')}
          </p>
          <ul className="mt-1.5 space-y-1">
            {s.dimensions_skipped.map((d) => (
              <li key={d.column} className="text-xs text-muted-foreground">
                <span className="font-mono text-foreground">{d.column}</span> — {d.reason}
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  )
}

export function RootCause() {
  const t = useTranslations('rootCause')
  const locale = useLocale()
  const projectId = useAppStore((s) => s.activeProjectId)

  const options = useRootCauseOptions(projectId)
  const run = useRunRootCause(projectId)

  // A "Why?" click on the Insights page lands here with a metric preselected,
  // so the drill-down opens on the question that was actually asked. Read once
  // at mount via getState() rather than subscribed: subscribing and then
  // clearing the value would set state from inside an effect, which cascades a
  // render on every visit to this page.
  const [measure, setMeasure] = React.useState(
    () => useAppStore.getState().pendingRootCauseMeasure ?? 'revenue'
  )
  const [windowMode, setWindowMode] = React.useState('auto')
  const [customDates, setCustomDates] = React.useState({
    current_start: '', current_end: '', prior_start: '', prior_end: '',
  })
  const [selectedDims, setSelectedDims] = React.useState<string[] | null>(null)
  const [includeWeekday, setIncludeWeekday] = React.useState(false)
  const [maxDepth, setMaxDepth] = React.useState(3)
  const [beamWidth, setBeamWidth] = React.useState(48)
  const [minEp, setMinEp] = React.useState(8)
  const [minSignal, setMinSignal] = React.useState(2)
  const [withNarrative, setWithNarrative] = React.useState(true)
  const [showAdvanced, setShowAdvanced] = React.useState(false)

  const dimensions = options.data?.dimensions ?? []
  const measures = options.data?.measures ?? []

  // Consuming the handoff is a write to an external store, not to local state,
  // so it belongs in an effect and triggers no cascade. Without it the metric
  // would be re-applied every time this section is opened.
  React.useEffect(() => {
    const store = useAppStore.getState()
    if (store.pendingRootCauseMeasure) store.setPendingRootCauseMeasure(null)
  }, [])

  const toggleDimension = (name: string) => {
    setSelectedDims((current) => {
      const base = current ?? dimensions.map((d) => d.name)
      return base.includes(name) ? base.filter((n) => n !== name) : [...base, name]
    })
  }
  const activeDims = selectedDims ?? dimensions.map((d) => d.name)

  const submit = () => {
    run.mutate({
      measure,
      window_mode: windowMode,
      ...(windowMode === 'custom' ? customDates : {}),
      dimensions: selectedDims && selectedDims.length !== dimensions.length ? selectedDims : null,
      include_weekday: includeWeekday,
      max_depth: maxDepth,
      beam_width: beamWidth,
      min_explanatory_power: minEp / 100,
      min_signal_to_noise: minSignal,
      with_narrative: withNarrative,
      language: locale,
    })
  }

  const result = run.data

  if (!projectId) {
    return (
      <SectionScroll>
        <SectionHeader title={t('title')} description={t('description')}
          icon={<Icons.Crosshair className="size-4.5" />} />
        <EmptyState icon="FolderKanban" title={t('noProject')} description={t('noProjectHint')} />
      </SectionScroll>
    )
  }

  return (
    <SectionScroll>
      <SectionHeader
        title={t('title')}
        description={t('description')}
        icon={<Icons.Crosshair className="size-4.5" />}
        actions={
          result?.available && (
            <SaveReportButton
              projectId={projectId}
              type="insights"
              title={`Root cause — ${result.measure_label}, ${result.current?.label ?? ''}`}
              markdown={buildReportMarkdown(result)}
            />
          )
        }
      />

      <div className="grid gap-5 lg:grid-cols-[320px_1fr]">
        {/* ── Controls ─────────────────────────────────────────────────── */}
        <Card className="h-fit p-4 lg:sticky lg:top-0">
          <h2 className="text-base font-semibold">{t('controls')}</h2>

          {options.isLoading && <LoadingState label={t('loadingOptions')} />}
          {options.isError && (
            <ErrorState
              title={t('optionsFailed')}
              body={(options.error as Error)?.message}
              onRetry={() => options.refetch()}
            />
          )}

          {options.data && !options.data.available && (
            <p className="mt-3 text-sm text-muted-foreground">{options.data.reason}</p>
          )}

          {options.data?.available && (
            <div className="mt-4 space-y-4">
              <div className="space-y-1.5">
                <Label className="text-sm">{t('measure')}</Label>
                <Select value={measure} onValueChange={setMeasure}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {measures.map((m) => (
                      <SelectItem key={m.key} value={m.key}>
                        {m.label}{!m.additive && ` · ${t('nonAdditive')}`}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-1.5">
                <Label className="text-sm">{t('comparison')}</Label>
                <Select value={windowMode} onValueChange={setWindowMode}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {(options.data.windows ?? []).map((w) => (
                      <SelectItem key={w.mode} value={w.mode}>
                        {w.label}{w.current && ` · ${w.current} vs ${w.prior}`}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              {windowMode === 'custom' && (
                <div className="grid grid-cols-2 gap-2">
                  {(['current_start', 'current_end', 'prior_start', 'prior_end'] as const).map((key) => (
                    <div key={key} className="space-y-1">
                      <Label className="text-xs text-muted-foreground">{t(key)}</Label>
                      <Input
                        type="date"
                        value={customDates[key]}
                        onChange={(e) => setCustomDates((c) => ({ ...c, [key]: e.target.value }))}
                      />
                    </div>
                  ))}
                </div>
              )}

              <div className="space-y-2">
                <Label className="text-sm">
                  {t('dimensions')} <span className="text-muted-foreground">({activeDims.length}/{dimensions.length})</span>
                </Label>
                <div className="flex flex-wrap gap-1.5">
                  {dimensions.map((d) => {
                    const on = activeDims.includes(d.name)
                    return (
                      <button
                        key={d.name}
                        onClick={() => toggleDimension(d.name)}
                        className={cn(
                          'rounded-md px-2 py-1 font-mono text-xs ring-1 ring-inset transition-colors',
                          on
                            ? 'bg-primary/12 text-primary ring-primary/25'
                            : 'bg-muted/30 text-muted-foreground ring-border hover:bg-muted/50'
                        )}
                        title={`${d.cardinality} ${t('distinctValues')}`}
                      >
                        {d.name}
                      </button>
                    )
                  })}
                </div>
              </div>

              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <Label className="text-sm">{t('includeWeekday')}</Label>
                  <p className="mt-0.5 text-xs leading-snug text-muted-foreground">
                    {t('includeWeekdayHint')}
                  </p>
                </div>
                <Switch checked={includeWeekday} onCheckedChange={setIncludeWeekday} />
              </div>

              <button
                onClick={() => setShowAdvanced((v) => !v)}
                className="flex w-full items-center gap-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground"
              >
                <Icons.ChevronRight className={cn('size-3.5 transition-transform', showAdvanced && 'rotate-90')} />
                {t('advanced')}
              </button>

              {showAdvanced && (
                <div className="space-y-4 rounded-lg border border-border bg-muted/20 p-3">
                  <SliderRow
                    label={t('maxDepth')} hint={t('maxDepthHint')}
                    value={maxDepth} min={1} max={4} step={1} onChange={setMaxDepth}
                    format={(v) => `${v}`}
                  />
                  <SliderRow
                    label={t('beamWidth')} hint={t('beamWidthHint')}
                    value={beamWidth} min={8} max={128} step={8} onChange={setBeamWidth}
                    format={(v) => `${v}`}
                  />
                  <SliderRow
                    label={t('minEp')} hint={t('minEpHint')}
                    value={minEp} min={1} max={50} step={1} onChange={setMinEp}
                    format={(v) => `${v}%`}
                  />
                  <SliderRow
                    label={t('minSignal')} hint={t('minSignalHint')}
                    value={minSignal} min={0} max={5} step={0.5} onChange={setMinSignal}
                    format={(v) => (v === 0 ? t('off') : `${v}σ`)}
                  />
                  <div className="flex items-center justify-between gap-3">
                    <Label className="text-sm">{t('withNarrative')}</Label>
                    <Switch checked={withNarrative} onCheckedChange={setWithNarrative} />
                  </div>
                </div>
              )}

              <Button className="w-full" onClick={submit} disabled={run.isPending}>
                {run.isPending
                  ? <><Icons.Loader2 className="size-3.5 animate-spin" /> {t('searching')}</>
                  : <><Icons.Search className="size-3.5" /> {t('findCause')}</>}
              </Button>
            </div>
          )}
        </Card>

        {/* ── Results ──────────────────────────────────────────────────── */}
        <div className="min-w-0 space-y-4">
          {run.isPending && <LoadingState label={t('searching')} stage={t('searchingStage')} />}

          {run.isError && (
            <ErrorState title={t('runFailed')} body={(run.error as Error)?.message} onRetry={submit} />
          )}

          {!run.isPending && !result && (
            <EmptyState
              icon="Crosshair"
              title={t('emptyTitle')}
              description={t('emptyBody')}
            />
          )}

          {result && !result.available && (
            <Card className="p-5">
              <div className="flex items-start gap-3">
                <Icons.Info className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                <div>
                  <h3 className="text-md font-semibold">{t('cannotAnswer')}</h3>
                  <p className="mt-1 text-sm leading-relaxed text-muted-foreground">{result.reason}</p>
                  {result.warnings.length > 0 && (
                    <ul className="mt-3 space-y-1">
                      {result.warnings.map((w, i) => (
                        <li key={i} className="text-sm text-muted-foreground">• {w}</li>
                      ))}
                    </ul>
                  )}
                </div>
              </div>
            </Card>
          )}

          {result?.available && (
            <>
              {/* Headline */}
              <Card className="p-5">
                <div className="flex flex-wrap items-end justify-between gap-4">
                  <div>
                    <p className="text-xs uppercase tracking-wider text-muted-foreground">
                      {result.measure_label} · {result.current?.label} vs {result.prior?.label}
                    </p>
                    <p className={cn('mt-1 text-2xl font-semibold leading-none tabular-nums',
                      result.total_delta < 0 ? 'text-destructive' : 'text-success')}>
                      {signed(result.total_delta, 2)}
                      {result.total_change_pct !== null && (
                        <span className="ms-2 text-lg text-muted-foreground">
                          ({signed(result.total_change_pct, 1)}%)
                        </span>
                      )}
                    </p>
                    <p className="mt-1.5 text-sm text-muted-foreground">
                      {num(result.prior?.value ?? 0)} → {num(result.current?.value ?? 0)} · {result.measure_basis}
                    </p>
                  </div>
                  {result.explanations.length > 0 && (
                    <div className="text-end">
                      <p className="text-xs uppercase tracking-wider text-muted-foreground">
                        {t('topSlice')}
                      </p>
                      <p className="mt-1 text-md font-semibold">{result.explanations[0].slice_label}</p>
                      <p className="mt-0.5 text-sm text-muted-foreground">
                        {t('explainsShare', {
                          pct: num(Math.abs(result.explanations[0].explanatory_power_pct), 1),
                        })}
                      </p>
                    </div>
                  )}
                </div>
              </Card>

              {result.warnings.length > 0 && (
                <Card className="border-warning/30 bg-warning/5 p-4">
                  <div className="flex items-start gap-2.5">
                    <Icons.AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning" />
                    <ul className="space-y-1.5">
                      {result.warnings.map((w, i) => (
                        <li key={i} className="text-sm leading-relaxed">{w}</li>
                      ))}
                    </ul>
                  </div>
                </Card>
              )}

              <Tabs defaultValue="explanations">
                <TabsList>
                  <TabsTrigger value="explanations">
                    {t('tabExplanations')} ({result.explanations.length})
                  </TabsTrigger>
                  <TabsTrigger value="dimensions">{t('tabDimensions')}</TabsTrigger>
                  <TabsTrigger value="search">{t('tabSearch')}</TabsTrigger>
                </TabsList>

                <TabsContent value="explanations" className="mt-4 space-y-4">
                  {result.narrative && (
                    <Card className="p-5">
                      <div className="mb-2 flex items-center gap-2">
                        <Icons.Sparkles className="size-3.5 text-primary" />
                        <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                          {t('narrative')}
                        </span>
                        <Badge variant="success">
                          <Icons.ShieldCheck className="size-3" /> {t('figuresSubstituted')}
                        </Badge>
                      </div>
                      <div className="prose prose-sm dark:prose-invert max-w-none text-base leading-relaxed">
                        <ReactMarkdown>{result.narrative}</ReactMarkdown>
                      </div>
                    </Card>
                  )}

                  {result.explanations.length === 0 ? (
                    <EmptyState
                      icon="Scan"
                      title={t('noSliceTitle')}
                      description={t('noSliceBody')}
                    />
                  ) : (
                    result.explanations.map((e) => (
                      <ExplanationCard
                        key={e.slice_expression}
                        explanation={e}
                        correctedThreshold={result.stats.corrected_threshold}
                        t={t}
                      />
                    ))
                  )}

                  <DrillPath result={result} t={t} />
                </TabsContent>

                <TabsContent value="dimensions" className="mt-4">
                  <DimensionTable result={result} t={t} />
                </TabsContent>

                <TabsContent value="search" className="mt-4">
                  <SearchStats result={result} t={t} />
                </TabsContent>
              </Tabs>
            </>
          )}
        </div>
      </div>
    </SectionScroll>
  )
}

function SliderRow({
  label, hint, value, min, max, step, onChange, format,
}: {
  label: string
  hint: string
  value: number
  min: number
  max: number
  step: number
  onChange: (v: number) => void
  format: (v: number) => string
}) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between">
        <Label className="text-sm">{label}</Label>
        <span className="tabular-nums text-sm font-medium">{format(value)}</span>
      </div>
      <Slider
        value={[value]}
        min={min}
        max={max}
        step={step}
        onValueChange={([v]) => onChange(v)}
      />
      <p className="text-xs leading-snug text-muted-foreground">{hint}</p>
    </div>
  )
}

/** Markdown for the Reports library — the finding plus the audit trail, so a
 * saved root-cause report can be re-read months later and still be checkable. */
function buildReportMarkdown(result: RootCauseResult): string {
  const lines: string[] = [
    `# Root cause — ${result.measure_label}`,
    '',
    `**${result.current?.label} vs ${result.prior?.label}**: ${result.total_delta >= 0 ? '+' : ''}` +
      `${num(result.total_delta)} (${signed(result.total_change_pct, 1)}%), ` +
      `from ${num(result.prior?.value ?? 0)} to ${num(result.current?.value ?? 0)}.`,
    '',
    `_Measure basis: ${result.measure_basis}_`,
    '',
  ]
  if (result.narrative) lines.push(result.narrative, '')
  if (result.explanations.length) {
    lines.push('## Explaining slices', '')
    lines.push('| # | Slice | Change | Share of change | Share of rows | Lift | Signal | Robust |')
    lines.push('|---|-------|--------|-----------------|---------------|------|--------|--------|')
    for (const e of result.explanations) {
      lines.push(
        `| ${e.rank} | ${e.slice_expression} | ${signed(e.delta, 2)} | ` +
        `${num(Math.abs(e.explanatory_power_pct), 1)}% | ${num(e.rows_share_pct, 1)}% | ` +
        `${num(e.concentration, 1)}× | ${e.signal_to_noise === null ? '—' : `${num(Math.min(e.signal_to_noise, 99), 1)}σ`} | ` +
        `${e.robust === null ? '—' : e.robust ? 'yes' : 'lead only'} |`
      )
    }
    lines.push('')
  }
  if (result.warnings.length) {
    lines.push('## What to be careful about', '')
    for (const w of result.warnings) lines.push(`- ${w}`)
    lines.push('')
  }
  const s = result.stats
  lines.push(
    '## How the search ran',
    '',
    `- Evaluated **${s.nodes_evaluated.toLocaleString()}** of ${s.exhaustive_combinations.toLocaleString()} possible slices` +
      `${s.search_reduction ? ` (${num(s.search_reduction, 1)}× reduction)` : ''} in ${num(s.elapsed_seconds * 1000, 0)} ms.`,
    `- Dimensions searched: ${s.dimensions_searched.join(', ') || '—'}.`,
    `- Pruned: ${s.pruned_by_support.toLocaleString()} by support, ` +
      `${s.pruned_by_magnitude_bound.toLocaleString()} by the magnitude bound (both exact), ` +
      `${s.pruned_by_beam.toLocaleString()} by the beam (approximate).`,
    `- ${s.slices_tested.toLocaleString()} slices were significance-tested; the Bonferroni-corrected ` +
      `threshold was ${num(s.corrected_threshold, 2)}σ.`,
  )
  return lines.join('\n')
}
