'use client'

import * as React from 'react'
import ReactMarkdown from 'react-markdown'
import { useTranslations } from 'next-intl'
import { cn } from '@/lib/utils'
import { useAppStore } from '@/lib/store'
import { useRunChurn, useRetentionPlan, type AtRiskCustomer } from '@/lib/queries/churn'
import { Card } from '@/components/ui/card'
import { Badge, GroundingBadge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { Label } from '@/components/ui/label'
import { SectionScroll, SectionHeader } from '@/components/shared/layout'
import { LoadingState, ErrorState } from '@/components/shared/states'
import { SaveReportButton } from '@/components/shared/save-report-button'
import * as Icons from 'lucide-react'

const HORIZON_OPTIONS = [30, 60, 90, 120, 180]

function formatMoney(n: number | null | undefined) {
  if (n == null) return '—'
  return `$${n.toLocaleString(undefined, { maximumFractionDigits: 0 })}`
}

function downloadCsv(rows: AtRiskCustomer[], filename: string) {
  const headers = ['customer', 'name', 'churn_probability', 'risk_tier', 'recency_days', 'frequency', 'monetary', 'expected_loss']
  const csv = [
    headers.join(','),
    ...rows.map((r) => headers.map((h) => JSON.stringify((r as any)[h] ?? '')).join(',')),
  ].join('\n')
  const blob = new Blob([csv], { type: 'text/csv' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

export function Churn() {
  const t = useTranslations('churn')
  const projectId = useAppStore((s) => s.activeProjectId)
  const setLastChurnResult = useAppStore((s) => s.setLastChurnResult)
  const [horizon, setHorizon] = React.useState('90')
  const [businessContext, setBusinessContext] = React.useState('')

  const runChurn = useRunChurn(projectId)
  const retentionPlan = useRetentionPlan(projectId)

  const run = () => {
    runChurn.mutate(
      { horizon_days: Number(horizon), top_n: 200 },
      { onSuccess: (res) => setLastChurnResult(res) }
    )
  }

  const payload = runChurn.data

  return (
    <SectionScroll>
      <SectionHeader
        title={t('header.title')}
        description={t('header.description')}
        icon={<Icons.ShieldAlert className="size-4.5" />}
        actions={
          payload?.available && (
            <Button size="sm" variant="outline" onClick={run} disabled={runChurn.isPending}>
              <Icons.RefreshCw className="size-3.5" /> {t('rerun')}
            </Button>
          )
        }
      />

      <Card padding="default" className="mb-4">
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label className="text-2xs uppercase tracking-wider text-muted-foreground/70 font-medium">{t('controls.horizon')}</label>
            <Select value={horizon} onValueChange={setHorizon}>
              <SelectTrigger className="h-9 w-[140px] mt-1 text-sm"><SelectValue /></SelectTrigger>
              <SelectContent>
                {HORIZON_OPTIONS.map((h) => (
                  <SelectItem key={h} value={String(h)}>{t('controls.horizonDays', { days: h })}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="ms-auto">
            <Button size="sm" onClick={run} disabled={runChurn.isPending}>
              {runChurn.isPending
                ? <><Icons.Loader2 className="size-3.5 animate-spin" /> {t('controls.analyzing')}</>
                : <><Icons.Play className="size-3.5" /> {t('controls.run')}</>}
            </Button>
          </div>
        </div>
      </Card>

      {runChurn.isPending && (
        <Card padding="none" className="mb-4">
          <LoadingState label={t('loading.label')} stage={t('loading.stage')} />
        </Card>
      )}

      {runChurn.isError && (
        <Card padding="none" className="mb-4">
          <ErrorState body={(runChurn.error as Error)?.message ?? t('errors.analysisFailed')} onRetry={run} />
        </Card>
      )}

      {payload && !payload.available && (
        <Card padding="lg">
          <p className="text-base text-muted-foreground">{payload.reason || t('notAvailable')}</p>
        </Card>
      )}

      {payload && payload.available && (
        <>
          {/* Top KPIs */}
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-5">
            <Card padding="default">
              <p className="text-2xs uppercase tracking-wider text-muted-foreground/70 font-medium">{t('kpis.customersAnalyzed')}</p>
              <p className="mt-1 text-xl font-semibold tabular-nums">{payload.customers_scored.toLocaleString()}</p>
              <p className="text-2xs text-muted-foreground mt-0.5">{t('kpis.customersAnalyzedSub')}</p>
            </Card>
            <Card padding="default">
              <div className="flex items-center justify-between">
                <p className="text-2xs uppercase tracking-wider text-muted-foreground/70 font-medium">{t('kpis.highRisk')}</p>
                <Icons.AlertTriangle className="size-3.5 text-destructive" />
              </div>
              <p className="mt-1 text-xl font-semibold text-destructive tabular-nums">{(payload.risk_distribution.high ?? 0).toLocaleString()}</p>
              <p className="text-2xs text-muted-foreground mt-0.5">
                {payload.customers_scored ? t('kpis.highRiskSub', { percent: (((payload.risk_distribution.high ?? 0) / payload.customers_scored) * 100).toFixed(1) }) : '—'}
              </p>
            </Card>
            <Card padding="default">
              <div className="flex items-center justify-between">
                <p className="text-2xs uppercase tracking-wider text-muted-foreground/70 font-medium">{t('kpis.expectedRevAtRisk')}</p>
                <Icons.TrendingDown className="size-3.5 text-warning" />
              </div>
              <p className="mt-1 text-xl font-semibold text-warning tabular-nums">{formatMoney(payload.expected_revenue_at_risk)}</p>
              <p className="text-2xs text-muted-foreground mt-0.5">{t('kpis.expectedRevAtRiskSub', { days: payload.horizon_days ?? 0 })}</p>
            </Card>
            <Card padding="default">
              <p className="text-2xs uppercase tracking-wider text-muted-foreground/70 font-medium">{t('kpis.modelAuc')}</p>
              <p className={cn('mt-1 text-xl font-semibold tabular-nums', payload.model.auc != null && 'text-success')}>
                {payload.model.auc != null ? Number(payload.model.auc).toFixed(3) : 'n/a'}
              </p>
              <p className="text-2xs text-muted-foreground mt-0.5">{payload.model.name || '—'}</p>
            </Card>
            <Card padding="default">
              <p className="text-2xs uppercase tracking-wider text-muted-foreground/70 font-medium">{t('kpis.validation')}</p>
              <p className="mt-1 text-sm font-semibold leading-tight capitalize">{(payload.model.validation || '—').replace(/_/g, ' ')}</p>
              <p className="text-2xs text-muted-foreground mt-0.5">{payload.model.n_cutoffs ? t('kpis.cutoffs', { count: payload.model.n_cutoffs }) : payload.snapshot_date}</p>
            </Card>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-4">
            {/* Risk distribution */}
            <Card padding="default" className="lg:col-span-2">
              <h3 className="text-base font-semibold mb-3">{t('riskDistribution.title')}</h3>
              <div className="grid grid-cols-3 gap-3 mb-4">
                {[
                  { label: t('riskDistribution.low'), count: payload.risk_distribution.low ?? 0, color: 'bg-success', tone: 'text-success' },
                  { label: t('riskDistribution.medium'), count: payload.risk_distribution.medium ?? 0, color: 'bg-warning', tone: 'text-warning' },
                  { label: t('riskDistribution.high'), count: payload.risk_distribution.high ?? 0, color: 'bg-destructive', tone: 'text-destructive' },
                ].map((r) => (
                  <div key={r.label} className="rounded-lg border border-border/60 bg-background/40 p-3">
                    <div className="flex items-center justify-between">
                      <p className="text-2xs uppercase tracking-wider text-muted-foreground/70 font-medium">{r.label}</p>
                      <span className={cn('size-2 rounded-full', r.color)} />
                    </div>
                    <p className={cn('mt-1 text-xl font-semibold tabular-nums', r.tone)}>{r.count.toLocaleString()}</p>
                    <p className="text-2xs text-muted-foreground">
                      {payload.customers_scored ? t('riskDistribution.percentOfCustomers', { percent: ((r.count / payload.customers_scored) * 100).toFixed(1) }) : '—'}
                    </p>
                  </div>
                ))}
              </div>
              <div className="h-3 rounded-full overflow-hidden bg-muted/30 flex">
                <div className="bg-success/60" style={{ width: `${((payload.risk_distribution.low ?? 0) / (payload.customers_scored || 1)) * 100}%` }} />
                <div className="bg-warning/70" style={{ width: `${((payload.risk_distribution.medium ?? 0) / (payload.customers_scored || 1)) * 100}%` }} />
                <div className="bg-destructive/70" style={{ width: `${((payload.risk_distribution.high ?? 0) / (payload.customers_scored || 1)) * 100}%` }} />
              </div>
              <p className="mt-2 text-xs text-muted-foreground">
                {t('riskDistribution.scoredBy', { model: payload.model.name || t('riskDistribution.defaultModel'), days: payload.horizon_days ?? 0, date: payload.snapshot_date ?? '—' })}
              </p>
            </Card>

            {/* Model metrics */}
            <Card padding="default">
              <h3 className="text-base font-semibold mb-3">{t('modelIntelligence.title')}</h3>
              <div className="space-y-2.5">
                <ModelMetric label={t('modelIntelligence.model')} value={payload.model.name || '—'} />
                <ModelMetric label={t('modelIntelligence.auc')} value={payload.model.auc != null ? Number(payload.model.auc).toFixed(3) : 'n/a'} tone={payload.model.auc != null ? 'success' : undefined} />
                <ModelMetric label={t('modelIntelligence.precision')} value={payload.model.precision != null ? Number(payload.model.precision).toFixed(2) : 'n/a'} />
                <ModelMetric label={t('modelIntelligence.recall')} value={payload.model.recall != null ? Number(payload.model.recall).toFixed(2) : 'n/a'} />
                <ModelMetric label={t('modelIntelligence.f1')} value={payload.model.f1 != null ? Number(payload.model.f1).toFixed(2) : 'n/a'} />
                <ModelMetric label={t('modelIntelligence.baseChurnRate')} value={payload.model.base_churn_rate != null ? `${(Number(payload.model.base_churn_rate) * 100).toFixed(0)}%` : 'n/a'} />
                <ModelMetric label={t('modelIntelligence.calibrated')} value={payload.model.calibrated ? t('modelIntelligence.yes') : t('modelIntelligence.no')} />
              </div>
              {payload.model.note && (
                <p className="mt-3 text-xs text-muted-foreground leading-relaxed">{payload.model.note}</p>
              )}
              <div className="mt-3 pt-3 border-t border-border/60 space-y-1.5">
                <Badge variant={payload.model.validation === 'out_of_time' ? 'success' : 'outline'} className="text-2xs w-full justify-center py-1">
                  <Icons.CheckCircle2 className="size-3" /> {payload.model.validation === 'out_of_time' ? t('modelIntelligence.backTestedOutOfTime') : payload.model.validation || t('modelIntelligence.notBackTested')}
                </Badge>
              </div>
            </Card>
          </div>

          {/* High-value at-risk table */}
          <Card padding="none" className="mb-4 overflow-hidden">
            <div className="px-4 py-3 border-b border-border flex items-center justify-between">
              <div>
                <h3 className="text-base font-semibold">{t('highRiskTable.title')}</h3>
                <p className="text-xs text-muted-foreground mt-0.5">
                  {payload.at_risk_ranked_by === 'expected_value_at_risk'
                    ? t('highRiskTable.rankedByValue')
                    : t('highRiskTable.rankedByProbability')}
                </p>
              </div>
              <div className="flex items-center gap-2">
                <Badge variant="danger" className="text-2xs">{t('highRiskTable.customersCount', { count: payload.at_risk_customers.length })}</Badge>
                <Button
                  size="sm" variant="outline" className="h-7 text-xs"
                  onClick={() => downloadCsv(payload.at_risk_customers, `churn_at_risk_${payload.snapshot_date}.csv`)}
                >
                  <Icons.Download className="size-3" /> {t('highRiskTable.exportCsv')}
                </Button>
              </div>
            </div>
            <div className="overflow-x-auto max-h-[420px] overflow-y-auto">
              <table className="w-full text-sm">
                <thead className="bg-muted/30 text-2xs uppercase tracking-wider text-muted-foreground/70 font-medium sticky top-0">
                  <tr>
                    <th className="px-4 py-2.5 text-start">{t('highRiskTable.columns.customer')}</th>
                    <th className="px-4 py-2.5 text-start">{t('highRiskTable.columns.tier')}</th>
                    <th className="px-4 py-2.5 text-end">{t('highRiskTable.columns.historicalSpend')}</th>
                    <th className="px-4 py-2.5 text-end">{t('highRiskTable.columns.churnProbability')}</th>
                    <th className="px-4 py-2.5 text-end">{t('highRiskTable.columns.expectedLoss')}</th>
                    <th className="px-4 py-2.5 text-end">{t('highRiskTable.columns.recencyDays')}</th>
                  </tr>
                </thead>
                <tbody>
                  {payload.at_risk_customers.slice(0, 100).map((c) => (
                    <tr key={c.customer} className="border-t border-border/60 hover:bg-accent/20">
                      <td className="px-4 py-2.5">
                        <div className="flex items-center gap-2.5">
                          <div className="flex size-7 shrink-0 items-center justify-center rounded-full text-2xs font-semibold bg-muted/40 text-muted-foreground">
                            {(c.name || c.customer).slice(0, 2).toUpperCase()}
                          </div>
                          <div>
                            <span className="font-medium block">{c.name || c.customer}</span>
                            {c.top_drivers?.length > 0 && (
                              <div className="flex items-center gap-1 mt-0.5">
                                {c.top_drivers.slice(0, 2).map((d) => (
                                  <span
                                    key={d.feature}
                                    title={t('highRiskTable.driverTooltip', { feature: d.feature, value: d.shap_value })}
                                    className={cn(
                                      'inline-flex items-center gap-0.5 rounded px-1 py-0.5 text-2xs font-mono leading-none',
                                      d.direction === 'increases_risk'
                                        ? 'bg-destructive/10 text-destructive'
                                        : 'bg-success/10 text-success'
                                    )}
                                  >
                                    {d.direction === 'increases_risk'
                                      ? <Icons.ArrowUp className="size-2.5" />
                                      : <Icons.ArrowDown className="size-2.5" />}
                                    {d.feature}
                                  </span>
                                ))}
                              </div>
                            )}
                          </div>
                        </div>
                      </td>
                      <td className="px-4 py-2.5">
                        <Badge variant={c.risk_tier === 'High' ? 'danger' : c.risk_tier === 'Medium' ? 'warning' : 'success'} className="text-2xs">{t(`riskTiers.${c.risk_tier}`)}</Badge>
                      </td>
                      <td className="px-4 py-2.5 text-end tabular-nums">{c.monetary != null ? formatMoney(c.monetary) : '—'}</td>
                      <td className="px-4 py-2.5 text-end">
                        <div className="inline-flex items-center gap-2">
                          <div className="h-1.5 w-12 rounded-full bg-muted overflow-hidden">
                            <div className={cn('h-full rounded-full', c.churn_probability > 0.75 ? 'bg-destructive' : 'bg-warning')} style={{ width: `${c.churn_probability * 100}%` }} />
                          </div>
                          <span className="tabular-nums text-muted-foreground">{c.churn_probability.toFixed(2)}</span>
                        </div>
                      </td>
                      <td className="px-4 py-2.5 text-end tabular-nums font-medium text-destructive">{c.expected_loss != null ? formatMoney(c.expected_loss) : '—'}</td>
                      <td className="px-4 py-2.5 text-end tabular-nums text-muted-foreground">{c.recency_days}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="px-4 py-2.5 border-t border-border bg-muted/20 text-xs text-muted-foreground">
              {t('highRiskTable.showing', { shown: Math.min(100, payload.at_risk_customers.length), total: payload.at_risk_customers.length })}
            </div>
          </Card>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {/* Feature importance */}
            <Card padding="default">
              <h3 className="text-base font-semibold mb-1">{t('featureImportance.title')}</h3>
              <p className="text-xs text-muted-foreground mb-3">{t('featureImportance.subtitle')}</p>
              {payload.feature_importance.length ? (
                <div className="space-y-2">
                  {payload.feature_importance.map((f) => (
                    <div key={f.feature} className="flex items-center gap-3">
                      <code className="w-44 shrink-0 text-2xs text-muted-foreground truncate">{f.feature}</code>
                      <div className="flex-1 h-2 rounded-full bg-muted/30 overflow-hidden">
                        <div className="h-full bg-primary/50 rounded-full" style={{ width: `${(f.importance / payload.feature_importance[0].importance) * 100}%` }} />
                      </div>
                      <span className="text-2xs tabular-nums text-muted-foreground w-8 text-end">{(f.importance * 100).toFixed(0)}%</span>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">{t('featureImportance.unavailable')}</p>
              )}

              {payload.shap_global_importance.length > 0 && (
                <div className="mt-4 pt-4 border-t border-border/60">
                  <h4 className="text-sm font-semibold mb-1">{t('featureImportance.shapTitle')}</h4>
                  <p className="text-xs text-muted-foreground mb-2.5">{t('featureImportance.shapSubtitle')}</p>
                  <div className="space-y-2">
                    {payload.shap_global_importance.map((f) => (
                      <div key={f.feature} className="flex items-center gap-3">
                        <code className="w-44 shrink-0 text-2xs text-muted-foreground truncate">{f.feature}</code>
                        <div className="flex-1 h-2 rounded-full bg-muted/30 overflow-hidden">
                          <div
                            className="h-full bg-info/60 rounded-full"
                            style={{ width: `${(f.importance / payload.shap_global_importance[0].importance) * 100}%` }}
                          />
                        </div>
                        <span className="text-2xs tabular-nums text-muted-foreground w-8 text-end">
                          {(f.importance * 100).toFixed(0)}%
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </Card>

            {/* Retention strategy */}
            <Card padding="default">
              <div className="flex items-center justify-between mb-3">
                <div>
                  <h3 className="text-base font-semibold">{t('retention.title')}</h3>
                  <p className="text-xs text-muted-foreground mt-0.5">{t('retention.subtitle')}</p>
                </div>
                {retentionPlan.data && <GroundingBadge status="grounded" />}
              </div>

              {!retentionPlan.data && (
                <>
                  <Label className="text-sm">{t('retention.businessContextLabel')}</Label>
                  <Textarea
                    className="mt-1.5 min-h-[70px] text-sm"
                    placeholder={t('retention.businessContextPlaceholder')}
                    value={businessContext}
                    onChange={(e) => setBusinessContext(e.target.value)}
                  />
                  <Button
                    size="sm" className="mt-3"
                    onClick={() => retentionPlan.mutate({ churn_result: payload, business_context: businessContext })}
                    disabled={retentionPlan.isPending}
                  >
                    {retentionPlan.isPending
                      ? <><Icons.Loader2 className="size-3.5 animate-spin" /> {t('retention.writing')}</>
                      : <><Icons.Sparkles className="size-3.5" /> {t('retention.generate')}</>}
                  </Button>
                </>
              )}

              {retentionPlan.isError && (
                <p className="mt-2 text-sm text-destructive">{t('errors.retentionPlanFailed')}</p>
              )}

              {retentionPlan.data && (
                <>
                  <div className="flex justify-end mb-2">
                    <SaveReportButton
                      key={retentionPlan.data.report_md}
                      projectId={projectId}
                      type="churn"
                      title={t('retention.reportTitle')}
                      markdown={retentionPlan.data.report_md}
                    />
                  </div>
                  <div className="prose prose-sm dark:prose-invert max-w-none text-sm leading-relaxed max-h-[420px] overflow-y-auto">
                    <ReactMarkdown>{retentionPlan.data.report_md}</ReactMarkdown>
                  </div>
                </>
              )}
            </Card>
          </div>
        </>
      )}
    </SectionScroll>
  )
}

function ModelMetric({ label, value, tone }: { label: string; value: string; tone?: 'success' }) {
  return (
    <div className="flex items-center justify-between py-1.5 border-b border-border/40 last:border-0">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className={cn('text-sm font-medium tabular-nums', tone === 'success' && 'text-success')}>{value}</span>
    </div>
  )
}
