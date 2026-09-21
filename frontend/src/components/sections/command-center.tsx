'use client'

import * as React from 'react'
import { useTranslations } from 'next-intl'
import { useAppStore, type Section } from '@/lib/store'
import { useProjects } from '@/lib/queries/projects'
import { useDashboardSnapshot } from '@/lib/queries/visualization'
import { Card } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { KPICard } from '@/components/shared/kpi-card'
import { LoadingState, ErrorState, EmptyState } from '@/components/shared/states'
import { SectionScroll, SectionHeader } from '@/components/shared/layout'
import * as Icons from 'lucide-react'

const quickLinkIcons: { section: Section; key: string; icon: string }[] = [
  { section: 'data', key: 'data', icon: 'Database' },
  { section: 'visualization', key: 'visualization', icon: 'BarChart3' },
  { section: 'insights', key: 'insights', icon: 'Lightbulb' },
  { section: 'forecasting', key: 'forecasting', icon: 'TrendingUp' },
  { section: 'marketing', key: 'marketing', icon: 'Megaphone' },
  { section: 'churn', key: 'churn', icon: 'ShieldAlert' },
]

export function CommandCenter() {
  const t = useTranslations('commandCenter')
  const quickLinks = quickLinkIcons.map((l) => ({ ...l, label: t(`quickLinks.${l.key}`) }))
  const activeProjectId = useAppStore((s) => s.activeProjectId)
  const setSection = useAppStore((s) => s.setSection)
  const setCommandOpen = useAppStore((s) => s.setCommandOpen)
  const projectsQuery = useProjects()
  const snapshotQuery = useDashboardSnapshot(activeProjectId)

  const activeProject = projectsQuery.data?.find((p) => p.id === activeProjectId)
  const hasNoProjects = projectsQuery.data?.length === 0

  if (hasNoProjects) {
    return (
      <SectionScroll>
        <SectionHeader
          title={t('title')}
          description={t('description')}
          icon={<Icons.LayoutDashboard className="size-4.5" />}
        />
        <Card padding="none">
          <EmptyState
            icon="FolderKanban"
            title={t('emptyTitle')}
            description={t('emptyDescription')}
            actionLabel={t('createProject')}
            onAction={() => setSection('projects')}
            size="lg"
          />
        </Card>
      </SectionScroll>
    )
  }

  return (
    <SectionScroll>
      <SectionHeader
        title={t('title')}
        description={t('description')}
        icon={<Icons.LayoutDashboard className="size-4.5" />}
        actions={
          <>
            <Button variant="outline" size="sm" onClick={() => setCommandOpen(true)}>
              <Icons.Sparkles className="size-3.5" /> {t('askDaas')}
            </Button>
            <Button size="sm" onClick={() => setSection('data')}>
              <Icons.Plus className="size-3.5" /> {t('connectData')}
            </Button>
          </>
        }
      />

      {/* Project status strip */}
      <Card padding="default" className="mb-5">
        <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
          <div className="flex items-center gap-2.5">
            <div className="flex size-9 items-center justify-center rounded-lg bg-gradient-to-br from-primary/30 to-primary/5 text-md font-bold text-primary ring-1 ring-inset ring-primary/25">
              {(activeProject?.name ?? 'P').charAt(0).toUpperCase()}
            </div>
            <div>
              <p className="text-md font-semibold leading-tight">{activeProject?.name ?? t('noProjectSelected')}</p>
              <p className="text-xs text-muted-foreground">
                {snapshotQuery.data ? t('rowsColumns', { rows: snapshotQuery.data.row_count.toLocaleString(), columns: snapshotQuery.data.column_count }) : activeProject?.data_source_mode}
              </p>
            </div>
          </div>
        </div>
      </Card>

      {/* Business Snapshot KPIs */}
      <div className="mb-5">
        <div className="mb-2.5 flex items-center justify-between">
          <h2 className="text-base font-semibold tracking-tight">{t('businessSnapshot')}</h2>
          <span className="text-xs text-muted-foreground">{t('computedFromSavedData')}</span>
        </div>

        {snapshotQuery.isLoading && <Card padding="none"><LoadingState label={t('computingSnapshot')} /></Card>}
        {snapshotQuery.isError && <Card padding="none"><ErrorState body={t('snapshotLoadError')} onRetry={() => snapshotQuery.refetch()} /></Card>}

        {snapshotQuery.data && (
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3">
            {snapshotQuery.data.kpis.map((k) => (
              <KPICard
                key={k.label}
                label={k.label}
                value={k.value}
                trend={(k.direction as 'up' | 'down' | 'flat' | undefined) ?? undefined}
              />
            ))}
          </div>
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Quick links */}
        <Card padding="default" className="lg:col-span-1">
          <h2 className="text-base font-semibold tracking-tight mb-3.5">{t('workspaces')}</h2>
          <div className="grid grid-cols-1 gap-2">
            {quickLinks.map((l) => {
              const Icon = (Icons as any)[l.icon] ?? Icons.Circle
              return (
                <button
                  key={l.section}
                  onClick={() => setSection(l.section)}
                  className="flex items-center gap-2.5 rounded-lg border border-border/60 bg-background/40 px-3 py-2.5 text-start text-sm font-medium hover:bg-accent/30 transition-colors"
                >
                  <Icon className="size-4 text-muted-foreground" />
                  {l.label}
                  <Icons.ChevronRight className="size-3.5 text-muted-foreground/50 ms-auto rtl:-scale-x-100" />
                </button>
              )
            })}
          </div>
        </Card>

        {/* Analyst Copilot promo */}
        <Card padding="default" className="lg:col-span-2 flex flex-col items-start justify-center gap-3">
          <div className="flex items-center gap-1.5 text-2xs uppercase tracking-wider text-primary font-semibold">
            <Icons.Sparkles className="size-3" /> {t('copilotPromo.title')}
          </div>
          <p className="text-sm text-muted-foreground">
            {t('copilotPromo.description')}
          </p>
          <Button size="sm" onClick={() => setSection('copilot')}>
            {t('copilotPromo.openButton')} <Icons.ArrowRight className="size-3.5 rtl:-scale-x-100" />
          </Button>
        </Card>
      </div>
    </SectionScroll>
  )
}
