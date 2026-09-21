'use client'

import { cn } from '@/lib/utils'
import type { KpiCard, Chart } from '@/lib/queries/visualization'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { PlotlyChart } from '@/components/shared/plotly-chart'

export function ChartHeader({ title, subtitle, badge }: { title: string; subtitle: string; badge?: string }) {
  return (
    <div className="flex items-center justify-between mb-3">
      <div>
        <h3 className="text-base font-semibold leading-tight">{title}</h3>
        <p className="text-xs text-muted-foreground mt-0.5">{subtitle}</p>
      </div>
      {badge && <Badge variant="outline" className="text-2xs">{badge}</Badge>}
    </div>
  )
}

export function KpiGrid({ kpis }: { kpis: KpiCard[] }) {
  if (kpis.length === 0) return null
  return (
    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3 mb-4">
      {kpis.map((k) => (
        <Card key={k.label} padding="sm">
          <p className="text-2xs uppercase tracking-wider text-muted-foreground/70 font-medium">{k.label}</p>
          <p className="mt-1 text-lg font-semibold tabular-nums">{k.value}</p>
          {k.direction && (
            <p className={cn('mt-0.5 text-xs', k.direction === 'up' ? 'text-success' : k.direction === 'down' ? 'text-destructive' : 'text-muted-foreground')}>
              {k.direction === 'up' ? '↑' : k.direction === 'down' ? '↓' : '→'}
            </p>
          )}
        </Card>
      ))}
    </div>
  )
}

export function ChartGrid({ charts, projectId, computedLabel }: { charts: Chart[]; projectId: string; computedLabel?: string }) {
  if (charts.length === 0) return null
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
      {charts.map((c, i) => (
        <Card key={i} padding="default" className={cn(c.width === 'full' && 'lg:col-span-2')}>
          <ChartHeader title={c.title} subtitle={c.insight} badge={computedLabel} />
          <PlotlyChart figure={c.figure} height={c.width === 'full' ? 380 : 340} projectId={projectId} title={c.title} />
        </Card>
      ))}
    </div>
  )
}
