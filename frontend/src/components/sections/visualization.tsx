'use client'

import * as React from 'react'
import { useTranslations } from 'next-intl'
import { cn } from '@/lib/utils'
import { useAppStore } from '@/lib/store'
import { useDashboard, useAskForChart, useAutoCharts, dashboardExportUrl, type Chart } from '@/lib/queries/visualization'
import { Card } from '@/components/ui/card'
import { Badge, GroundingBadge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { PlotlyChart } from '@/components/shared/plotly-chart'
import { KpiGrid, ChartGrid } from '@/components/shared/dashboard-blocks'
import { SectionScroll, SectionHeader } from '@/components/shared/layout'
import { EmptyState, LoadingState, ErrorState } from '@/components/shared/states'
import * as Icons from 'lucide-react'

export function Visualization() {
  const t = useTranslations('visualization')
  const [mode, setMode] = React.useState<'executive' | 'studio'>('executive')
  const projectId = useAppStore((s) => s.activeProjectId)

  return (
    <SectionScroll>
      <SectionHeader
        title={t('title')}
        description={t('description')}
        icon={<Icons.BarChart3 className="size-4.5" />}
        actions={
          <>
            <div className="flex items-center rounded-lg border border-border bg-muted/30 p-0.5">
              <button onClick={() => setMode('executive')} className={cn('rounded-md px-3 py-1.5 text-sm font-medium transition-colors', mode === 'executive' ? 'bg-background text-foreground' : 'text-muted-foreground')}>
                {t('modes.executive')}
              </button>
              <button onClick={() => setMode('studio')} className={cn('rounded-md px-3 py-1.5 text-sm font-medium transition-colors', mode === 'studio' ? 'bg-background text-foreground' : 'text-muted-foreground')}>
                {t('modes.studio')}
              </button>
            </div>
            {mode === 'executive' && (
              <Button size="sm" variant="outline" asChild>
                <a href={dashboardExportUrl(projectId)} target="_blank" rel="noreferrer">
                  <Icons.Download className="size-3.5" /> {t('export')}
                </a>
              </Button>
            )}
          </>
        }
      />

      {mode === 'executive' ? <ExecutiveDashboard projectId={projectId} /> : <AIChartStudio projectId={projectId} />}
    </SectionScroll>
  )
}

function ExecutiveDashboard({ projectId }: { projectId: string }) {
  const t = useTranslations('visualization')
  const { data, isLoading, isError, error, refetch } = useDashboard(projectId)

  if (isLoading) {
    return <Card padding="none"><LoadingState label={t('executive.loadingLabel')} stage={t('executive.loadingStage')} /></Card>
  }
  if (isError) {
    return <Card padding="none"><ErrorState body={(error as Error)?.message ?? t('executive.errorFallback')} onRetry={() => refetch()} /></Card>
  }
  if (!data || data.charts.length === 0) {
    return (
      <Card padding="none">
        <EmptyState
          icon="BarChart3"
          title={t('executive.emptyTitle')}
          description={t('executive.emptyDescription')}
          size="lg"
        />
      </Card>
    )
  }

  return (
    <div>
      <Card padding="sm" className="mb-4">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="brand" className="text-2xs">{t('executive.liveBadge')}</Badge>
          <span className="text-xs text-muted-foreground">{t('executive.rowsRevenueBasis', { rows: data.row_count.toLocaleString(), revenueLabel: data.revenue_label })}</span>
        </div>
      </Card>

      <KpiGrid kpis={data.kpis} />
      <ChartGrid charts={data.charts} projectId={projectId} computedLabel={t('computed')} />
    </div>
  )
}

// ============= AI CHART STUDIO =============

export function AIChartStudio({ projectId }: { projectId: string }) {
  const t = useTranslations('visualization')
  const samplePrompts = t.raw('studio.samplePrompts') as string[]
  const [prompt, setPrompt] = React.useState('')
  const [history, setHistory] = React.useState<{ prompt: string; figures: Chart['figure'][]; error?: string | null }[]>([])
  const askForChart = useAskForChart(projectId)
  const autoCharts = useAutoCharts(projectId)

  const generate = (p?: string) => {
    const q = p ?? prompt
    if (!q) return
    setPrompt(q)
    askForChart.mutate(q, {
      onSuccess: (res) => setHistory((h) => [{ prompt: q, figures: res.figures, error: res.error }, ...h]),
    })
  }

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
      <div className="space-y-3">
        <Card padding="default">
          <h3 className="text-base font-semibold mb-1">{t('studio.describeTitle')}</h3>
          <p className="text-xs text-muted-foreground mb-3">{t('studio.describeDescription')}</p>
          <div className="rounded-lg border border-border/60 bg-background/40 p-2.5">
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder={t('studio.placeholder')}
              className="w-full bg-transparent resize-none text-base placeholder:text-muted-foreground/60 outline-none min-h-[60px]"
              onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) generate() }}
            />
            <div className="flex items-center justify-between pt-1.5 border-t border-border/60 mt-1.5">
              <span className="text-2xs text-muted-foreground">{t('studio.hint')}</span>
              <Button size="sm" onClick={() => generate()} disabled={!prompt || askForChart.isPending}>
                {askForChart.isPending ? <><Icons.Loader2 className="size-3.5 animate-spin" /> {t('studio.generating')}</> : <><Icons.Sparkles className="size-3.5" /> {t('studio.generate')}</>}
              </Button>
            </div>
          </div>
        </Card>

        <Card padding="default">
          <p className="text-2xs uppercase tracking-wider text-muted-foreground/70 font-semibold mb-2">{t('studio.tryPrompt')}</p>
          <div className="space-y-1.5">
            {samplePrompts.map((p) => (
              <button
                key={p}
                onClick={() => generate(p)}
                className="flex w-full items-center gap-2 rounded-lg border border-border/60 bg-background/40 p-2.5 text-start text-sm hover:bg-accent/30 transition-colors"
              >
                <Icons.MessageSquare className="size-3.5 text-muted-foreground shrink-0" />
                <span className="flex-1">{p}</span>
                <Icons.ArrowRight className="size-3 text-muted-foreground/60 rtl:-scale-x-100" />
              </button>
            ))}
          </div>
        </Card>

        <Card padding="default">
          <Button
            size="sm"
            variant="outline"
            className="w-full"
            disabled={autoCharts.isPending}
            onClick={() =>
              autoCharts.mutate('', {
                onSuccess: (res) =>
                  setHistory((h) => [
                    ...res.charts.map((c) => ({ prompt: c.title, figures: [c.figure] })),
                    ...h,
                  ]),
              })
            }
          >
            {autoCharts.isPending ? <><Icons.Loader2 className="size-3.5 animate-spin" /> {t('studio.autoGenerating')}</> : <>{t('studio.autoGenerate')}</>}
          </Button>
        </Card>
      </div>

      <div className="lg:col-span-2 space-y-3">
        {history.length === 0 && !askForChart.isPending && !autoCharts.isPending && (
          <Card padding="none">
            <EmptyState
              icon="BarChart3"
              title={t('studio.emptyTitle')}
              description={t('studio.emptyDescription')}
              size="lg"
            />
          </Card>
        )}

        {askForChart.isPending && (
          <Card padding="none">
            <LoadingState label={t('studio.loadingLabel')} stage={t('studio.loadingStage')} />
          </Card>
        )}

        {history.map((h, i) => (
          <Card key={i} padding="default" className="animate-fade-up">
            <div className="flex items-start justify-between gap-3 mb-3 pb-3 border-b border-border/60">
              <div className="flex items-start gap-2.5 min-w-0">
                <div className="flex size-7 shrink-0 items-center justify-center rounded-lg bg-primary/12 text-primary ring-1 ring-inset ring-primary/20">
                  <Icons.Sparkles className="size-3.5" />
                </div>
                <div className="min-w-0">
                  <p className="text-2xs uppercase tracking-wider text-muted-foreground/70 font-medium">{t('studio.yourRequest')}</p>
                  <p className="text-sm font-medium mt-0.5">{h.prompt}</p>
                </div>
              </div>
              {!h.error && <GroundingBadge status="grounded" />}
            </div>

            {h.error ? (
              <p className="rounded-md bg-destructive/10 border border-destructive/30 px-3 py-2 text-sm text-destructive font-mono whitespace-pre-wrap">{h.error}</p>
            ) : (
              h.figures.map((fig, fi) => <PlotlyChart key={fi} figure={fig} height={340} projectId={projectId} context={h.prompt} />)
            )}
          </Card>
        ))}
      </div>
    </div>
  )
}
