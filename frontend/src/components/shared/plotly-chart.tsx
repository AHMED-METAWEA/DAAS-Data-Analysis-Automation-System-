'use client'

import * as React from 'react'
import dynamic from 'next/dynamic'
import { useTheme } from 'next-themes'
import { useTranslations } from 'next-intl'
import ReactMarkdown from 'react-markdown'
import * as Icons from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { LoadingState, ErrorState } from '@/components/shared/states'
import { useExplainChart } from '@/lib/queries/charts'

// Plotly touches `window` at import time, so it can only load client-side.
const Plot = dynamic(() => import('react-plotly.js'), { ssr: false })

// Approximate the oklch tokens in globals.css (`:root` / `.dark`) in hex/rgba —
// Plotly.js's color validator doesn't recognize the oklch() function and
// silently drops it back to its own template default, so these can't be the
// literal CSS variable values, just visually equivalent standard-format colors.
const THEME_COLORS = {
  light: {
    ink: '#1F2A3D',
    muted: '#5B6472',
    grid: '#E4E7EC',
    surface: '#FFFFFF',
  },
  dark: {
    ink: '#F5F7FA',
    muted: '#9CA5B4',
    grid: 'rgba(255,255,255,0.1)',
    surface: '#232A35',
  },
} as const

type ThemeColors = { ink: string; muted: string; grid: string; surface: string }

function themedAxis(axis: Record<string, any> | undefined, colors: ThemeColors) {
  return {
    ...axis,
    gridcolor: colors.grid,
    zerolinecolor: colors.grid,
    linecolor: colors.grid,
    tickfont: { ...axis?.tickfont, color: colors.muted },
    title: typeof axis?.title === 'object' ? { ...axis.title, font: { ...axis.title?.font, color: colors.muted } } : axis?.title,
  }
}

export function PlotlyChart({
  figure,
  height = 360,
  className,
  projectId,
  title,
  context,
}: {
  figure: { data: any[]; layout: Record<string, any> }
  height?: number
  className?: string
  /** Presence gates whether the "explain this chart" button renders at all. */
  projectId?: string
  /** Optional richer prompt context for the explanation, when the caller has it. */
  title?: string
  context?: string
}) {
  const t = useTranslations('common')
  const { resolvedTheme } = useTheme()
  const colors = THEME_COLORS[resolvedTheme === 'light' ? 'light' : 'dark']
  const [open, setOpen] = React.useState(false)
  const explain = useExplainChart(projectId ?? '')

  const handleOpenChange = (next: boolean) => {
    setOpen(next)
    if (next && !explain.data && !explain.isPending) {
      explain.mutate({ figure, title, context })
    }
  }

  const themedLayout: Record<string, any> = {
    ...figure.layout,
    paper_bgcolor: 'transparent',
    plot_bgcolor: 'transparent',
    font: { ...figure.layout?.font, color: colors.ink },
    // The backend's registered template sets `template.layout.title.font.color`
    // for a white canvas; that specific override beats the general `font.color`
    // above unless we also set it explicitly here. (The subtitle span the
    // backend appends below the title has its color baked into inline HTML and
    // can't be themed this way — its fixed muted-gray reads acceptably on both
    // a light and a dark card, so it's left as-is.)
    title: typeof figure.layout?.title === 'object'
      ? { ...figure.layout.title, font: { ...figure.layout.title?.font, color: colors.ink } }
      : figure.layout?.title,
    legend: { ...figure.layout?.legend, font: { ...figure.layout?.legend?.font, color: colors.muted } },
    hoverlabel: {
      ...figure.layout?.hoverlabel,
      bgcolor: colors.surface,
      bordercolor: colors.grid,
      font: { ...figure.layout?.hoverlabel?.font, color: colors.ink },
    },
    autosize: true,
    height,
    margin: figure.layout?.margin ?? { t: 40, r: 16, b: 40, l: 48 },
    // Set unconditionally (not just when already present): the backend's
    // registered Plotly template resolves axis colors for a white canvas at
    // serialization time and lives under `layout.template.layout.xaxis`, not
    // `layout.xaxis` — an explicit top-level key here is required to win.
    xaxis: themedAxis(figure.layout?.xaxis, colors),
    yaxis: themedAxis(figure.layout?.yaxis, colors),
  }

  for (const key of Object.keys(figure.layout ?? {})) {
    if (/^(x|y)axis\d+$/.test(key)) {
      themedLayout[key] = themedAxis(figure.layout[key], colors)
    }
  }

  return (
    <div className="relative">
      {projectId && (
        <Button
          variant="ghost"
          size="icon"
          className="absolute top-2 left-1/2 -translate-x-1/2 z-10 size-7 bg-background/70 backdrop-blur-sm hover:bg-background"
          onClick={() => handleOpenChange(true)}
          title={t('explainChart')}
        >
          <Icons.Sparkles className="size-3.5" />
          <span className="sr-only">{t('explainChart')}</span>
        </Button>
      )}
      <Plot
        data={figure.data}
        layout={themedLayout}
        config={{ displaylogo: false, responsive: true, modeBarButtonsToRemove: ['sendDataToCloud'] }}
        style={{ width: '100%', height: `${height}px` }}
        className={className}
        useResizeHandler
      />
      {projectId && (
        <Dialog open={open} onOpenChange={handleOpenChange}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>{t('explainChartTitle')}</DialogTitle>
            </DialogHeader>
            {explain.isPending && <LoadingState label={t('explaining')} />}
            {explain.isError && (
              <ErrorState body={t('explainError')} onRetry={() => explain.mutate({ figure, title, context })} />
            )}
            {explain.data && (
              <div className="prose prose-sm dark:prose-invert max-w-none text-base">
                <ReactMarkdown>{explain.data.explanation}</ReactMarkdown>
              </div>
            )}
          </DialogContent>
        </Dialog>
      )}
    </div>
  )
}
