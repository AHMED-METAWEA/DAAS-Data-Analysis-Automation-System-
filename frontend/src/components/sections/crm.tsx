'use client'

import * as React from 'react'
import { useTranslations } from 'next-intl'
import { cn } from '@/lib/utils'
import { useAppStore } from '@/lib/store'
import {
  usePortfolio,
  useCustomers,
  useCustomerDetail,
  useRankingComparison,
  useSnapshots,
  useRefreshCrm,
  type CustomerSummary,
  type RankingComparisonRow,
} from '@/lib/queries/crm'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { SectionScroll, SectionHeader } from '@/components/shared/layout'
import { LoadingState, ErrorState, EmptyState } from '@/components/shared/states'
import * as Icons from 'lucide-react'

const HORIZON_OPTIONS = [30, 60, 90, 120, 180]
const PAGE_SIZE = 25
const ANY = '__any__'
const KNOWN_TRIGGERS = ['manual', 'churn_run', 'scheduled']

/** Sentinel used by the filter Selects. Radix `SelectItem` rejects an empty
 * string value, and the query layer treats '' as "no filter" — so the two are
 * translated here rather than leaking a magic string into either. */
const fromAny = (value: string) => (value === ANY ? '' : value)

function formatMoney(n: number | null | undefined) {
  if (n == null) return '—'
  return `$${n.toLocaleString(undefined, { maximumFractionDigits: 0 })}`
}

function formatNumber(n: number | null | undefined) {
  if (n == null) return '—'
  return n.toLocaleString()
}

function tierVariant(tier: string | null | undefined) {
  return tier === 'High' ? 'danger' : tier === 'Medium' ? 'warning' : tier === 'Low' ? 'success' : 'outline'
}

function initials(row: { display_name: string | null; customer_id: string }) {
  return (row.display_name || row.customer_id).slice(0, 2).toUpperCase()
}

function downloadCsv(rows: CustomerSummary[], filename: string) {
  const headers = [
    'customer_id', 'display_name', 'rfm_segment', 'lifecycle_stage', 'risk_tier',
    'churn_probability', 'monetary', 'predicted_clv', 'value_at_risk',
    'recency_days', 'frequency', 'last_order_date',
  ]
  const csv = [
    headers.join(','),
    ...rows.map((r) => headers.map((h) => JSON.stringify((r as any)[h] ?? '')).join(',')),
  ].join('\n')
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }))
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

export function Crm() {
  const t = useTranslations('crm')
  const projectId = useAppStore((s) => s.activeProjectId)

  const [runChurn, setRunChurn] = React.useState(true)
  const [horizon, setHorizon] = React.useState('90')
  const [segment, setSegment] = React.useState(ANY)
  const [stage, setStage] = React.useState(ANY)
  const [tier, setTier] = React.useState(ANY)
  const [search, setSearch] = React.useState('')
  const [sortBy, setSortBy] = React.useState('value_at_risk')
  const [page, setPage] = React.useState(0)
  const [openCustomer, setOpenCustomer] = React.useState<string | null>(null)

  const portfolio = usePortfolio(projectId)
  const comparison = useRankingComparison(projectId, 10)
  const snapshots = useSnapshots(projectId)
  const refresh = useRefreshCrm(projectId)

  const customers = useCustomers(projectId, {
    segment: fromAny(segment),
    stage: fromAny(stage),
    tier: fromAny(tier),
    search,
    sort_by: sortBy,
    descending: true,
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  })

  // Any change to what is being filtered or sorted invalidates the current page
  // number — page 4 of the old result set is not page 4 of the new one. Done in
  // the handlers rather than an effect: resetting in an effect would render the
  // stale page once before correcting it.
  const filterSetter = <T,>(set: React.Dispatch<React.SetStateAction<T>>) => (value: T) => {
    set(value)
    setPage(0)
  }

  const runRefresh = () =>
    refresh.mutate({ run_churn: runChurn, horizon_days: Number(horizon) })

  const data = portfolio.data
  const snapshot = data?.latest_snapshot

  if (!projectId) {
    return (
      <SectionScroll>
        <SectionHeader
          title={t('header.title')}
          description={t('header.description')}
          icon={<Icons.Contact className="size-4.5" />}
        />
        <Card padding="none">
          <EmptyState
            icon="FolderKanban"
            title={t('noProject.title')}
            description={t('noProject.body')}
          />
        </Card>
      </SectionScroll>
    )
  }

  return (
    <SectionScroll>
      <SectionHeader
        title={t('header.title')}
        description={t('header.description')}
        icon={<Icons.Contact className="size-4.5" />}
        actions={
          data?.available && (
            <Button size="sm" variant="outline" onClick={runRefresh} disabled={refresh.isPending}>
              <Icons.RefreshCw className={cn('size-3.5', refresh.isPending && 'animate-spin')} />{' '}
              {t('refresh.rerun')}
            </Button>
          )
        }
      />

      {/* Refresh controls */}
      <Card padding="default" className="mb-4">
        <div className="flex flex-wrap items-end gap-4">
          <div>
            <label className="text-2xs uppercase tracking-wider text-muted-foreground/70 font-medium">
              {t('refresh.horizon')}
            </label>
            <Select value={horizon} onValueChange={setHorizon}>
              <SelectTrigger className="h-9 w-[140px] mt-1 text-sm"><SelectValue /></SelectTrigger>
              <SelectContent>
                {HORIZON_OPTIONS.map((h) => (
                  <SelectItem key={h} value={String(h)}>{t('refresh.horizonDays', { days: h })}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex items-center gap-2.5 pb-2">
            <Switch id="crm-run-churn" checked={runChurn} onCheckedChange={setRunChurn} />
            <Label htmlFor="crm-run-churn" className="text-sm cursor-pointer">
              {t('refresh.scoreChurn')}
            </Label>
          </div>
          <p className="text-xs text-muted-foreground pb-2 max-w-md">
            {runChurn ? t('refresh.scoreChurnOn') : t('refresh.scoreChurnOff')}
          </p>
          <div className="ms-auto">
            <Button size="sm" onClick={runRefresh} disabled={refresh.isPending}>
              {refresh.isPending
                ? <><Icons.Loader2 className="size-3.5 animate-spin" /> {t('refresh.building')}</>
                : <><Icons.DatabaseZap className="size-3.5" /> {t('refresh.run')}</>}
            </Button>
          </div>
        </div>

        {refresh.data && (
          <div className="mt-3 pt-3 border-t border-border/60 flex flex-wrap items-center gap-2 text-xs">
            <Badge variant={refresh.data.status === 'success' ? 'success' : refresh.data.status === 'partial' ? 'warning' : 'danger'} className="text-2xs">
              {t(`refresh.status.${refresh.data.status}`)}
            </Badge>
            {refresh.data.reason
              ? <span className="text-muted-foreground">{refresh.data.reason}</span>
              : <span className="text-muted-foreground">
                  {t('refresh.result', {
                    customers: refresh.data.customers,
                    date: refresh.data.snapshot_date ?? '—',
                    ms: refresh.data.duration_ms ?? 0,
                  })}
                </span>}
          </div>
        )}
        {refresh.isError && (
          <p className="mt-3 text-sm text-destructive">{(refresh.error as Error)?.message}</p>
        )}
      </Card>

      {portfolio.isLoading && (
        <Card padding="none" className="mb-4">
          <LoadingState label={t('loading.label')} stage={t('loading.stage')} />
        </Card>
      )}

      {portfolio.isError && (
        <Card padding="none" className="mb-4">
          <ErrorState
            body={(portfolio.error as Error)?.message ?? t('errors.portfolioFailed')}
            onRetry={() => portfolio.refetch()}
          />
        </Card>
      )}

      {data && !data.available && (
        <Card padding="none">
          <EmptyState
            icon="Contact"
            title={t('empty.title')}
            description={data.reason || t('empty.body')}
            actionLabel={t('refresh.run')}
            onAction={runRefresh}
          />
        </Card>
      )}

      {data?.available && (
        <>
          {/* Portfolio KPIs */}
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-5">
            <Kpi
              label={t('kpis.customers')}
              value={formatNumber(data.customers)}
              sub={t('kpis.customersSub', { date: data.snapshot_date ?? '—' })}
            />
            <Kpi
              label={t('kpis.bookValue')}
              value={formatMoney(data.total_monetary)}
              sub={t('kpis.bookValueSub', { count: data.customers_with_monetary })}
              icon={<Icons.Wallet className="size-3.5 text-muted-foreground" />}
            />
            <Kpi
              label={t('kpis.valueAtRisk')}
              value={formatMoney(data.total_value_at_risk)}
              tone="warning"
              sub={t('kpis.valueAtRiskSub', { count: data.customers_with_value_at_risk })}
              icon={<Icons.TrendingDown className="size-3.5 text-warning" />}
            />
            <Kpi
              label={t('kpis.avgRisk')}
              value={data.avg_churn_probability != null ? data.avg_churn_probability.toFixed(2) : '—'}
              sub={t('kpis.avgRiskSub', { count: data.customers_scored })}
            />
            <Kpi
              label={t('kpis.snapshots')}
              value={formatNumber(snapshots.data?.length ?? 0)}
              sub={snapshot ? t('kpis.snapshotsSub', { trigger: snapshot.trigger }) : '—'}
              icon={<Icons.History className="size-3.5 text-muted-foreground" />}
            />
          </div>

          <Tabs defaultValue="portfolio">
            <TabsList className="mb-4">
              <TabsTrigger value="portfolio">{t('tabs.portfolio')}</TabsTrigger>
              <TabsTrigger value="customers">{t('tabs.customers')}</TabsTrigger>
              <TabsTrigger value="prioritisation">{t('tabs.prioritisation')}</TabsTrigger>
              <TabsTrigger value="history">{t('tabs.history')}</TabsTrigger>
            </TabsList>

            {/* ── Portfolio ─────────────────────────────────────────────── */}
            <TabsContent value="portfolio">
              <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
                <Distribution
                  title={t('distribution.risk')}
                  subtitle={t('distribution.riskSub')}
                  data={data.by_risk_tier}
                  total={data.customers_scored || data.customers}
                  tone={(key) => (key === 'High' ? 'bg-destructive' : key === 'Medium' ? 'bg-warning' : 'bg-success')}
                  emptyLabel={t('distribution.noRisk')}
                />
                <Distribution
                  title={t('distribution.stage')}
                  subtitle={t('distribution.stageSub')}
                  data={data.by_stage}
                  total={data.customers}
                  tone={() => 'bg-info'}
                  emptyLabel={t('distribution.noStage')}
                />
                <Distribution
                  title={t('distribution.segment')}
                  subtitle={t('distribution.segmentSub')}
                  data={data.by_segment}
                  total={data.customers}
                  tone={() => 'bg-primary'}
                  emptyLabel={t('distribution.noSegment')}
                />
              </div>

              {/* Provenance: which layers actually produced values */}
              <Card padding="default" className="mt-4">
                <h3 className="text-base font-semibold">{t('coverage.title')}</h3>
                <p className="text-xs text-muted-foreground mt-0.5 mb-3">{t('coverage.subtitle')}</p>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                  <Coverage
                    label={t('coverage.observed')}
                    covered={data.customers_with_monetary}
                    total={data.customers}
                    note={t('coverage.observedNote')}
                  />
                  <Coverage
                    label={t('coverage.predictedRisk')}
                    covered={data.customers_scored}
                    total={data.customers}
                    note={t('coverage.predictedRiskNote')}
                  />
                  <Coverage
                    label={t('coverage.predictedClv')}
                    covered={data.customers_with_clv}
                    total={data.customers}
                    note={t('coverage.predictedClvNote')}
                  />
                </div>
                {snapshot?.components && Object.keys(snapshot.components).length > 0 && (
                  <div className="mt-3 pt-3 border-t border-border/60 flex flex-wrap gap-1.5">
                    {Object.entries(snapshot.components).map(([name, value]) => {
                      const ok = typeof value === 'object' && value !== null
                        ? Boolean((value as any).available ?? (value as any).ok)
                        : Boolean(value)
                      const reason = typeof value === 'object' && value !== null ? (value as any).reason : null
                      return (
                        <Badge key={name} variant={ok ? 'success' : 'outline'} className="text-2xs" title={reason || undefined}>
                          {ok ? <Icons.Check className="size-3" /> : <Icons.Minus className="size-3" />} {name}
                        </Badge>
                      )
                    })}
                  </div>
                )}
              </Card>
            </TabsContent>

            {/* ── Customers ─────────────────────────────────────────────── */}
            <TabsContent value="customers">
              <Card padding="default" className="mb-4">
                <div className="flex flex-wrap items-end gap-3">
                  <div className="min-w-[200px] flex-1">
                    <label className="text-2xs uppercase tracking-wider text-muted-foreground/70 font-medium">
                      {t('filters.search')}
                    </label>
                    <Input
                      className="h-9 mt-1 text-sm"
                      placeholder={t('filters.searchPlaceholder')}
                      value={search}
                      onChange={(e) => filterSetter(setSearch)(e.target.value)}
                    />
                  </div>
                  <FilterSelect
                    label={t('filters.tier')}
                    value={tier}
                    onChange={filterSetter(setTier)}
                    anyLabel={t('filters.anyTier')}
                    options={Object.keys(data.by_risk_tier)}
                  />
                  <FilterSelect
                    label={t('filters.stage')}
                    value={stage}
                    onChange={filterSetter(setStage)}
                    anyLabel={t('filters.anyStage')}
                    options={Object.keys(data.by_stage)}
                  />
                  <FilterSelect
                    label={t('filters.segment')}
                    value={segment}
                    onChange={filterSetter(setSegment)}
                    anyLabel={t('filters.anySegment')}
                    options={Object.keys(data.by_segment)}
                  />
                  <div>
                    <label className="text-2xs uppercase tracking-wider text-muted-foreground/70 font-medium">
                      {t('filters.sortBy')}
                    </label>
                    <Select value={sortBy} onValueChange={filterSetter(setSortBy)}>
                      <SelectTrigger className="h-9 w-[170px] mt-1 text-sm"><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="value_at_risk">{t('columns.valueAtRisk')}</SelectItem>
                        <SelectItem value="churn_probability">{t('columns.risk')}</SelectItem>
                        <SelectItem value="monetary">{t('columns.spend')}</SelectItem>
                        <SelectItem value="predicted_clv">{t('columns.clv')}</SelectItem>
                        <SelectItem value="recency_days">{t('columns.recency')}</SelectItem>
                        <SelectItem value="frequency">{t('columns.orders')}</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                </div>
              </Card>

              <Card padding="none" className="overflow-hidden">
                <div className="px-4 py-3 border-b border-border flex items-center justify-between gap-3">
                  <div>
                    <h3 className="text-base font-semibold">{t('table.title')}</h3>
                    <p className="text-xs text-muted-foreground mt-0.5">
                      {t('table.subtitle', { total: customers.data?.total ?? 0 })}
                    </p>
                  </div>
                  <Button
                    size="sm" variant="outline" className="h-7 text-xs"
                    disabled={!customers.data?.customers.length}
                    onClick={() =>
                      downloadCsv(
                        customers.data?.customers ?? [],
                        `crm_customers_${data.snapshot_date}.csv`
                      )
                    }
                  >
                    <Icons.Download className="size-3" /> {t('table.exportCsv')}
                  </Button>
                </div>

                {customers.isLoading && <LoadingState label={t('table.loading')} />}

                {customers.data && customers.data.customers.length === 0 && (
                  <div className="px-4 py-10 text-center text-sm text-muted-foreground">
                    {t('table.noMatches')}
                  </div>
                )}

                {customers.data && customers.data.customers.length > 0 && (
                  <>
                    <div className="overflow-x-auto">
                      <table className="w-full text-sm">
                        <thead className="bg-muted/30 text-2xs uppercase tracking-wider text-muted-foreground/70 font-medium">
                          <tr>
                            <th className="px-4 py-2.5 text-start">{t('columns.customer')}</th>
                            <th className="px-4 py-2.5 text-start">{t('columns.segment')}</th>
                            <th className="px-4 py-2.5 text-start">{t('columns.stage')}</th>
                            <th className="px-4 py-2.5 text-start">{t('columns.tier')}</th>
                            <th className="px-4 py-2.5 text-end">{t('columns.risk')}</th>
                            <th className="px-4 py-2.5 text-end">{t('columns.spend')}</th>
                            <th className="px-4 py-2.5 text-end">{t('columns.valueAtRisk')}</th>
                            <th className="px-4 py-2.5 text-end">{t('columns.recency')}</th>
                          </tr>
                        </thead>
                        <tbody>
                          {customers.data.customers.map((c) => (
                            <tr
                              key={c.customer_id}
                              onClick={() => setOpenCustomer(c.customer_id)}
                              className="border-t border-border/60 hover:bg-accent/20 cursor-pointer"
                            >
                              <td className="px-4 py-2.5">
                                <div className="flex items-center gap-2.5">
                                  <div className="flex size-7 shrink-0 items-center justify-center rounded-full text-2xs font-semibold bg-muted/40 text-muted-foreground">
                                    {initials(c)}
                                  </div>
                                  <div className="min-w-0">
                                    <span className="font-medium block truncate">{c.display_name || c.customer_id}</span>
                                    {c.display_name && (
                                      <code className="text-2xs text-muted-foreground">{c.customer_id}</code>
                                    )}
                                  </div>
                                </div>
                              </td>
                              <td className="px-4 py-2.5 text-muted-foreground">{c.rfm_segment || '—'}</td>
                              <td className="px-4 py-2.5 text-muted-foreground">{c.lifecycle_stage || '—'}</td>
                              <td className="px-4 py-2.5">
                                {c.risk_tier
                                  ? <Badge variant={tierVariant(c.risk_tier)} className="text-2xs">{c.risk_tier}</Badge>
                                  : <span className="text-muted-foreground">—</span>}
                              </td>
                              <td className="px-4 py-2.5 text-end">
                                {c.churn_probability != null ? (
                                  <div className="inline-flex items-center gap-2">
                                    <div className="h-1.5 w-12 rounded-full bg-muted overflow-hidden">
                                      <div
                                        className={cn('h-full rounded-full', c.churn_probability > 0.75 ? 'bg-destructive' : 'bg-warning')}
                                        style={{ width: `${c.churn_probability * 100}%` }}
                                      />
                                    </div>
                                    <span className="tabular-nums text-muted-foreground">{c.churn_probability.toFixed(2)}</span>
                                  </div>
                                ) : <span className="text-muted-foreground">—</span>}
                              </td>
                              <td className="px-4 py-2.5 text-end tabular-nums">{formatMoney(c.monetary)}</td>
                              <td className="px-4 py-2.5 text-end tabular-nums font-medium text-warning">{formatMoney(c.value_at_risk)}</td>
                              <td className="px-4 py-2.5 text-end tabular-nums text-muted-foreground">{formatNumber(c.recency_days)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>

                    <div className="px-4 py-2.5 border-t border-border bg-muted/20 flex items-center justify-between text-xs text-muted-foreground">
                      <span>
                        {t('table.showing', {
                          from: page * PAGE_SIZE + 1,
                          to: Math.min((page + 1) * PAGE_SIZE, customers.data.total),
                          total: customers.data.total,
                        })}
                      </span>
                      <div className="flex items-center gap-1.5">
                        <Button
                          size="sm" variant="outline" className="h-7 text-xs"
                          disabled={page === 0}
                          onClick={() => setPage((p) => Math.max(0, p - 1))}
                        >
                          <Icons.ChevronLeft className="size-3" /> {t('table.previous')}
                        </Button>
                        <Button
                          size="sm" variant="outline" className="h-7 text-xs"
                          disabled={(page + 1) * PAGE_SIZE >= customers.data.total}
                          onClick={() => setPage((p) => p + 1)}
                        >
                          {t('table.next')} <Icons.ChevronRight className="size-3" />
                        </Button>
                      </div>
                    </div>
                  </>
                )}
              </Card>
            </TabsContent>

            {/* ── Prioritisation ────────────────────────────────────────── */}
            <TabsContent value="prioritisation">
              {comparison.isLoading && <Card padding="none"><LoadingState label={t('comparison.loading')} /></Card>}

              {comparison.data && !comparison.data.available && (
                <Card padding="lg">
                  <p className="text-base text-muted-foreground">{comparison.data.reason || t('empty.body')}</p>
                </Card>
              )}

              {comparison.data?.available && (
                <>
                  <Card padding="default" className="mb-4">
                    <div className="flex flex-wrap items-start justify-between gap-4">
                      <div className="max-w-2xl">
                        <h3 className="text-base font-semibold">{t('comparison.title')}</h3>
                        <p className="text-xs text-muted-foreground mt-1 leading-relaxed">
                          {t('comparison.subtitle', { n: comparison.data.top_n })}
                        </p>
                      </div>
                      <div className="flex items-center gap-3">
                        <div className="text-end">
                          <p className="text-2xs uppercase tracking-wider text-muted-foreground/70 font-medium">
                            {t('comparison.uplift')}
                          </p>
                          <p className={cn(
                            'text-xl font-semibold tabular-nums',
                            comparison.data.difference > 0 ? 'text-success' : 'text-muted-foreground'
                          )}>
                            {comparison.data.difference > 0 ? '+' : ''}{formatMoney(comparison.data.difference)}
                          </p>
                        </div>
                        <div className="text-end">
                          <p className="text-2xs uppercase tracking-wider text-muted-foreground/70 font-medium">
                            {t('comparison.overlap')}
                          </p>
                          <p className="text-xl font-semibold tabular-nums">
                            {comparison.data.overlap}/{comparison.data.top_n}
                          </p>
                        </div>
                      </div>
                    </div>
                    <p className="mt-3 pt-3 border-t border-border/60 text-xs text-muted-foreground leading-relaxed">
                      {t('comparison.explainer', {
                        missed: comparison.data.top_n - comparison.data.overlap,
                        basis: comparison.data.value_basis === 'predicted_clv'
                          ? t('comparison.basisClv')
                          : t('comparison.basisMonetary'),
                      })}
                    </p>
                  </Card>

                  <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                    <RankingList
                      title={t('comparison.byRisk')}
                      subtitle={t('comparison.byRiskSub')}
                      covered={comparison.data.value_covered_by_risk}
                      rows={comparison.data.by_risk}
                      otherIds={new Set(comparison.data.by_value_at_risk.map((r) => r.customer_id))}
                      coveredLabel={t('comparison.valueCovered')}
                      onSelect={setOpenCustomer}
                      tone="muted"
                    />
                    <RankingList
                      title={t('comparison.byValue')}
                      subtitle={t('comparison.byValueSub')}
                      covered={comparison.data.value_covered_by_value_at_risk}
                      rows={comparison.data.by_value_at_risk}
                      otherIds={new Set(comparison.data.by_risk.map((r) => r.customer_id))}
                      coveredLabel={t('comparison.valueCovered')}
                      onSelect={setOpenCustomer}
                      tone="success"
                    />
                  </div>
                </>
              )}
            </TabsContent>

            {/* ── History ───────────────────────────────────────────────── */}
            <TabsContent value="history">
              <Card padding="none" className="overflow-hidden">
                <div className="px-4 py-3 border-b border-border">
                  <h3 className="text-base font-semibold">{t('snapshots.title')}</h3>
                  <p className="text-xs text-muted-foreground mt-0.5">{t('snapshots.subtitle')}</p>
                </div>
                {snapshots.isLoading && <LoadingState label={t('snapshots.loading')} />}
                {snapshots.data && snapshots.data.length === 0 && (
                  <div className="px-4 py-10 text-center text-sm text-muted-foreground">{t('snapshots.none')}</div>
                )}
                {snapshots.data && snapshots.data.length > 0 && (
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead className="bg-muted/30 text-2xs uppercase tracking-wider text-muted-foreground/70 font-medium">
                        <tr>
                          <th className="px-4 py-2.5 text-start">{t('snapshots.columns.date')}</th>
                          <th className="px-4 py-2.5 text-start">{t('snapshots.columns.trigger')}</th>
                          <th className="px-4 py-2.5 text-start">{t('snapshots.columns.status')}</th>
                          <th className="px-4 py-2.5 text-end">{t('snapshots.columns.customers')}</th>
                          <th className="px-4 py-2.5 text-end">{t('snapshots.columns.rows')}</th>
                          <th className="px-4 py-2.5 text-end">{t('snapshots.columns.history')}</th>
                          <th className="px-4 py-2.5 text-end">{t('snapshots.columns.duration')}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {snapshots.data.map((s) => (
                          <tr key={s.id} className="border-t border-border/60 hover:bg-accent/20">
                            <td className="px-4 py-2.5 font-medium tabular-nums">{s.snapshot_date}</td>
                            <td className="px-4 py-2.5">
                              <Badge variant="outline" className="text-2xs">
                                {s.trigger === 'churn_run' && <Icons.Zap className="size-3" />}
                                {/* The backend may add trigger kinds this build
                                    has no translation for; showing the raw value
                                    beats crashing the row on a missing key. */}
                                {KNOWN_TRIGGERS.includes(s.trigger)
                                  ? t(`snapshots.triggers.${s.trigger}` as any)
                                  : s.trigger}
                              </Badge>
                            </td>
                            <td className="px-4 py-2.5">
                              <Badge variant={s.status === 'success' ? 'success' : s.status === 'partial' ? 'warning' : 'danger'} className="text-2xs">
                                {s.status}
                              </Badge>
                            </td>
                            <td className="px-4 py-2.5 text-end tabular-nums">{formatNumber(s.customers)}</td>
                            <td className="px-4 py-2.5 text-end tabular-nums text-muted-foreground">{formatNumber(s.row_count)}</td>
                            <td className="px-4 py-2.5 text-end tabular-nums text-muted-foreground">
                              {t('snapshots.days', { days: s.history_days })}
                            </td>
                            <td className="px-4 py-2.5 text-end tabular-nums text-muted-foreground">
                              {s.duration_ms != null ? `${(s.duration_ms / 1000).toFixed(1)}s` : '—'}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Card>
            </TabsContent>
          </Tabs>
        </>
      )}

      <CustomerDialog
        projectId={projectId}
        customerId={openCustomer}
        onClose={() => setOpenCustomer(null)}
      />
    </SectionScroll>
  )
}

/* ── Building blocks ───────────────────────────────────────────────────── */

function Kpi({
  label, value, sub, tone, icon,
}: {
  label: string; value: string; sub?: string; tone?: 'warning'; icon?: React.ReactNode
}) {
  return (
    <Card padding="default">
      <div className="flex items-center justify-between">
        <p className="text-2xs uppercase tracking-wider text-muted-foreground/70 font-medium">{label}</p>
        {icon}
      </div>
      <p className={cn('mt-1 text-xl font-semibold tabular-nums', tone === 'warning' && 'text-warning')}>{value}</p>
      {sub && <p className="text-2xs text-muted-foreground mt-0.5 truncate">{sub}</p>}
    </Card>
  )
}

function FilterSelect({
  label, value, onChange, anyLabel, options,
}: {
  label: string; value: string; onChange: (v: string) => void; anyLabel: string; options: string[]
}) {
  return (
    <div>
      <label className="text-2xs uppercase tracking-wider text-muted-foreground/70 font-medium">{label}</label>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger className="h-9 w-[150px] mt-1 text-sm"><SelectValue /></SelectTrigger>
        <SelectContent>
          <SelectItem value={ANY}>{anyLabel}</SelectItem>
          {options.map((o) => (
            <SelectItem key={o} value={o}>{o}</SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}

function Distribution({
  title, subtitle, data, total, tone, emptyLabel,
}: {
  title: string
  subtitle: string
  data: Record<string, number>
  total: number
  tone: (key: string) => string
  emptyLabel: string
}) {
  const entries = Object.entries(data).sort((a, b) => b[1] - a[1])
  const max = entries.length ? entries[0][1] : 1

  return (
    <Card padding="default">
      <h3 className="text-base font-semibold">{title}</h3>
      <p className="text-xs text-muted-foreground mt-0.5 mb-3">{subtitle}</p>
      {entries.length === 0 ? (
        <p className="text-sm text-muted-foreground">{emptyLabel}</p>
      ) : (
        <div className="space-y-2">
          {entries.map(([key, count]) => (
            <div key={key} className="flex items-center gap-3">
              <span className="w-28 shrink-0 text-xs truncate" title={key}>{key}</span>
              <div className="flex-1 h-2 rounded-full bg-muted/30 overflow-hidden">
                <div className={cn('h-full rounded-full opacity-70', tone(key))} style={{ width: `${(count / max) * 100}%` }} />
              </div>
              <span className="text-2xs tabular-nums text-muted-foreground w-16 text-end">
                {count.toLocaleString()}
                {total ? ` · ${((count / total) * 100).toFixed(0)}%` : ''}
              </span>
            </div>
          ))}
        </div>
      )}
    </Card>
  )
}

function Coverage({ label, covered, total, note }: { label: string; covered: number; total: number; note: string }) {
  const pct = total ? (covered / total) * 100 : 0
  return (
    <div className="rounded-lg border border-border/60 bg-background/40 p-3">
      <p className="text-2xs uppercase tracking-wider text-muted-foreground/70 font-medium">{label}</p>
      <p className="mt-1 text-lg font-semibold tabular-nums">
        {covered.toLocaleString()}
        <span className="text-xs font-normal text-muted-foreground"> / {total.toLocaleString()}</span>
      </p>
      <div className="mt-1.5 h-1.5 rounded-full bg-muted/30 overflow-hidden">
        <div className={cn('h-full rounded-full', pct >= 99 ? 'bg-success' : pct > 0 ? 'bg-warning' : 'bg-muted')} style={{ width: `${pct}%` }} />
      </div>
      <p className="mt-1.5 text-2xs text-muted-foreground leading-relaxed">{note}</p>
    </div>
  )
}

function RankingList({
  title, subtitle, covered, coveredLabel, rows, otherIds, onSelect, tone,
}: {
  title: string
  subtitle: string
  covered: number
  coveredLabel: string
  rows: RankingComparisonRow[]
  /** IDs in the *other* ranking — a row missing from that set is a customer
   * this ordering catches and the other one does not. That contrast is the
   * whole point of showing the two lists side by side. */
  otherIds: Set<string>
  onSelect: (id: string) => void
  tone: 'muted' | 'success'
}) {
  return (
    <Card padding="none" className="overflow-hidden">
      <div className="px-4 py-3 border-b border-border">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h3 className="text-base font-semibold">{title}</h3>
            <p className="text-xs text-muted-foreground mt-0.5">{subtitle}</p>
          </div>
          <div className="text-end shrink-0">
            <p className="text-2xs uppercase tracking-wider text-muted-foreground/70 font-medium">{coveredLabel}</p>
            <p className={cn('text-base font-semibold tabular-nums', tone === 'success' ? 'text-success' : 'text-muted-foreground')}>
              {formatMoney(covered)}
            </p>
          </div>
        </div>
      </div>
      <ul className="divide-y divide-border/60">
        {rows.map((r) => {
          const exclusive = !otherIds.has(r.customer_id)
          return (
            <li
              key={r.customer_id}
              onClick={() => onSelect(r.customer_id)}
              className="px-4 py-2.5 flex items-center gap-3 hover:bg-accent/20 cursor-pointer"
            >
              <span className="w-5 shrink-0 text-2xs tabular-nums text-muted-foreground">{r.rank}</span>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5">
                  <span className="font-medium text-sm truncate">{r.display_name || r.customer_id}</span>
                  {exclusive && (
                    <Badge variant={tone === 'success' ? 'success' : 'outline'} className="text-2xs shrink-0">
                      <Icons.Sparkles className="size-2.5" /> only here
                    </Badge>
                  )}
                </div>
                <p className="text-2xs text-muted-foreground truncate">
                  {r.rfm_segment || '—'} · {r.lifecycle_stage || '—'}
                </p>
              </div>
              <div className="text-end shrink-0">
                <p className="text-sm tabular-nums font-medium">{formatMoney(r.value_at_risk)}</p>
                <p className="text-2xs tabular-nums text-muted-foreground">
                  p={r.churn_probability != null ? r.churn_probability.toFixed(2) : '—'}
                </p>
              </div>
            </li>
          )
        })}
      </ul>
    </Card>
  )
}

/* ── Customer 360 ──────────────────────────────────────────────────────── */

function CustomerDialog({
  projectId, customerId, onClose,
}: {
  projectId: string; customerId: string | null; onClose: () => void
}) {
  const t = useTranslations('crm')
  const detail = useCustomerDetail(projectId, customerId)
  const customer = detail.data?.customer
  const history = detail.data?.history ?? []

  return (
    <Dialog open={Boolean(customerId)} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-3xl max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2.5">
            <Icons.UserCircle className="size-5 text-primary" />
            {customer?.display_name || customerId}
          </DialogTitle>
        </DialogHeader>

        {detail.isLoading && <LoadingState label={t('detail.loading')} />}

        {detail.data && !detail.data.available && (
          <p className="text-sm text-muted-foreground">{detail.data.reason}</p>
        )}

        {customer && (
          <div className="space-y-4">
            {/* Identity + contact. Rendered here and only here: the browser is
                inside the tenant's session, which is as far as PII travels. */}
            <div className="flex flex-wrap items-center gap-2">
              <code className="text-2xs rounded bg-muted/40 px-1.5 py-0.5">{customer.customer_id}</code>
              {customer.risk_tier && (
                <Badge variant={tierVariant(customer.risk_tier)} className="text-2xs">
                  {customer.risk_tier} risk
                </Badge>
              )}
              {customer.rfm_segment && <Badge variant="info" className="text-2xs">{customer.rfm_segment}</Badge>}
              {customer.lifecycle_stage && <Badge variant="outline" className="text-2xs">{customer.lifecycle_stage}</Badge>}
              {Object.entries(customer.contact || {}).map(([key, value]) =>
                value ? (
                  <span key={key} className="inline-flex items-center gap-1 text-2xs text-muted-foreground">
                    <Icons.Dot className="size-3" />{String(value)}
                  </span>
                ) : null
              )}
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <Stat label={t('detail.spend')} value={formatMoney(customer.monetary)} />
              <Stat label={t('detail.orders')} value={formatNumber(customer.frequency)} />
              <Stat label={t('detail.aov')} value={formatMoney(customer.avg_order_value)} />
              <Stat label={t('detail.recency')} value={customer.recency_days != null ? t('detail.daysAgo', { days: customer.recency_days }) : '—'} />
              <Stat label={t('detail.tenure')} value={customer.tenure_days != null ? t('detail.days', { days: customer.tenure_days }) : '—'} />
              <Stat label={t('detail.churnProbability')} value={customer.churn_probability != null ? customer.churn_probability.toFixed(2) : '—'} />
              <Stat label={t('detail.clv')} value={formatMoney(customer.predicted_clv)} />
              <Stat label={t('detail.valueAtRisk')} value={formatMoney(customer.value_at_risk)} tone="warning" />
            </div>

            {/* RFM component scores — why the segment label is what it is */}
            {(customer.r_score != null || customer.f_score != null || customer.m_score != null) && (
              <div>
                <h4 className="text-sm font-semibold mb-2">{t('detail.rfmTitle')}</h4>
                <div className="grid grid-cols-3 gap-3">
                  {[
                    { key: 'R', score: customer.r_score, label: t('detail.recencyScore') },
                    { key: 'F', score: customer.f_score, label: t('detail.frequencyScore') },
                    { key: 'M', score: customer.m_score, label: t('detail.monetaryScore') },
                  ].map((s) => (
                    <div key={s.key} className="rounded-lg border border-border/60 bg-background/40 p-3">
                      <p className="text-2xs uppercase tracking-wider text-muted-foreground/70 font-medium">{s.label}</p>
                      <p className="mt-1 text-lg font-semibold tabular-nums">{s.score ?? '—'}<span className="text-xs font-normal text-muted-foreground">/5</span></p>
                      <div className="mt-1.5 flex gap-0.5">
                        {[1, 2, 3, 4, 5].map((n) => (
                          <span key={n} className={cn('h-1.5 flex-1 rounded-full', (s.score ?? 0) >= n ? 'bg-primary/60' : 'bg-muted/40')} />
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Per-customer SHAP drivers */}
            <div>
              <h4 className="text-sm font-semibold mb-2">{t('detail.driversTitle')}</h4>
              {customer.drivers.length > 0 ? (
                <div className="space-y-1.5">
                  {customer.drivers.map((d) => (
                    <div key={d.feature} className="flex items-center justify-between rounded-md border border-border/60 px-3 py-1.5">
                      <code className="text-2xs text-muted-foreground">{d.feature}</code>
                      <span className={cn(
                        'inline-flex items-center gap-1 text-2xs font-medium',
                        d.direction === 'increases_risk' ? 'text-destructive' : 'text-success'
                      )}>
                        {d.direction === 'increases_risk'
                          ? <Icons.ArrowUp className="size-3" />
                          : <Icons.ArrowDown className="size-3" />}
                        {d.shap_value != null ? d.shap_value.toFixed(3) : '—'}
                      </span>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-xs text-muted-foreground">
                  {customer.explained ? t('detail.noDrivers') : t('detail.notExplained')}
                </p>
              )}
            </div>

            {/* The timeline — the question persistence exists to answer */}
            <div>
              <h4 className="text-sm font-semibold mb-2">{t('detail.timelineTitle')}</h4>
              {history.length > 1 ? (
                <div className="overflow-x-auto rounded-lg border border-border/60">
                  <table className="w-full text-sm">
                    <thead className="bg-muted/30 text-2xs uppercase tracking-wider text-muted-foreground/70 font-medium">
                      <tr>
                        <th className="px-3 py-2 text-start">{t('detail.timelineColumns.date')}</th>
                        <th className="px-3 py-2 text-start">{t('detail.timelineColumns.stage')}</th>
                        <th className="px-3 py-2 text-end">{t('detail.timelineColumns.risk')}</th>
                        <th className="px-3 py-2 text-end">{t('detail.timelineColumns.spend')}</th>
                        <th className="px-3 py-2 text-end">{t('detail.timelineColumns.valueAtRisk')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {history.map((h) => (
                        <tr key={h.snapshot_date} className="border-t border-border/60">
                          <td className="px-3 py-2 tabular-nums">{h.snapshot_date}</td>
                          <td className="px-3 py-2 text-muted-foreground">{h.lifecycle_stage || '—'}</td>
                          <td className="px-3 py-2 text-end tabular-nums">
                            {h.churn_probability != null ? h.churn_probability.toFixed(2) : '—'}
                          </td>
                          <td className="px-3 py-2 text-end tabular-nums">{formatMoney(h.monetary)}</td>
                          <td className="px-3 py-2 text-end tabular-nums">{formatMoney(h.value_at_risk)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="text-xs text-muted-foreground">{t('detail.timelineSingle')}</p>
              )}
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: 'warning' }) {
  return (
    <div className="rounded-lg border border-border/60 bg-background/40 p-2.5">
      <p className="text-2xs uppercase tracking-wider text-muted-foreground/70 font-medium">{label}</p>
      <p className={cn('mt-0.5 text-sm font-semibold tabular-nums', tone === 'warning' && 'text-warning')}>{value}</p>
    </div>
  )
}
