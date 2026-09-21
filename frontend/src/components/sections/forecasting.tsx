'use client'

import * as React from 'react'
import ReactMarkdown from 'react-markdown'
import { useTranslations } from 'next-intl'
import { cn } from '@/lib/utils'
import { useAppStore } from '@/lib/store'
import { useForecastMetrics, useRunForecast, type ForecastOutput } from '@/lib/queries/forecasting'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { SectionScroll, SectionHeader } from '@/components/shared/layout'
import { PlotlyChart } from '@/components/shared/plotly-chart'
import { LoadingState, ErrorState } from '@/components/shared/states'
import { SaveReportButton } from '@/components/shared/save-report-button'
import * as Icons from 'lucide-react'

const MODEL_OPTIONS = [
  'Auto', 'Ensemble', 'Prophet', 'ETS', 'Holt-Winters', 'SARIMA', 'ARIMA',
  'STL-ETS', 'STL-ARIMA', 'Theta', 'Harmonic Regression', 'Seasonal Profile',
  'Linear Trend', 'Damped Trend', 'Damped Drift', 'Moving Average',
  'Mean (4 seasons)', 'Seasonal Naive', 'Naive', 'Drift',
]
const GRANULARITY_OPTIONS = ['native', 'daily', 'weekly', 'monthly']
const HORIZON_OPTIONS = [7, 15, 30, 60, 90]

function formatMetric(name: string) {
  return name.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

function formatNumber(n: number) {
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 }).format(n)
}

function formatCompact(n: number) {
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 }).format(n)
}

// Reliability drives the loudest element on the page. A forecast that failed
// out-of-sample validation looks identical to one that passed unless the UI
// says otherwise, and a confident-looking number nobody flagged is the failure
// mode users cannot detect for themselves.
const RELIABILITY_STYLES = {
  reliable: {
    wrap: 'border-success/30 bg-success/[0.06]',
    text: 'text-success',
    icon: Icons.ShieldCheck,
  },
  indicative: {
    wrap: 'border-warning/35 bg-warning/[0.07]',
    text: 'text-warning',
    icon: Icons.AlertTriangle,
  },
  unreliable: {
    wrap: 'border-destructive/35 bg-destructive/[0.07]',
    text: 'text-destructive',
    icon: Icons.ShieldAlert,
  },
  unknown: {
    wrap: 'border-border/60 bg-muted/30',
    text: 'text-muted-foreground',
    icon: Icons.HelpCircle,
  },
} as const

export function Forecasting() {
  const t = useTranslations('forecasting')
  const projectId = useAppStore((s) => s.activeProjectId)
  const setLastForecastOutputs = useAppStore((s) => s.setLastForecastOutputs)
  const metricsQuery = useForecastMetrics(projectId)
  const runForecast = useRunForecast(projectId)

  const [metric, setMetric] = React.useState('')
  const [granularity, setGranularity] = React.useState('native')
  const [horizon, setHorizon] = React.useState('30')
  const [model, setModel] = React.useState('Auto')
  const [activeMetric, setActiveMetric] = React.useState<string | null>(null)

  const availableMetrics = React.useMemo(() => metricsQuery.data?.metrics ?? [], [metricsQuery.data])
  const effectiveMetric = metric || availableMetrics[0] || ''

  const run = () => {
    if (!effectiveMetric) return
    runForecast.mutate(
      { targets: [effectiveMetric], horizon_days: Number(horizon), granularity, model_override: model },
      {
        onSuccess: (res) => {
          setActiveMetric(res.outputs[0]?.metric ?? null)
          setLastForecastOutputs(res.outputs)
        },
      }
    )
  }

  const result = runForecast.data
  const output: ForecastOutput | undefined =
    result?.outputs.find((o) => o.metric === activeMetric) ?? result?.outputs[0]
  const figure = output ? result?.figures[output.metric] : undefined
  const selectedCv = output?.cv_results.find((r) => r.model === output.selected_model)

  return (
    <SectionScroll>
      <SectionHeader
        title={t('header.title')}
        description={t('header.description')}
        icon={<Icons.TrendingUp className="size-4.5" />}
        actions={
          result && (
            <>
              {result.forecastable && result.report_md && (
                <SaveReportButton
                  key={result.run_id}
                  projectId={projectId}
                  type="forecast"
                  title={t('reportTitle', { metrics: result.outputs.map((o) => o.metric).join(', ') })}
                  markdown={result.report_md}
                />
              )}
              <Button size="sm" variant="outline" onClick={run} disabled={runForecast.isPending}>
                <Icons.RefreshCw className="size-3.5" /> {t('rerun')}
              </Button>
            </>
          )
        }
      />

      {/* Controls */}
      <Card padding="default" className="mb-4">
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label className="text-2xs uppercase tracking-wider text-muted-foreground/70 font-medium">{t('controls.targetMetric')}</label>
            <Select value={effectiveMetric} onValueChange={setMetric} disabled={metricsQuery.isLoading || !availableMetrics.length}>
              <SelectTrigger className="h-9 w-[180px] mt-1 text-sm">
                <SelectValue placeholder={metricsQuery.isLoading ? t('controls.loadingMetrics') : t('controls.selectMetric')} />
              </SelectTrigger>
              <SelectContent>
                {availableMetrics.map((m) => (
                  <SelectItem key={m} value={m}>{formatMetric(m)}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <label className="text-2xs uppercase tracking-wider text-muted-foreground/70 font-medium">{t('common.granularity')}</label>
            <Select value={granularity} onValueChange={setGranularity}>
              <SelectTrigger className="h-9 w-[120px] mt-1 text-sm"><SelectValue /></SelectTrigger>
              <SelectContent>
                {GRANULARITY_OPTIONS.map((g) => (
                  <SelectItem key={g} value={g}>{t(`granularityOptions.${g}`)}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <label className="text-2xs uppercase tracking-wider text-muted-foreground/70 font-medium">{t('common.horizon')}</label>
            <Select value={horizon} onValueChange={setHorizon}>
              <SelectTrigger className="h-9 w-[110px] mt-1 text-sm"><SelectValue /></SelectTrigger>
              <SelectContent>
                {HORIZON_OPTIONS.map((h) => (
                  <SelectItem key={h} value={String(h)}>{t('controls.horizonDays', { days: h })}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <label className="text-2xs uppercase tracking-wider text-muted-foreground/70 font-medium">{t('controls.model')}</label>
            <Select value={model} onValueChange={setModel}>
              <SelectTrigger className="h-9 w-[150px] mt-1 text-sm"><SelectValue /></SelectTrigger>
              <SelectContent>
                {MODEL_OPTIONS.map((m) => (
                  <SelectItem key={m} value={m}>{m === 'Auto' ? t('controls.modelAuto') : m}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="ms-auto">
            <Button size="sm" onClick={run} disabled={runForecast.isPending || !effectiveMetric}>
              {runForecast.isPending
                ? <><Icons.Loader2 className="size-3.5 animate-spin" /> {t('controls.running')}</>
                : <><Icons.Play className="size-3.5" /> {t('controls.run')}</>}
            </Button>
          </div>
        </div>
      </Card>

      {metricsQuery.isError && (
        <Card padding="none" className="mb-4">
          <ErrorState body={t('errors.loadMetrics')} onRetry={() => metricsQuery.refetch()} />
        </Card>
      )}

      {!metricsQuery.isLoading && !metricsQuery.isError && !availableMetrics.length && (
        <Card padding="lg">
          <p className="text-base text-muted-foreground">
            {t('emptyMetrics')}
          </p>
        </Card>
      )}

      {runForecast.isPending && (
        <Card padding="none" className="mb-4">
          <LoadingState
            label={t('loading.label')}
            stage={t('loading.stage')}
          />
        </Card>
      )}

      {runForecast.isError && (
        <Card padding="none" className="mb-4">
          <ErrorState body={(runForecast.error as Error)?.message ?? t('errors.generateForecast')} onRetry={run} />
        </Card>
      )}

      {result && !result.forecastable && (
        <Card padding="lg" className="mb-4">
          <p className="text-base text-muted-foreground">{result.validation_message || t('notForecastable')}</p>
        </Card>
      )}

      {result && result.forecastable && output && (
        <>
          {result.outputs.length > 1 && (
            <div className="flex flex-wrap gap-2 mb-4">
              {result.outputs.map((o) => (
                <button
                  key={o.metric}
                  onClick={() => setActiveMetric(o.metric)}
                  className={cn(
                    'rounded-md border px-3 py-1.5 text-sm transition-colors',
                    o.metric === output.metric
                      ? 'border-primary bg-primary/10 text-primary'
                      : 'border-border/60 text-muted-foreground hover:text-foreground'
                  )}
                >
                  {formatMetric(o.metric)}
                </button>
              ))}
            </div>
          )}

          {output.error ? (
            <Card padding="lg" className="mb-4">
              <p className="text-base text-destructive">{output.error}</p>
            </Card>
          ) : (
            <>
              {/* Reliability verdict — deliberately above the numbers it qualifies. */}
              <ReliabilityBanner
                output={output}
                onUseGranularity={(g) => {
                  setGranularity(g)
                  runForecast.mutate(
                    {
                      targets: [output.metric],
                      horizon_days: Number(horizon),
                      granularity: g,
                      model_override: model,
                    },
                    {
                      onSuccess: (res) => {
                        setActiveMetric(res.outputs[0]?.metric ?? null)
                        setLastForecastOutputs(res.outputs)
                      },
                    }
                  )
                }}
                t={t}
              />

              {/* Main forecast chart */}
              <Card padding="default" className="mb-4">
                <div className="flex items-center justify-between mb-4">
                  <div>
                    <h3 className="text-md font-semibold">
                      {t('chart.title', {
                        metric: formatMetric(output.metric),
                        horizon,
                        frequency: result.frequency || output.granularity,
                      })}
                    </h3>
                    <p className="text-xs text-muted-foreground mt-0.5">
                      {t('chart.subtitle', {
                        periods: output.training_periods,
                        granularity: output.granularity,
                        model: output.selected_model || 'n/a',
                      })}
                    </p>
                  </div>
                </div>
                {figure && (
                  <PlotlyChart
                    figure={figure}
                    height={320}
                    projectId={projectId}
                    title={formatMetric(output.metric)}
                    context={`Forecast for ${formatMetric(output.metric)}, horizon ${horizon} ${output.granularity}`}
                  />
                )}
                <div className="mt-3 grid grid-cols-2 md:grid-cols-4 gap-3 pt-3 border-t border-border/60">
                  {/* The horizon total is the number a business plans against, and
                      it is far more predictable than any single period inside it —
                      so it leads, with its range rather than as a bare point. */}
                  <ForecastStat
                    label={
                      output.aggregation === 'sum'
                        ? t('stats.horizonTotal', { horizon })
                        : t('stats.horizonAverage', { horizon })
                    }
                    value={
                      output.horizon_total != null
                        ? formatCompact(output.horizon_total)
                        : formatNumber(output.forecasted_value)
                    }
                    sub={
                      output.horizon_total_lower != null && output.horizon_total_upper != null
                        ? t('stats.range', {
                            level: Math.round((output.interval_level ?? 0.8) * 100),
                            lower: formatCompact(output.horizon_total_lower),
                            upper: formatCompact(output.horizon_total_upper),
                          })
                        : undefined
                    }
                  />
                  <ForecastStat
                    label={t('stats.vsPrevious', { horizon })}
                    value={`${output.change_percent >= 0 ? '+' : ''}${output.change_percent.toFixed(1)}%`}
                    sub={
                      output.baseline_window_value != null
                        ? t('stats.previousWas', { value: formatCompact(output.baseline_window_value) })
                        : undefined
                    }
                    tone={output.change_percent >= 0 ? 'success' : 'warning'}
                  />
                  <ForecastStat
                    label={t('common.skillVsNaive')}
                    value={output.skill_score != null ? `${output.skill_score >= 0 ? '+' : ''}${(output.skill_score * 100).toFixed(1)}%` : 'n/a'}
                    sub={t('stats.skillSub')}
                    tone={
                      output.skill_score == null
                        ? undefined
                        : output.skill_score > 0.1
                          ? 'success'
                          : output.skill_score > 0
                            ? 'warning'
                            : 'danger'
                    }
                  />
                  {/* A band's measured coverage is the honest answer to "how much
                      should I trust this range" — more useful than its label. */}
                  <ForecastStat
                    label={t('stats.coverage', { level: Math.round((output.interval_level ?? 0.8) * 100) })}
                    value={output.measured_coverage != null ? `${(output.measured_coverage * 100).toFixed(0)}%` : 'n/a'}
                    sub={
                      output.measured_coverage != null
                        ? Math.abs(output.measured_coverage - (output.interval_level ?? 0.8)) <= 0.1
                          ? t('stats.calibrated')
                          : t('stats.approximate')
                        : t('common.notBackTested')
                    }
                    tone={
                      output.measured_coverage != null &&
                      Math.abs(output.measured_coverage - (output.interval_level ?? 0.8)) <= 0.1
                        ? 'success'
                        : 'warning'
                    }
                  />
                </div>
              </Card>

              <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
                {/* Model leaderboard */}
                <Card padding="default" className="lg:col-span-2">
                  <div className="flex items-center justify-between mb-3">
                    <div>
                      <h3 className="text-md font-semibold">{t('leaderboard.title')}</h3>
                      <p className="text-xs text-muted-foreground mt-0.5">{t('leaderboard.subtitle')}</p>
                    </div>
                    <Badge variant="outline" className="text-2xs">{t('leaderboard.modelsCount', { count: output.cv_results.length })}</Badge>
                  </div>
                  {output.cv_results.length ? (
                    <div className="overflow-x-auto">
                      <table className="w-full text-sm">
                        <thead className="text-2xs uppercase tracking-wider text-muted-foreground/70 font-medium">
                          <tr className="border-b border-border">
                            <th className="py-2 pe-3 text-start">{t('leaderboard.columns.model')}</th>
                            <th className="py-2 px-3 text-right">{t('common.mase')}</th>
                            <th className="py-2 px-3 text-right">{t('common.mape')}</th>
                            <th className="py-2 px-3 text-right">{t('common.mae')}</th>
                            <th className="py-2 px-3 text-right">{t('common.rmse')}</th>
                            <th className="py-2 ps-3 text-right">{t('leaderboard.columns.folds')}</th>
                          </tr>
                        </thead>
                        <tbody>
                          {output.cv_results.map((m) => (
                            <tr
                              key={m.model}
                              className={cn(
                                'border-b border-border/60 last:border-0',
                                m.model === output.selected_model && 'bg-primary/[0.06]'
                              )}
                            >
                              <td className="py-2.5 pe-3">
                                <div className="flex items-center gap-2">
                                  {m.model === output.selected_model && <Icons.CheckCircle2 className="size-3.5 text-primary" />}
                                  <span className="font-medium">{m.model}</span>
                                </div>
                              </td>
                              <td className={cn(
                                'py-2.5 px-3 text-right tabular-nums',
                                m.mase != null && m.mase < 1 && 'text-success'
                              )}>{m.mase != null ? Number(m.mase).toFixed(2) : '—'}</td>
                              <td className="py-2.5 px-3 text-right tabular-nums">{m.mape != null ? `${Number(m.mape).toFixed(1)}%` : '—'}</td>
                              <td className="py-2.5 px-3 text-right tabular-nums">{m.mae != null ? formatNumber(m.mae) : '—'}</td>
                              <td className="py-2.5 px-3 text-right tabular-nums">{m.rmse != null ? formatNumber(m.rmse) : '—'}</td>
                              <td className="py-2.5 ps-3 text-right tabular-nums">{m.folds ?? '—'}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <p className="text-sm text-muted-foreground">{t('leaderboard.noHistory')}</p>
                  )}

                  {selectedCv?.components && (
                    <div className="mt-4 rounded-lg border border-primary/25 bg-primary/[0.04] p-3.5">
                      <div className="flex items-center gap-1.5 text-2xs uppercase tracking-wider text-primary font-semibold mb-2">
                        <Icons.Layers className="size-3" /> {t('ensemble.title')}
                      </div>
                      <div className="grid grid-cols-3 gap-3">
                        {(selectedCv.components as string[]).map((name, i) => {
                          const w = (selectedCv.weights as number[] | undefined)?.[i] ?? 0
                          return (
                            <div key={name} className="rounded-md border border-border/60 bg-background/40 p-2.5">
                              <p className="text-xs font-medium">{name}</p>
                              <p className="text-2xs text-muted-foreground mt-0.5">
                                {t('ensemble.weight')} <span className="text-primary font-semibold">{(w * 100).toFixed(0)}%</span>
                              </p>
                              <div className="mt-1.5 h-1 rounded-full bg-muted overflow-hidden">
                                <div className="h-full bg-primary rounded-full" style={{ width: `${w * 100}%` }} />
                              </div>
                            </div>
                          )
                        })}
                      </div>
                    </div>
                  )}
                </Card>

                {/* Right column: evaluation + why + risks/actions */}
                <div className="space-y-4">
                  <Card padding="default">
                    <h3 className="text-base font-semibold mb-3">{t('evaluation.title')}</h3>
                    <div className="space-y-2.5">
                      <EvalRow label={t('common.selectedModel')} value={output.selected_model || '—'} />
                      <EvalRow
                        label={t('common.mase')}
                        value={output.evaluation.mase != null ? Number(output.evaluation.mase).toFixed(2) : 'n/a'}
                        tone={output.evaluation.mase != null ? (output.evaluation.mase < 1 ? 'success' : 'warning') : undefined}
                      />
                      <EvalRow label={t('common.mape')} value={output.evaluation.mape != null ? `${Number(output.evaluation.mape).toFixed(1)}%` : 'n/a'} />
                      <EvalRow label={t('common.mae')} value={output.evaluation.mae != null ? formatNumber(output.evaluation.mae) : 'n/a'} />
                      <EvalRow label={t('common.rmse')} value={output.evaluation.rmse != null ? formatNumber(output.evaluation.rmse) : 'n/a'} />
                      <EvalRow
                        label={t('common.skillVsNaive')}
                        value={output.skill_score != null ? `${output.skill_score >= 0 ? '+' : ''}${(output.skill_score * 100).toFixed(1)}%` : 'n/a'}
                        tone={output.skill_score != null && output.skill_score >= 0 ? 'success' : undefined}
                      />
                      <EvalRow
                        label={t('common.confidence')}
                        value={output.confidence_score != null ? `${(output.confidence_score * 100).toFixed(0)}%` : t('common.notBackTested')}
                        tone={output.confidence_score != null ? 'success' : 'warning'}
                      />
                      {/* How much of this series is learnable at all — the ceiling
                          on any model's accuracy, independent of which model won. */}
                      {output.predictability != null && (
                        <EvalRow
                          label={t('common.signalVsNoise')}
                          value={`${(output.predictability * 100).toFixed(0)}% ${verdictLabel(output.signal_verdict, t)}`}
                          tone={output.predictability >= 0.2 ? 'success' : 'warning'}
                        />
                      )}
                      <EvalRow label={t('common.horizon')} value={output.forecast_horizon?.replace(/_/g, ' ') || t('controls.horizonDays', { days: horizon })} />
                      <EvalRow label={t('common.granularity')} value={output.granularity} />
                      <EvalRow label={t('common.trainingPeriods')} value={`${output.training_periods} ${output.granularity}`} />
                      {output.transform && output.transform !== 'none' && (
                        <EvalRow label={t('common.fittedScale')} value={output.transform} />
                      )}
                    </div>
                  </Card>

                  <DataQualityCard output={output} t={t} />

                  <Card padding="default">
                    <div className="flex items-center gap-1.5 text-2xs uppercase tracking-wider text-primary font-semibold mb-2">
                      <Icons.Sparkles className="size-3" /> {t('why.title')}
                    </div>
                    <p className="text-sm text-foreground/85 leading-relaxed">{output.model_selection_reason}</p>
                    {output.business_summary && (
                      <p className="mt-2 text-sm text-muted-foreground leading-relaxed">{output.business_summary}</p>
                    )}
                    <div className="mt-3 flex items-center gap-2 pt-3 border-t border-border/60">
                      <Badge variant={output.confidence_score != null ? 'success' : 'outline'} className="text-2xs">
                        {output.confidence_score != null ? t('common.backTested') : t('common.notBackTested')}
                      </Badge>
                      <Badge variant="outline" className="text-2xs capitalize">{t('why.impact', { impact: output.business_impact })}</Badge>
                    </div>
                  </Card>

                  {(output.risks.length > 0 || output.opportunities.length > 0 || output.recommended_actions.length > 0) && (
                    <Card padding="default">
                      <h3 className="text-base font-semibold mb-3">{t('risksActions.title')}</h3>
                      {output.risks.length > 0 && (
                        <div className="mb-3">
                          <p className="text-2xs uppercase tracking-wider text-muted-foreground/70 font-medium mb-1.5">{t('risksActions.risks')}</p>
                          <ul className="space-y-1">
                            {output.risks.map((r, i) => (
                              <li key={i} className="text-sm text-foreground/85 flex gap-1.5">
                                <Icons.AlertTriangle className="size-3.5 text-warning shrink-0 mt-0.5" />{r}
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}
                      {output.opportunities.length > 0 && (
                        <div className="mb-3">
                          <p className="text-2xs uppercase tracking-wider text-muted-foreground/70 font-medium mb-1.5">{t('risksActions.opportunities')}</p>
                          <ul className="space-y-1">
                            {output.opportunities.map((r, i) => (
                              <li key={i} className="text-sm text-foreground/85 flex gap-1.5">
                                <Icons.Sparkles className="size-3.5 text-success shrink-0 mt-0.5" />{r}
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}
                      {output.recommended_actions.length > 0 && (
                        <div>
                          <p className="text-2xs uppercase tracking-wider text-muted-foreground/70 font-medium mb-1.5">{t('risksActions.recommendedActions')}</p>
                          <ul className="space-y-1">
                            {output.recommended_actions.map((r, i) => (
                              <li key={i} className="text-sm text-foreground/85 flex gap-1.5">
                                <Icons.ArrowRight className="size-3.5 text-primary shrink-0 mt-0.5" />{r}
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}
                    </Card>
                  )}
                </div>
              </div>

              {result.report_md && (
                <Card padding="lg" className="mt-4 prose-report">
                  <div className="prose prose-sm dark:prose-invert max-w-none text-base leading-relaxed">
                    <ReactMarkdown>{result.report_md}</ReactMarkdown>
                  </div>
                </Card>
              )}
            </>
          )}
        </>
      )}
    </SectionScroll>
  )
}

const KNOWN_VERDICTS = ['strong', 'moderate', 'weak', 'noise-dominated', 'unknown'] as const

/** Look up a verdict label only for keys the message catalogue actually has —
 *  next-intl throws on a missing key, and an engine that gains a new verdict
 *  should not be able to crash the page. */
function verdictLabel(verdict: string, t: ReturnType<typeof useTranslations>) {
  return (KNOWN_VERDICTS as readonly string[]).includes(verdict)
    ? t(`verdict.${verdict}` as never)
    : ''
}

function EvalRow({ label, value, tone }: { label: string; value: string; tone?: 'success' | 'warning' | 'danger' }) {
  return (
    <div className="flex items-center justify-between py-1.5 border-b border-border/40 last:border-0">
      <span className="text-sm text-muted-foreground">{label}</span>
      <span className={cn(
        'text-sm font-medium tabular-nums',
        tone === 'success' && 'text-success',
        tone === 'warning' && 'text-warning',
        tone === 'danger' && 'text-destructive',
      )}>{value}</span>
    </div>
  )
}

function ForecastStat({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: 'success' | 'warning' | 'danger' }) {
  return (
    <div>
      <p className="text-2xs uppercase tracking-wider text-muted-foreground/70 font-medium">{label}</p>
      <p className={cn(
        'mt-1 text-lg font-semibold tabular-nums',
        tone === 'success' && 'text-success',
        tone === 'warning' && 'text-warning',
        tone === 'danger' && 'text-destructive',
      )}>{value}</p>
      {sub && <p className="text-2xs text-muted-foreground/80 mt-0.5 truncate" title={sub}>{sub}</p>}
    </div>
  )
}

/**
 * The verdict on whether this forecast is fit to act on, stated above the
 * numbers rather than beside them. Also surfaces live drift — a model whose
 * error has run away from its back-test has stopped working, regardless of how
 * good the back-test looked — and offers a one-click switch to a granularity
 * where the data actually carries signal.
 */
function ReliabilityBanner({
  output,
  onUseGranularity,
  t,
}: {
  output: ForecastOutput
  onUseGranularity: (g: string) => void
  t: ReturnType<typeof useTranslations>
}) {
  const level = (output.reliability ?? 'unknown') as keyof typeof RELIABILITY_STYLES
  const style = RELIABILITY_STYLES[level] ?? RELIABILITY_STYLES.unknown
  const Icon = style.icon
  const drift = output.drift
  const driftAlert = drift && (drift.status === 'degraded' || drift.status === 'watch')

  if (level === 'reliable' && !driftAlert) {
    return (
      <div className={cn('mb-4 flex items-center gap-2 rounded-lg border px-3.5 py-2', style.wrap)}>
        <Icon className={cn('size-4 shrink-0', style.text)} />
        <p className="text-sm text-foreground/85">
          <span className={cn('font-semibold', style.text)}>{t('reliability.reliable')}</span>
          {' — '}{output.reliability_headline}
        </p>
      </div>
    )
  }

  return (
    <div className={cn('mb-4 rounded-lg border p-3.5', style.wrap)}>
      <div className="flex items-start gap-2.5">
        <Icon className={cn('size-4.5 shrink-0 mt-0.5', style.text)} />
        <div className="min-w-0 flex-1">
          <p className={cn('text-base font-semibold', style.text)}>
            {t(`reliability.${level}` as never)}
          </p>
          {output.reliability_headline && (
            <p className="text-sm text-foreground/85 mt-0.5">{output.reliability_headline}</p>
          )}

          {output.reliability_reasons?.length > 0 && (
            <ul className="mt-2 space-y-1">
              {output.reliability_reasons.map((r, i) => (
                <li key={i} className="text-xs text-muted-foreground flex gap-1.5">
                  <span className="text-muted-foreground/50 shrink-0">•</span>
                  <span>{r}</span>
                </li>
              ))}
            </ul>
          )}

          {driftAlert && (
            <div className="mt-2.5 rounded-md border border-border/60 bg-background/50 px-2.5 py-2">
              <div className="flex items-center gap-1.5 text-2xs uppercase tracking-wider font-semibold text-muted-foreground/80">
                <Icons.Activity className="size-3" /> {t('drift.title')}
              </div>
              <p className="text-xs text-foreground/85 mt-1">{drift!.message}</p>
            </div>
          )}

          {output.recommended_granularity && (
            <div className="mt-2.5 flex flex-wrap items-center gap-2">
              <p className="text-xs text-foreground/85">{output.granularity_reason}</p>
              <Button
                size="sm"
                variant="outline"
                className="h-7 text-xs"
                onClick={() => onUseGranularity(output.recommended_granularity)}
              >
                <Icons.Wand2 className="size-3" />
                {t('reliability.tryGranularity', { granularity: output.recommended_granularity })}
              </Button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

/**
 * What had to be done to the raw data before a model could see it. Users who
 * spot a "12 periods had no records" note here can usually explain it, and the
 * explanation frequently matters more than the forecast.
 */
function DataQualityCard({ output, t }: { output: ForecastOutput; t: ReturnType<typeof useTranslations> }) {
  const dq = output.data_quality ?? {}
  const items: string[] = []

  if (dq.filled_periods) {
    items.push(
      t('dataQuality.filled', {
        count: dq.filled_periods,
        share: Math.round((dq.fill_share ?? 0) * 100),
        how: dq.aggregation === 'sum' ? t('dataQuality.asZero') : t('dataQuality.interpolated'),
      })
    )
  }
  if (dq.dropped_partial?.length) {
    items.push(t('dataQuality.droppedPartial', { kinds: dq.dropped_partial.join('/') }))
  }
  if (output.anomalies_detected > 0) {
    const dates = (output.anomaly_periods ?? [])
      .map((p) => p.date)
      .filter(Boolean)
      .slice(0, 3)
      .join(', ')
    items.push(
      dates
        ? t('dataQuality.anomalies', { count: output.anomalies_detected, dates })
        : t('dataQuality.anomaliesNoDates', { count: output.anomalies_detected })
    )
  }
  if (output.level_shift) {
    items.push(t('dataQuality.levelShift', { score: output.level_shift.score }))
  }
  if (dq.intermittent) items.push(t('dataQuality.intermittent'))

  if (!items.length) return null

  return (
    <Card padding="default">
      <div className="flex items-center gap-1.5 text-2xs uppercase tracking-wider text-muted-foreground/80 font-semibold mb-2">
        <Icons.Database className="size-3" /> {t('dataQuality.title')}
      </div>
      <ul className="space-y-1.5">
        {items.map((item, i) => (
          <li key={i} className="text-xs text-foreground/80 flex gap-1.5">
            <Icons.Info className="size-3.5 text-muted-foreground/60 shrink-0 mt-0.5" />
            <span>{item}</span>
          </li>
        ))}
      </ul>
    </Card>
  )
}
