'use client'

import * as React from 'react'
import ReactMarkdown from 'react-markdown'
import { useTranslations } from 'next-intl'
import { cn } from '@/lib/utils'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { SectionScroll, SectionHeader } from '@/components/shared/layout'
import { EmptyState, LoadingState, ErrorState } from '@/components/shared/states'
import { useMyReports, useReport, type ReportSummary } from '@/lib/queries/reports'
import * as Icons from 'lucide-react'

const TYPE_ICON: Record<string, string> = {
  insights: 'Lightbulb',
  forecast: 'TrendingUp',
  churn: 'ShieldAlert',
  marketing: 'Megaphone',
}

function formatDate(iso: string) {
  return new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

export function Reports() {
  const t = useTranslations('reports')
  const reportsQuery = useMyReports()
  const [search, setSearch] = React.useState('')
  const [type, setType] = React.useState('all')
  const [project, setProject] = React.useState('all')
  const [view, setView] = React.useState<'grid' | 'list'>('grid')
  const [openReportId, setOpenReportId] = React.useState<string | null>(null)

  const reports = reportsQuery.data ?? []
  const projectNames = React.useMemo(
    () => Array.from(new Set(reports.map((r) => r.project_name))),
    [reports]
  )

  const filtered = reports.filter((r) => {
    if (search && !r.title.toLowerCase().includes(search.toLowerCase())) return false
    if (type !== 'all' && r.type !== type) return false
    if (project !== 'all' && r.project_name !== project) return false
    return true
  })

  return (
    <SectionScroll>
      <SectionHeader
        title={t('header.title')}
        description={t('header.description')}
        icon={<Icons.FileText className="size-4.5" />}
        actions={
          <Button size="sm" variant="outline" onClick={() => reportsQuery.refetch()}>
            <Icons.RefreshCw className="size-3.5" /> {t('refresh')}
          </Button>
        }
      />

      {/* Filters */}
      <Card padding="sm" className="mb-4">
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative flex-1 min-w-[200px]">
            <Icons.Search className="absolute start-2.5 top-1/2 -translate-y-1/2 size-3.5 text-muted-foreground" />
            <Input
              placeholder={t('filters.searchPlaceholder')}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="h-9 ps-8 text-sm"
            />
          </div>
          <Select value={type} onValueChange={setType}>
            <SelectTrigger className="h-9 w-[170px] text-sm"><SelectValue placeholder={t('filters.typePlaceholder')} /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">{t('filters.allTypes')}</SelectItem>
              <SelectItem value="insights">{t('types.insights')}</SelectItem>
              <SelectItem value="forecast">{t('types.forecast')}</SelectItem>
              <SelectItem value="marketing">{t('types.marketing')}</SelectItem>
              <SelectItem value="churn">{t('types.churn')}</SelectItem>
            </SelectContent>
          </Select>
          <Select value={project} onValueChange={setProject}>
            <SelectTrigger className="h-9 w-[180px] text-sm"><SelectValue placeholder={t('filters.projectPlaceholder')} /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">{t('filters.allProjects')}</SelectItem>
              {projectNames.map((p) => <SelectItem key={p} value={p}>{p}</SelectItem>)}
            </SelectContent>
          </Select>
          <div className="flex items-center rounded-lg border border-border bg-muted/30 p-0.5">
            <button onClick={() => setView('grid')} className={cn('rounded-md p-1.5', view === 'grid' ? 'bg-background text-foreground' : 'text-muted-foreground')}>
              <Icons.LayoutGrid className="size-3.5" />
            </button>
            <button onClick={() => setView('list')} className={cn('rounded-md p-1.5', view === 'list' ? 'bg-background text-foreground' : 'text-muted-foreground')}>
              <Icons.List className="size-3.5" />
            </button>
          </div>
          <span className="text-xs text-muted-foreground ms-auto">{t('filters.countOf', { filtered: filtered.length, total: reports.length })}</span>
        </div>
      </Card>

      {reportsQuery.isLoading && (
        <Card padding="none"><LoadingState label={t('loading')} /></Card>
      )}

      {reportsQuery.isError && (
        <Card padding="none"><ErrorState body={t('errors.loadFailed')} onRetry={() => reportsQuery.refetch()} /></Card>
      )}

      {reportsQuery.data && filtered.length === 0 && (
        <Card padding="none">
          <EmptyState
            icon="FileText"
            title={reports.length === 0 ? t('empty.noReportsTitle') : t('empty.noMatchTitle')}
            description={
              reports.length === 0
                ? t('empty.noReportsDescription')
                : t('empty.noMatchDescription')
            }
            size="lg"
          />
        </Card>
      )}

      {reportsQuery.data && filtered.length > 0 && (
        view === 'grid' ? (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
            {filtered.map((r) => <ReportCard key={r.id} report={r} onOpen={() => setOpenReportId(r.id)} />)}
          </div>
        ) : (
          <Card padding="none" className="overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-muted/30 text-2xs uppercase tracking-wider text-muted-foreground/70 font-medium">
                <tr>
                  <th className="px-4 py-2.5 text-start">{t('table.report')}</th>
                  <th className="px-4 py-2.5 text-start">{t('table.type')}</th>
                  <th className="px-4 py-2.5 text-start">{t('table.project')}</th>
                  <th className="px-4 py-2.5 text-start">{t('table.saved')}</th>
                  <th className="px-4 py-2.5 text-end w-0"></th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((r) => {
                  const Icon = (Icons as any)[TYPE_ICON[r.type] ?? 'FileText'] ?? Icons.FileText
                  return (
                    <tr key={r.id} onClick={() => setOpenReportId(r.id)} className="border-t border-border/60 hover:bg-accent/20 cursor-pointer group">
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2.5">
                          <div className="flex size-7 items-center justify-center rounded-md bg-muted/40 text-muted-foreground">
                            <Icon className="size-3.5" />
                          </div>
                          <span className="font-medium">{r.title}</span>
                        </div>
                      </td>
                      <td className="px-4 py-3"><Badge variant="outline" className="text-2xs">{t.has(`types.${r.type}`) ? t(`types.${r.type}`) : r.type}</Badge></td>
                      <td className="px-4 py-3 text-muted-foreground">{r.project_name}</td>
                      <td className="px-4 py-3 text-muted-foreground">{formatDate(r.created_at)}</td>
                      <td className="px-4 py-3 text-end"><Icons.ChevronRight className="size-3.5 text-muted-foreground/60 rtl:-scale-x-100" /></td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </Card>
        )
      )}

      <ReportDialog reportId={openReportId} onClose={() => setOpenReportId(null)} />
    </SectionScroll>
  )
}

function ReportCard({ report, onOpen }: { report: ReportSummary; onOpen: () => void }) {
  const t = useTranslations('reports')
  const Icon = (Icons as any)[TYPE_ICON[report.type] ?? 'FileText'] ?? Icons.FileText
  return (
    <Card padding="default" className="group cursor-pointer hover:border-border hover:bg-accent/20 transition-all" onClick={onOpen}>
      <div className="flex items-start gap-3">
        <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted/40 text-muted-foreground">
          <Icon className="size-4.5" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-base font-semibold leading-tight line-clamp-2">{report.title}</p>
          <p className="mt-1 text-xs text-muted-foreground">{t.has(`types.${report.type}`) ? t(`types.${report.type}`) : report.type}</p>
        </div>
      </div>
      <div className="mt-3 flex items-center justify-between gap-2">
        <Badge variant="outline" className="text-2xs">{report.project_name}</Badge>
        <span className="text-2xs text-muted-foreground/70">{formatDate(report.created_at)}</span>
      </div>
    </Card>
  )
}

function ReportDialog({ reportId, onClose }: { reportId: string | null; onClose: () => void }) {
  const t = useTranslations('reports')
  const reportQuery = useReport(reportId)
  return (
    <Dialog open={Boolean(reportId)} onOpenChange={(o) => { if (!o) onClose() }}>
      <DialogContent className="sm:max-w-[720px] max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{reportQuery.data?.title ?? t('dialog.defaultTitle')}</DialogTitle>
        </DialogHeader>
        {reportQuery.isLoading && <LoadingState label={t('dialog.loading')} />}
        {reportQuery.isError && <ErrorState body={t('errors.loadReportFailed')} />}
        {reportQuery.data && (
          <div className="prose prose-sm dark:prose-invert max-w-none text-base leading-relaxed">
            <ReactMarkdown>{reportQuery.data.markdown}</ReactMarkdown>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
