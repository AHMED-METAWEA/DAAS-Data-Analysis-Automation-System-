'use client'

import * as React from 'react'
import { useTranslations } from 'next-intl'
import { cn } from '@/lib/utils'
import { useAppStore } from '@/lib/store'
import { useProjects, useCreateProject, useDeleteProject, type Project } from '@/lib/queries/projects'
import {
  useIngestFiles,
  useIngestGoogleSheet,
  useTestDbLink,
  useIngestDbLink,
  type IngestResponse,
} from '@/lib/queries/ingestion'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { SectionScroll, SectionHeader } from '@/components/shared/layout'
import { EmptyState, LoadingState, ErrorState } from '@/components/shared/states'
import * as Icons from 'lucide-react'

const statusTone: Record<string, 'success' | 'info' | 'warning' | 'danger'> = {
  active: 'success',
  archived: 'warning',
}

export function Projects() {
  const t = useTranslations('projects')
  const sourceLabel = (mode: Project['data_source_mode']) => t(`sourceLabels.${mode}`)
  const { setActiveProjectId, setSection } = useAppStore()
  const [creating, setCreating] = React.useState(false)
  const [view, setView] = React.useState<'grid' | 'list'>('grid')
  const { data: projects, isLoading, isError, error, refetch } = useProjects()

  if (creating) {
    return <CreateProjectFlow onClose={() => setCreating(false)} />
  }

  return (
    <SectionScroll>
      <SectionHeader
        title={t('list.title')}
        description={t('list.description')}
        icon={<Icons.FolderKanban className="size-4.5" />}
        actions={
          <>
            <div className="flex items-center rounded-lg border border-border bg-muted/30 p-0.5">
              <button onClick={() => setView('grid')} className={cn('rounded-md p-1.5', view === 'grid' ? 'bg-background text-foreground' : 'text-muted-foreground')}>
                <Icons.LayoutGrid className="size-3.5" />
              </button>
              <button onClick={() => setView('list')} className={cn('rounded-md p-1.5', view === 'list' ? 'bg-background text-foreground' : 'text-muted-foreground')}>
                <Icons.List className="size-3.5" />
              </button>
            </div>
            <Button size="sm" onClick={() => setCreating(true)}>
              <Icons.Plus className="size-3.5" /> {t('list.newProject')}
            </Button>
          </>
        }
      />

      {isLoading ? (
        <LoadingState label={t('list.loading')} />
      ) : isError ? (
        <ErrorState body={(error as Error)?.message ?? t('list.loadFailed')} onRetry={() => refetch()} />
      ) : !projects || projects.length === 0 ? (
        <Card padding="none">
          <EmptyState
            icon="FolderKanban"
            title={t('list.emptyTitle')}
            description={t('list.emptyDescription')}
            actionLabel={t('list.createProject')}
            onAction={() => setCreating(true)}
            size="lg"
          />
        </Card>
      ) : view === 'grid' ? (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {projects.map((p) => (
            <ProjectCard
              key={p.id}
              project={p}
              onOpen={() => { setActiveProjectId(p.id); setSection('command-center') }}
            />
          ))}
          <button
            onClick={() => setCreating(true)}
            className="flex min-h-[220px] flex-col items-center justify-center rounded-xl border border-dashed border-border/80 bg-card/30 text-muted-foreground transition-colors hover:border-primary/40 hover:text-primary hover:bg-primary/[0.03]"
          >
            <div className="flex size-10 items-center justify-center rounded-lg bg-muted/40 ring-1 ring-inset ring-border mb-3">
              <Icons.Plus className="size-5" />
            </div>
            <p className="text-base font-medium">{t('list.createNewProjectCard')}</p>
            <p className="mt-0.5 text-xs text-muted-foreground/70">{t('list.createNewProjectHint')}</p>
          </button>
        </div>
      ) : (
        <Card padding="none" className="overflow-hidden">
          <table className="w-full text-base">
            <thead className="border-b border-border bg-muted/30">
              <tr className="text-start text-xs uppercase tracking-wider text-muted-foreground">
                <th className="px-4 py-2.5 font-medium">{t('list.table.project')}</th>
                <th className="px-4 py-2.5 font-medium">{t('list.table.source')}</th>
                <th className="px-4 py-2.5 font-medium">{t('list.table.created')}</th>
                <th className="px-4 py-2.5 font-medium">{t('list.table.status')}</th>
                <th className="px-4 py-2.5 font-medium w-0"></th>
              </tr>
            </thead>
            <tbody>
              {projects.map((p) => (
                <tr
                  key={p.id}
                  className="border-b border-border/60 last:border-0 hover:bg-accent/30 cursor-pointer"
                  onClick={() => { setActiveProjectId(p.id); setSection('command-center') }}
                >
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2.5">
                      <div className="flex size-7 items-center justify-center rounded-md bg-gradient-to-br from-primary/30 to-primary/5 text-xs font-bold text-primary ring-1 ring-inset ring-primary/20">
                        {p.name.charAt(0)}
                      </div>
                      <p className="font-medium leading-tight">{p.name}</p>
                    </div>
                  </td>
                  <td className="px-4 py-3 text-sm text-muted-foreground">{sourceLabel(p.data_source_mode)}</td>
                  <td className="px-4 py-3 text-sm text-muted-foreground">{new Date(p.created_at).toLocaleDateString()}</td>
                  <td className="px-4 py-3"><Badge variant="outline" className="text-xs capitalize">{p.status}</Badge></td>
                  <td className="px-4 py-3"><Icons.ChevronRight className="size-3.5 text-muted-foreground/60 rtl:-scale-x-100" /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </SectionScroll>
  )
}

function ProjectCard({ project, onOpen }: { project: Project; onOpen: () => void }) {
  const t = useTranslations('projects')
  const tone = statusTone[project.status] ?? 'info'
  const deleteProject = useDeleteProject()
  return (
    <Card padding="default" className="group relative cursor-pointer hover:border-border hover:bg-accent/20 transition-all">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3 min-w-0">
          <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-primary/30 to-primary/5 text-lg font-bold text-primary ring-1 ring-inset ring-primary/25">
            {project.name.charAt(0)}
          </div>
          <div className="min-w-0">
            <p className="text-md font-semibold leading-tight truncate">{project.name}</p>
            <p className="text-xs text-muted-foreground mt-0.5">{t(`sourceLabels.${project.data_source_mode}`)}</p>
          </div>
        </div>
        <Button
          variant="ghost"
          size="icon"
          className="size-7"
          onClick={(e) => {
            e.stopPropagation()
            if (confirm(t('list.deleteConfirm', { name: project.name }))) {
              deleteProject.mutate(project.id)
            }
          }}
        >
          <Icons.Trash2 className="size-3.5" />
        </Button>
      </div>

      <div className="mt-3 flex flex-wrap gap-1.5">
        <Badge variant={tone === 'success' ? 'success' : tone === 'info' ? 'info' : tone === 'warning' ? 'warning' : 'danger'} className="capitalize">
          {project.status}
        </Badge>
      </div>

      <div className="mt-3 flex items-center justify-between border-t border-border/60 pt-3">
        <p className="text-xs text-muted-foreground">
          {t('list.createdOn', { date: new Date(project.created_at).toLocaleDateString() })}
        </p>
        <Button variant="ghost" size="sm" onClick={onOpen} className="text-sm">
          {t('list.open')} <Icons.ArrowRight className="size-3 rtl:-scale-x-100" />
        </Button>
      </div>
    </Card>
  )
}

// ============= Create Project Flow =============
const stepIcons = [
  { id: 'details', icon: 'FileText' },
  { id: 'source', icon: 'Database' },
  { id: 'configure', icon: 'Settings2' },
  { id: 'review', icon: 'CheckCircle2' },
]

const sourceTypeIcons = [
  { id: 'upload', icon: 'Upload' },
  { id: 'sheets', icon: 'Sheet' },
  { id: 'database', icon: 'Database' },
] as const

type SourceType = (typeof sourceTypeIcons)[number]['id']

export function CreateProjectFlow({ onClose }: { onClose: () => void }) {
  const t = useTranslations('projects')
  const steps = stepIcons.map((s) => ({ ...s, label: t(`create.steps.${s.id}`) }))
  const sourceTypes = sourceTypeIcons.map((s) => ({ ...s, label: t(`create.sourceTypes.${s.id}.label`), desc: t(`create.sourceTypes.${s.id}.desc`) }))
  const [step, setStep] = React.useState(0)
  const [name, setName] = React.useState('')
  const [sourceType, setSourceType] = React.useState<SourceType>('upload')
  const [project, setProject] = React.useState<Project | null>(null)
  const [ingestResult, setIngestResult] = React.useState<IngestResponse | null>(null)
  const [formError, setFormError] = React.useState<string | null>(null)

  const [files, setFiles] = React.useState<File[]>([])
  const fileInput = React.useRef<HTMLInputElement>(null)

  const [sheetUrl, setSheetUrl] = React.useState('')
  const [serviceAccountJson, setServiceAccountJson] = React.useState('')

  const [dbDialect, setDbDialect] = React.useState<'postgresql' | 'mysql' | 'mssql'>('postgresql')
  const [dbHost, setDbHost] = React.useState('')
  const [dbPort, setDbPort] = React.useState('5432')
  const [dbName, setDbName] = React.useState('')
  const [dbUser, setDbUser] = React.useState('')
  const [dbPassword, setDbPassword] = React.useState('')
  const [dbTested, setDbTested] = React.useState(false)

  const { setActiveProjectId, setSection, setPendingIngestion } = useAppStore()
  const createProject = useCreateProject()
  const deleteProject = useDeleteProject()
  const ingestFiles = useIngestFiles(project?.id ?? '')
  const ingestGSheet = useIngestGoogleSheet(project?.id ?? '')
  const testDbLink = useTestDbLink()
  const ingestDbLink = useIngestDbLink(project?.id ?? '')

  const dataSourceMode = sourceType === 'upload' ? 'files' : sourceType === 'sheets' ? 'google_sheet' : 'database'

  const cancel = () => {
    // Don't leave an orphan empty project behind if the user backs out
    // after we've already created it but before ingestion finished.
    if (project && !ingestResult) deleteProject.mutate(project.id)
    onClose()
  }

  const goToConfigure = async () => {
    setFormError(null)
    if (!name.trim()) {
      setFormError(t('create.errors.nameRequired'))
      return
    }
    try {
      const created = await createProject.mutateAsync({ name: name.trim(), data_source_mode: dataSourceMode })
      setProject(created)
      setStep(2)
    } catch (err) {
      setFormError(err instanceof Error ? err.message : t('create.errors.createFailed'))
    }
  }

  const goToReview = async () => {
    if (!project) return
    setFormError(null)
    try {
      let result: IngestResponse
      if (sourceType === 'upload') {
        if (files.length === 0) {
          setFormError(t('create.errors.addAtLeastOneFile'))
          return
        }
        result = await ingestFiles.mutateAsync(files)
      } else if (sourceType === 'sheets') {
        if (!sheetUrl.trim() || !serviceAccountJson.trim()) {
          setFormError(t('create.errors.sheetFieldsRequired'))
          return
        }
        result = await ingestGSheet.mutateAsync({ sheet_url_or_id: sheetUrl.trim(), service_account_json: serviceAccountJson })
      } else {
        if (!dbHost.trim() || !dbName.trim() || !dbUser.trim()) {
          setFormError(t('create.errors.dbFieldsRequired'))
          return
        }
        result = await ingestDbLink.mutateAsync({
          dialect: dbDialect,
          host: dbHost.trim(),
          port: Number(dbPort) || 5432,
          database: dbName.trim(),
          username: dbUser.trim(),
          password: dbPassword,
          save_connection: true,
        })
      }
      setIngestResult(result)
      setPendingIngestion(result)
      setStep(3)
    } catch (err) {
      setFormError(err instanceof Error ? err.message : t('create.errors.connectFailed'))
    }
  }

  const finish = () => {
    if (!project) return
    setActiveProjectId(project.id)
    setSection('data')
    onClose()
  }

  const testDb = async () => {
    setFormError(null)
    setDbTested(false)
    try {
      await testDbLink.mutateAsync({
        dialect: dbDialect,
        host: dbHost.trim(),
        port: Number(dbPort) || 5432,
        database: dbName.trim(),
        username: dbUser.trim(),
        password: dbPassword,
      })
      setDbTested(true)
    } catch (err) {
      setFormError(err instanceof Error ? err.message : t('create.errors.connectionFailed'))
    }
  }

  const configuring = createProject.isPending || ingestFiles.isPending || ingestGSheet.isPending || ingestDbLink.isPending

  return (
    <SectionScroll>
      <SectionHeader
        title={t('create.title')}
        description={t('create.description')}
        icon={<Icons.Plus className="size-4.5" />}
        actions={
          <Button variant="ghost" size="sm" onClick={cancel}>
            <Icons.X className="size-3.5" /> {t('create.cancel')}
          </Button>
        }
      />

      {/* Stepper */}
      <div className="mb-6 flex items-center gap-2 overflow-x-auto no-scrollbar">
        {steps.map((s, i) => {
          const Icon = (Icons as any)[s.icon]
          const isCurrent = i === step
          const isCompleted = i < step
          return (
            <React.Fragment key={s.id}>
              <button
                onClick={() => i < step && setStep(i)}
                className={cn(
                  'flex items-center gap-2 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors shrink-0',
                  isCurrent ? 'bg-primary/12 text-primary ring-1 ring-inset ring-primary/25'
                  : isCompleted ? 'text-foreground hover:bg-accent/40'
                  : 'text-muted-foreground'
                )}
              >
                <span className={cn(
                  'flex size-5 items-center justify-center rounded-md text-xs',
                  isCurrent ? 'bg-primary text-primary-foreground'
                  : isCompleted ? 'bg-success/15 text-success'
                  : 'bg-muted/60 text-muted-foreground'
                )}>
                  {isCompleted ? <Icons.Check className="size-3" /> : i + 1}
                </span>
                {s.label}
              </button>
              {i < steps.length - 1 && <div className="h-px w-6 bg-border shrink-0" />}
            </React.Fragment>
          )
        })}
      </div>

      <Card padding="lg" className="max-w-3xl">
        {formError && (
          <p className="mb-4 rounded-md bg-destructive/10 border border-destructive/30 px-3 py-2 text-sm text-destructive">
            {formError}
          </p>
        )}

        {step === 0 && (
          <div className="space-y-5 animate-fade-up">
            <div>
              <h2 className="text-md font-semibold">{t('create.step0.title')}</h2>
              <p className="mt-1 text-sm text-muted-foreground">{t('create.step0.description')}</p>
            </div>
            <div>
              <Label htmlFor="name" className="text-sm">{t('create.step0.nameLabel')}</Label>
              <Input
                id="name"
                placeholder={t('create.step0.namePlaceholder')}
                className="mt-1.5"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="ghost" onClick={cancel}>{t('create.cancel')}</Button>
              <Button onClick={() => name.trim() ? setStep(1) : setFormError(t('create.errors.nameRequired'))}>
                {t('create.continue')} <Icons.ArrowRight className="size-3.5 rtl:-scale-x-100" />
              </Button>
            </div>
          </div>
        )}

        {step === 1 && (
          <div className="space-y-5 animate-fade-up">
            <div>
              <h2 className="text-md font-semibold">{t('create.step1.title')}</h2>
              <p className="mt-1 text-sm text-muted-foreground">{t('create.step1.description')}</p>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              {sourceTypes.map((s) => {
                const Icon = (Icons as any)[s.icon]
                const active = sourceType === s.id
                return (
                  <button
                    key={s.id}
                    onClick={() => setSourceType(s.id)}
                    className={cn(
                      'text-start rounded-xl border p-4 transition-all',
                      active ? 'border-primary bg-primary/[0.05] ring-1 ring-primary/30' : 'border-border hover:bg-accent/30'
                    )}
                  >
                    <div className={cn('flex size-9 items-center justify-center rounded-lg', active ? 'bg-primary/15 text-primary' : 'bg-muted/40 text-muted-foreground')}>
                      <Icon className="size-4.5" />
                    </div>
                    <p className="mt-2.5 text-base font-semibold">{s.label}</p>
                    <p className="mt-0.5 text-xs text-muted-foreground">{s.desc}</p>
                  </button>
                )
              })}
            </div>
            <div className="flex justify-between gap-2 pt-2">
              <Button variant="ghost" onClick={() => setStep(0)}>{t('create.back')}</Button>
              <Button onClick={goToConfigure} disabled={createProject.isPending}>
                {createProject.isPending ? <><Icons.Loader2 className="size-3.5 animate-spin" /> {t('create.step1.creating')}</> : <>{t('create.continue')} <Icons.ArrowRight className="size-3.5 rtl:-scale-x-100" /></>}
              </Button>
            </div>
          </div>
        )}

        {step === 2 && (
          <div className="space-y-5 animate-fade-up">
            {sourceType === 'upload' && (
              <>
                <div>
                  <h2 className="text-md font-semibold">{t('create.step2.upload.title')}</h2>
                  <p className="mt-1 text-sm text-muted-foreground">{t('create.step2.upload.description')}</p>
                </div>
                <div
                  onClick={() => fileInput.current?.click()}
                  className="rounded-xl border-2 border-dashed border-border bg-muted/20 p-8 text-center cursor-pointer hover:border-primary/40 hover:bg-primary/[0.03] transition-colors"
                >
                  <div className="flex size-12 mx-auto items-center justify-center rounded-xl bg-primary/10 text-primary ring-1 ring-inset ring-primary/20 mb-3">
                    <Icons.UploadCloud className="size-6" />
                  </div>
                  <p className="text-base font-medium">{t('create.step2.upload.dropzoneTitle')}</p>
                  <p className="mt-1 text-xs text-muted-foreground">{t('create.step2.upload.dropzoneHint')}</p>
                  <input
                    ref={fileInput}
                    type="file"
                    multiple
                    className="hidden"
                    onChange={(e) => {
                      const picked = Array.from(e.target.files ?? [])
                      setFiles((prev) => [...prev, ...picked])
                    }}
                  />
                </div>
                {files.length > 0 && (
                  <div className="space-y-2">
                    <p className="text-xs uppercase tracking-wider text-muted-foreground/70 font-medium">{t('create.step2.upload.filesReady', { count: files.length })}</p>
                    {files.map((f, i) => (
                      <div key={i} className="flex items-center gap-3 rounded-lg border border-border/60 bg-background/40 p-3">
                        <div className="flex size-9 items-center justify-center rounded-lg bg-muted/40 text-2xs font-bold text-muted-foreground">
                          {f.name.split('.').pop()?.toUpperCase() ?? 'FILE'}
                        </div>
                        <div className="min-w-0 flex-1">
                          <p className="text-base font-medium truncate">{f.name}</p>
                          <p className="text-xs text-muted-foreground">{t('create.step2.upload.willBeParsed', { size: (f.size / 1024 / 1024).toFixed(1) })}</p>
                        </div>
                        <Button variant="ghost" size="icon" className="size-7" onClick={() => setFiles((prev) => prev.filter((_, idx) => idx !== i))}>
                          <Icons.X className="size-3.5" />
                        </Button>
                      </div>
                    ))}
                  </div>
                )}
                <div className="rounded-lg border border-info/25 bg-info/[0.05] p-3 flex items-start gap-2.5">
                  <Icons.Info className="size-4 text-info shrink-0 mt-0.5" />
                  <p className="text-sm text-muted-foreground leading-relaxed">
                    <span className="text-foreground font-medium">{t('create.step2.upload.arabicDetectionTitle')}</span> {t('create.step2.upload.arabicDetectionBody')}
                  </p>
                </div>
              </>
            )}
            {sourceType === 'sheets' && (
              <>
                <div>
                  <h2 className="text-md font-semibold">{t('create.step2.sheets.title')}</h2>
                  <p className="mt-1 text-sm text-muted-foreground">{t('create.step2.sheets.description')}</p>
                </div>
                <div>
                  <Label className="text-sm">{t('create.step2.sheets.urlLabel')}</Label>
                  <Input
                    placeholder="https://docs.google.com/spreadsheets/d/…"
                    className="mt-1.5"
                    value={sheetUrl}
                    onChange={(e) => setSheetUrl(e.target.value)}
                  />
                </div>
                <div>
                  <Label className="text-sm">{t('create.step2.sheets.keyLabel')}</Label>
                  <textarea
                    placeholder='{"type": "service_account", ...}'
                    className="mt-1.5 w-full rounded-md border border-border bg-background/40 p-2.5 text-sm font-mono min-h-[120px]"
                    value={serviceAccountJson}
                    onChange={(e) => setServiceAccountJson(e.target.value)}
                  />
                  <p className="mt-1.5 text-xs text-muted-foreground">
                    {t.rich('create.step2.sheets.shareHint', { code: (chunks) => <code>{chunks}</code> })}
                  </p>
                </div>
              </>
            )}
            {sourceType === 'database' && (
              <>
                <div>
                  <h2 className="text-md font-semibold">{t('create.step2.database.title')}</h2>
                  <p className="mt-1 text-sm text-muted-foreground">{t('create.step2.database.description')}</p>
                </div>
                <div className="grid grid-cols-3 gap-2">
                  {(['postgresql', 'mysql', 'mssql'] as const).map((d) => (
                    <button
                      key={d}
                      onClick={() => setDbDialect(d)}
                      className={cn('rounded-lg border p-3 text-sm font-medium transition-colors capitalize', dbDialect === d ? 'border-primary bg-primary/[0.05] text-primary' : 'border-border hover:bg-accent/30')}
                    >
                      {d === 'postgresql' ? 'PostgreSQL' : d === 'mysql' ? 'MySQL' : 'MSSQL'}
                    </button>
                  ))}
                </div>
                <div className="grid gap-3">
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <Label className="text-sm">{t('create.step2.database.hostLabel')}</Label>
                      <Input className="mt-1.5" value={dbHost} onChange={(e) => { setDbHost(e.target.value); setDbTested(false) }} placeholder="db.example.com" />
                    </div>
                    <div>
                      <Label className="text-sm">{t('create.step2.database.portLabel')}</Label>
                      <Input className="mt-1.5" value={dbPort} onChange={(e) => { setDbPort(e.target.value); setDbTested(false) }} />
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <Label className="text-sm">{t('create.step2.database.databaseLabel')}</Label>
                      <Input className="mt-1.5" value={dbName} onChange={(e) => { setDbName(e.target.value); setDbTested(false) }} />
                    </div>
                    <div>
                      <Label className="text-sm">{t('create.step2.database.userLabel')}</Label>
                      <Input className="mt-1.5" value={dbUser} onChange={(e) => { setDbUser(e.target.value); setDbTested(false) }} />
                    </div>
                  </div>
                  <div>
                    <Label className="text-sm">{t('create.step2.database.passwordLabel')}</Label>
                    <Input type="password" className="mt-1.5" value={dbPassword} onChange={(e) => { setDbPassword(e.target.value); setDbTested(false) }} />
                  </div>
                </div>
                <Button variant="outline" size="sm" onClick={testDb} disabled={testDbLink.isPending}>
                  {testDbLink.isPending ? <><Icons.Loader2 className="size-3.5 animate-spin" /> {t('create.step2.database.testing')}</> : t('create.step2.database.testConnection')}
                </Button>
                {dbTested && (
                  <div className="rounded-lg border border-success/25 bg-success/[0.05] p-3 flex items-start gap-2.5">
                    <Icons.ShieldCheck className="size-4 text-success shrink-0 mt-0.5" />
                    <p className="text-sm text-muted-foreground leading-relaxed">
                      <span className="text-foreground font-medium">{t('create.step2.database.verifiedTitle')}</span> {t('create.step2.database.verifiedBody')}
                    </p>
                  </div>
                )}
              </>
            )}
            <div className="flex justify-between gap-2 pt-2">
              <Button variant="ghost" onClick={() => setStep(1)}>{t('create.back')}</Button>
              <Button onClick={goToReview} disabled={configuring}>
                {configuring ? <><Icons.Loader2 className="size-3.5 animate-spin" /> {t('create.step2.connecting')}</> : <>{t('create.continue')} <Icons.ArrowRight className="size-3.5 rtl:-scale-x-100" /></>}
              </Button>
            </div>
          </div>
        )}

        {step === 3 && ingestResult && (
          <div className="space-y-5 animate-fade-up">
            <div>
              <h2 className="text-md font-semibold">{t('create.step3.title')}</h2>
              <p className="mt-1 text-sm text-muted-foreground">
                {t('create.step3.tablesDiscovered', { count: ingestResult.tables.length })}
              </p>
            </div>
            <div className="rounded-xl border border-border/60 divide-y divide-border/60">
              <Row label={t('create.step3.projectName')} value={project?.name ?? name} />
              <Row label={t('create.step3.dataSource')} value={t(`sourceLabels.${dataSourceMode}`)} />
              <Row label={t('create.step3.primaryTable')} value={ingestResult.primary_table} />
              <Row
                label={t('create.step3.tablesDiscoveredLabel')}
                value={ingestResult.tables.map((tbl) => t('create.step3.tableRowsFormat', { name: tbl.name, rows: tbl.rows })).join(', ')}
              />
            </div>
            <div className="rounded-lg border border-primary/25 bg-primary/[0.04] p-3.5">
              <div className="flex items-center gap-1.5 text-xs uppercase tracking-wider text-primary font-semibold">
                <Icons.Sparkles className="size-3" /> {t('create.step3.whatsNext')}
              </div>
              <p className="mt-1 text-sm text-foreground/85 leading-relaxed">
                {t('create.step3.whatsNextBody')}
              </p>
            </div>
            <div className="flex justify-between gap-2 pt-2">
              <Button variant="ghost" onClick={() => setStep(2)}>{t('create.back')}</Button>
              <Button onClick={finish}>
                <Icons.Check className="size-3.5" /> {t('create.step3.goToWorkspace')}
              </Button>
            </div>
          </div>
        )}
      </Card>
    </SectionScroll>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-start justify-between gap-4 px-4 py-2.5">
      <span className="text-sm text-muted-foreground">{label}</span>
      <span className="text-sm font-medium text-right">{value}</span>
    </div>
  )
}
