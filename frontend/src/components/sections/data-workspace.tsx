'use client'

import * as React from 'react'
import { useTranslations } from 'next-intl'
import { cn } from '@/lib/utils'
import { useAppStore } from '@/lib/store'
import {
  useSchemaDiscovery,
  useSubmitRelationships,
  usePlanTable,
  useEditPlan,
  useCleanTable,
  useCleanRemaining,
  usePlanStepOpinion,
  useIntegrity,
  useSaveToProject,
  decisionKey,
  type SchemaDiscoveryResult,
  type RelationshipCandidate,
  type RelationshipDecision,
  type PlanResult,
  type CleaningPlanStep,
  type CleanResult,
  type CleanRemainingResult,
  type IntegrityResult,
  type SaveResult,
} from '@/lib/queries/pipeline'
import dynamic from 'next/dynamic'
import { useToast } from '@/hooks/use-toast'
import { Card } from '@/components/ui/card'
import { Badge, ConfidenceBadge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { PipelineProgress } from '@/components/shared/pipeline-progress'
import { ContextPanel } from '@/components/shared/layout'
import { AIInsightCallout } from '@/components/shared/ai-response-card'
import { EmptyState, LoadingState } from '@/components/shared/states'
import * as Icons from 'lucide-react'

// react-query mutations fail silently by default (no UI change at all) unless
// each call site wires onError explicitly. This pulls out the backend's own
// detail message (e.g. "Pipeline session not found or expired") so it can be
// shown as the toast description instead of just a generic title.
function describeError(err: unknown): string | undefined {
  return err instanceof Error && err.message ? err.message : undefined
}

type Stage = 'source' | 'profile' | 'discover' | 'relationships' | 'clean' | 'validate' | 'reconcile' | 'save'

const pipelineStageIcons: { id: Stage; icon: string }[] = [
  { id: 'source', icon: 'Database' },
  { id: 'profile', icon: 'BarChart3' },
  { id: 'discover', icon: 'Network' },
  { id: 'relationships', icon: 'GitBranch' },
  { id: 'clean', icon: 'Sparkles' },
  { id: 'validate', icon: 'CheckCircle2' },
  { id: 'reconcile', icon: 'Scale' },
  { id: 'save', icon: 'Save' },
]

const RelationshipErdView = dynamic(
  () => import('@/components/sections/relationship-erd').then((m) => m.RelationshipErdView),
  { ssr: false }
)

export function DataWorkspace() {
  const t = useTranslations('dataWorkspace')
  const pipelineStages = pipelineStageIcons.map((s) => ({ ...s, label: t(`stages.${s.id}`) }))
  const pendingIngestion = useAppStore((s) => s.pendingIngestion)
  const setSection = useAppStore((s) => s.setSection)
  const sessionId = pendingIngestion?.pipeline_session_id ?? ''
  const primaryTable = pendingIngestion?.primary_table ?? ''

  const [stage, setStage] = React.useState<Stage>('source')
  const [activeTable, setActiveTable] = React.useState(primaryTable)
  const [completed, setCompleted] = React.useState<Stage[]>([])

  const [schemaResult, setSchemaResult] = React.useState<SchemaDiscoveryResult | null>(null)
  const [decisions, setDecisions] = React.useState<Record<string, RelationshipDecision>>({})
  const [manualRelationships, setManualRelationships] = React.useState<RelationshipCandidate[]>([])
  const [relationshipsSubmitted, setRelationshipsSubmitted] = React.useState(false)

  // Plan/edit/clean state is keyed per table so every uploaded table is
  // independently reviewable — not just the primary one — and switching
  // tables in the sidebar doesn't lose or mix up another table's state.
  const [plansByTable, setPlansByTable] = React.useState<Record<string, PlanResult>>({})
  const [editedPlansByTable, setEditedPlansByTable] = React.useState<Record<string, CleaningPlanStep[]>>({})
  const [cleanResultsByTable, setCleanResultsByTable] = React.useState<Record<string, CleanResult>>({})
  const plan = plansByTable[activeTable] ?? null
  const setPlan = React.useCallback((p: PlanResult | null) => setPlansByTable((prev) => {
    const next = { ...prev }
    if (p) next[activeTable] = p; else delete next[activeTable]
    return next
  }), [activeTable])
  const editedPlan = editedPlansByTable[activeTable] ?? null
  const setEditedPlan = React.useCallback((steps: CleaningPlanStep[]) => setEditedPlansByTable((prev) => ({
    ...prev, [activeTable]: steps,
  })), [activeTable])
  const cleanResult = cleanResultsByTable[activeTable] ?? null
  const setCleanResult = React.useCallback((r: CleanResult | null) => setCleanResultsByTable((prev) => {
    const next = { ...prev }
    if (r) next[activeTable] = r; else delete next[activeTable]
    return next
  }), [activeTable])

  const [remainingResult, setRemainingResult] = React.useState<CleanRemainingResult | null>(null)
  const [integrityResult, setIntegrityResult] = React.useState<IntegrityResult | null>(null)
  const [saveResult, setSaveResult] = React.useState<SaveResult | null>(null)

  const schemaDiscovery = useSchemaDiscovery(sessionId)
  const submitRelationships = useSubmitRelationships(sessionId)
  const planTable = usePlanTable(sessionId, activeTable)
  const editPlan = useEditPlan(sessionId, activeTable)
  const cleanTable = useCleanTable(sessionId, activeTable)
  const planStepOpinion = usePlanStepOpinion(sessionId, activeTable)
  const cleanRemaining = useCleanRemaining(sessionId)
  const integrity = useIntegrity(sessionId)
  const save = useSaveToProject(sessionId)

  const markDone = (s: Stage) => setCompleted((prev) => (prev.includes(s) ? prev : [...prev, s]))

  if (!pendingIngestion) {
    return (
      <div className="flex h-full items-center justify-center">
        <Card padding="none" className="max-w-md">
          <EmptyState
            icon="Database"
            title={t('empty.title')}
            description={t('empty.description')}
            actionLabel={t('empty.action')}
            onAction={() => setSection('projects')}
            size="lg"
          />
        </Card>
      </div>
    )
  }

  const ctx = {
    pendingIngestion, sessionId, primaryTable, activeTable, setActiveTable,
    schemaResult, setSchemaResult, schemaDiscovery,
    decisions, setDecisions, manualRelationships, setManualRelationships,
    relationshipsSubmitted, setRelationshipsSubmitted, submitRelationships,
    plan, setPlan, editedPlan, setEditedPlan, planTable, editPlan, planStepOpinion,
    cleanResult, setCleanResult, cleanTable,
    remainingResult, setRemainingResult, cleanRemaining,
    integrityResult, setIntegrityResult, integrity,
    saveResult, setSaveResult, save,
    markDone, setStage,
  }

  return (
    <div className="flex h-full">
      <div className="flex-1 flex flex-col min-w-0">
        <div className="border-b border-border bg-background/60 px-6 py-3 backdrop-blur">
          <div className="flex items-center justify-between gap-3 mb-2.5">
            <div>
              <h1 className="text-lg font-semibold tracking-tight leading-tight">{t('header.title')}</h1>
              <p className="text-sm text-muted-foreground">
                {t('header.tablesCount', { count: pendingIngestion.tables.length, primary: primaryTable })}
              </p>
            </div>
            {saveResult?.saved && <Badge variant="success"><Icons.Check className="size-3" /> {t('header.savedBadge')}</Badge>}
          </div>
          <PipelineProgress
            stages={pipelineStages}
            current={stage}
            completed={completed}
            onStageClick={(s) => setStage(s as Stage)}
          />
        </div>

        <div className="flex flex-1 min-h-0">
          <aside className="hidden lg:flex w-[220px] shrink-0 flex-col border-r border-border bg-card/20">
            <div className="px-3 pt-3 pb-2">
              <p className="text-xs uppercase tracking-wider text-muted-foreground/70 font-semibold">{t('sidebar.tablesLabel')}</p>
            </div>
            <div className="flex-1 overflow-y-auto px-2 pb-3 space-y-0.5">
              {pendingIngestion.tables.map((tbl) => (
                <button
                  key={tbl.name}
                  onClick={() => setActiveTable(tbl.name)}
                  className={cn(
                    'flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-xs transition-colors text-start',
                    activeTable === tbl.name ? 'bg-primary/12 text-primary' : 'text-muted-foreground hover:bg-accent/40 hover:text-foreground'
                  )}
                >
                  <Icons.Table className="size-3 shrink-0" />
                  <span className="flex-1 truncate">{tbl.name}</span>
                  {tbl.name === primaryTable && <Badge variant="outline" className="text-2xs px-1 py-0">{t('sidebar.primaryBadge')}</Badge>}
                </button>
              ))}
            </div>
          </aside>

          <div className="flex-1 min-w-0 overflow-y-auto bg-background">
            <StageContent stage={stage} ctx={ctx} />
          </div>

          <ContextPanel>
            <ContextContent stage={stage} ctx={ctx} />
          </ContextPanel>
        </div>
      </div>
    </div>
  )
}

// ============= STAGE CONTENT =============
function StageContent({ stage, ctx }: { stage: Stage; ctx: any }) {
  if (stage === 'source') return <SourceStage ctx={ctx} />
  if (stage === 'profile') return <ProfileStage ctx={ctx} />
  if (stage === 'discover') return <DiscoverStage ctx={ctx} />
  if (stage === 'relationships') return <RelationshipsStage ctx={ctx} />
  if (stage === 'clean') return <CleanStage ctx={ctx} />
  if (stage === 'validate') return <ValidateStage ctx={ctx} />
  if (stage === 'reconcile') return <ReconcileStage ctx={ctx} />
  if (stage === 'save') return <SaveStage ctx={ctx} />
  return null
}

function StageShell({ title, description, children, actions }: { title: string; description: string; children: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <div className="px-6 py-5 max-w-[1400px]">
      <div className="mb-5 flex items-end justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">{title}</h2>
          <p className="mt-0.5 text-sm text-muted-foreground">{description}</p>
        </div>
        {actions}
      </div>
      {children}
    </div>
  )
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: 'warning' | 'danger' | 'success' }) {
  return (
    <Card padding="sm">
      <p className="text-2xs uppercase tracking-wider text-muted-foreground/70 font-medium">{label}</p>
      <p className={cn(
        'mt-1 text-lg font-semibold tabular-nums',
        tone === 'warning' && 'text-warning',
        tone === 'danger' && 'text-destructive',
        tone === 'success' && 'text-success',
      )}>{value}</p>
    </Card>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-4 px-4 py-2.5 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium">{value}</span>
    </div>
  )
}

// ============= SOURCE =============
function SourceStage({ ctx }: { ctx: any }) {
  const t = useTranslations('dataWorkspace')
  const { pendingIngestion, setStage } = ctx
  return (
    <StageShell
      title={t('source.title')}
      description={t('source.description')}
      actions={<Button size="sm" onClick={() => setStage('discover')}><Icons.ArrowRight className="size-3.5 rtl:-scale-x-100" /> {t('source.discoverButton')}</Button>}
    >
      <Card padding="default">
        <h3 className="text-base font-semibold mb-3">{t('source.detectedTitle')}</h3>
        <div className="space-y-2">
          {pendingIngestion.tables.map((tbl: any) => (
            <div key={tbl.name} className="flex items-center gap-3 rounded-lg border border-border/60 bg-background/40 p-3">
              <div className="flex size-9 items-center justify-center rounded-lg bg-muted/40 text-muted-foreground">
                <Icons.Table className="size-4" />
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <p className="text-base font-medium truncate">{tbl.name}</p>
                  {tbl.name === pendingIngestion.primary_table && <Badge variant="brand" className="text-2xs">{t('source.primaryBadge')}</Badge>}
                </div>
                <p className="text-xs text-muted-foreground">{t('common.rowsColumns', { rows: tbl.rows, columns: tbl.columns.length })}</p>
              </div>
              <Badge variant="success" className="text-2xs">{t('source.parsedBadge')}</Badge>
            </div>
          ))}
        </div>
      </Card>
    </StageShell>
  )
}

// ============= PROFILE =============
function ProfileStage({ ctx }: { ctx: any }) {
  const t = useTranslations('dataWorkspace')
  const { toast } = useToast()
  const { activeTable, plan, planTable, setPlan } = ctx

  React.useEffect(() => {
    if (!plan && !planTable.isPending) {
      planTable.mutate(undefined, {
        onSuccess: (res: PlanResult) => setPlan(res),
        onError: (err: unknown) => toast({ title: t('errors.planFailed'), description: describeError(err), variant: 'destructive' }),
      })
    }
  }, [activeTable, plan, planTable, setPlan, toast, t])

  if (planTable.isPending) {
    return <StageShell title={t('profile.title')} description={t('profile.profilingDescription')}><LoadingState label={t('profile.analyzingLabel')} /></StageShell>
  }

  if (!plan) {
    return (
      <StageShell title={t('profile.title')} description={t('profile.statisticalProfileOf', { table: activeTable })}>
        <Card padding="default"><p className="text-sm text-muted-foreground">{t('profile.notRunYet')}</p></Card>
      </StageShell>
    )
  }

  const profile = plan.profile
  const columns: any[] = profile.columns ?? []

  return (
    <StageShell
      title={t('profile.title')}
      description={t('profile.statisticalProfileComputed', { table: activeTable })}
    >
      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-5 gap-3 mb-5">
        <Metric label={t('profile.metrics.rows')} value={String(profile.total_rows ?? '—')} />
        <Metric label={t('profile.metrics.columns')} value={String(profile.total_cols ?? '—')} />
        <Metric label={t('profile.metrics.duplicateRows')} value={String(profile.duplicate_rows_count ?? 0)} tone={profile.duplicate_rows_count ? 'warning' : 'success'} />
        <Metric label={t('profile.metrics.missingTotal')} value={String(columns.reduce((n, c) => n + (c.missing_count ?? 0), 0))} tone="warning" />
        <Metric label={t('profile.metrics.typeMismatches')} value={String(columns.filter((c) => c.type_mismatch_flag).length)} tone="warning" />
      </div>

      <Card padding="none" className="overflow-hidden">
        <div className="px-4 py-3 border-b border-border">
          <h3 className="text-base font-semibold">{t('profile.columnProfileTitle', { table: activeTable })}</h3>
        </div>
        <table className="w-full text-sm">
          <thead className="bg-muted/30 text-xs uppercase tracking-wider text-muted-foreground">
            <tr>
              <th className="px-4 py-2 text-start font-medium">{t('profile.table.column')}</th>
              <th className="px-4 py-2 text-start font-medium">{t('profile.table.type')}</th>
              <th className="px-4 py-2 text-start font-medium">{t('profile.table.inferredRole')}</th>
              <th className="px-4 py-2 text-end font-medium">{t('profile.table.missing')}</th>
              <th className="px-4 py-2 text-end font-medium">{t('profile.table.unique')}</th>
              <th className="px-4 py-2 text-start font-medium">{t('profile.table.sample')}</th>
            </tr>
          </thead>
          <tbody>
            {columns.map((c) => (
              <tr key={c.name} className="border-t border-border/60 hover:bg-accent/20">
                <td className="px-4 py-2.5 font-medium font-mono text-xs">{c.name}</td>
                <td className="px-4 py-2.5 text-muted-foreground">{c.dtype}</td>
                <td className="px-4 py-2.5">
                  <Badge variant={c.is_relationship_key ? 'info' : 'outline'} className="text-2xs">
                    {c.is_relationship_key ? t('profile.relationshipKeyBadge') : c.inferred_type}
                  </Badge>
                </td>
                <td className="px-4 py-2.5 text-end tabular-nums">
                  {c.missing_count > 0 ? <span className="text-warning">{c.missing_count}</span> : <span className="text-muted-foreground">0</span>}
                </td>
                <td className="px-4 py-2.5 text-end tabular-nums text-muted-foreground">{c.unique_count?.toLocaleString?.() ?? c.unique_count}</td>
                <td className="px-4 py-2.5 font-mono text-xs text-muted-foreground truncate max-w-[180px]">
                  {(c.sample_values ?? []).slice(0, 3).join(', ')}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </StageShell>
  )
}

// ============= DISCOVER =============
function DiscoverStage({ ctx }: { ctx: any }) {
  const t = useTranslations('dataWorkspace')
  const { toast } = useToast()
  const { schemaResult, setSchemaResult, schemaDiscovery, setStage } = ctx

  if (!schemaResult) {
    return (
      <StageShell title={t('discover.title')} description={t('discover.idleDescription')}>
        {schemaDiscovery.isPending ? (
          <LoadingState label={t('discover.scanningLabel')} stage={t('discover.scanningStage')} />
        ) : (
          <Card padding="default">
            <p className="text-sm text-muted-foreground mb-3">{t('discover.runPrompt')}</p>
            <Button
              size="sm"
              onClick={() => schemaDiscovery.mutate(undefined, {
                onSuccess: (res: SchemaDiscoveryResult) => setSchemaResult(res),
                onError: (err: unknown) => toast({ title: t('errors.discoverFailed'), description: describeError(err), variant: 'destructive' }),
              })}
            >
              <Icons.Network className="size-3.5" /> {t('discover.runButton')}
            </Button>
          </Card>
        )}
      </StageShell>
    )
  }

  const bySource: Record<string, number> = {}
  for (const c of [...schemaResult.auto, ...schemaResult.review, ...schemaResult.manual]) {
    bySource[c.source] = (bySource[c.source] ?? 0) + 1
  }

  return (
    <StageShell
      title={t('discover.title')}
      description={t('discover.resultDescription')}
      actions={<Button size="sm" onClick={() => setStage('relationships')}><Icons.ArrowRight className="size-3.5 rtl:-scale-x-100" /> {t('discover.reviewButton')}</Button>}
    >
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card padding="default">
          <h3 className="text-base font-semibold mb-3">{t('discover.tablesScannedTitle')}</h3>
          <div className="space-y-2">
            {schemaResult.tables.map((tbl) => (
              <div key={tbl.name} className="flex items-center gap-3 rounded-lg border border-border/60 p-2.5">
                <Icons.Table2 className="size-4 text-muted-foreground shrink-0" />
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium">{tbl.name}</p>
                  <p className="text-2xs text-muted-foreground">{t('common.rowsColumns', { rows: tbl.row_count, columns: tbl.column_count })}</p>
                </div>
              </div>
            ))}
          </div>
        </Card>
        <Card padding="default">
          <h3 className="text-base font-semibold mb-3">{t('discover.candidatesByMethodTitle')}</h3>
          <div className="space-y-3">
            {Object.entries(bySource).map(([source, count]) => (
              <div key={source} className="flex items-center gap-3">
                <div className={cn('flex size-7 items-center justify-center rounded-lg bg-muted/40', source === 'llm' ? 'text-primary' : source === 'database' ? 'text-success' : 'text-info')}>
                  {source === 'llm' ? <Icons.Sparkles className="size-3.5" /> : source === 'database' ? <Icons.ShieldCheck className="size-3.5" /> : <Icons.Cpu className="size-3.5" />}
                </div>
                <p className="text-sm flex-1 capitalize">{source}</p>
                <Badge variant="outline" className="text-2xs">{t('discover.relationCount', { count })}</Badge>
              </div>
            ))}
            {Object.keys(bySource).length === 0 && (
              <p className="text-sm text-muted-foreground">{t('discover.noCandidates')}</p>
            )}
          </div>
          {schemaResult.manual.length > 0 && (
            <AIInsightCallout
              label={t('discover.manualReviewLabel')}
              body={t('discover.manualReviewBody', { count: schemaResult.manual.length })}
              className="mt-4"
            />
          )}
        </Card>
      </div>
    </StageShell>
  )
}

// ============= RELATIONSHIPS =============
function RelationshipsStage({ ctx }: { ctx: any }) {
  const t = useTranslations('dataWorkspace')
  const { toast } = useToast()
  const {
    primaryTable, schemaResult, decisions, setDecisions, manualRelationships, setManualRelationships,
    relationshipsSubmitted, setRelationshipsSubmitted, submitRelationships, setStage,
  } = ctx

  // Seed default decisions: auto + review candidates default to approved
  // (per the confidence-gated review contract — see relationships/review.py);
  // manual candidates require an explicit choice. Seeds exactly once per
  // schemaResult via a ref flag — NOT a `decisions`-is-empty check, which
  // infinite-loops whenever there are zero candidates (any single-table
  // project): `seeded` stays `{}`, `setDecisions({})` never satisfies
  // "decisions is non-empty", so the effect re-fires forever.
  const seededRef = React.useRef(false)
  React.useEffect(() => {
    seededRef.current = false
    setManualRelationships([])
  }, [schemaResult, setManualRelationships])
  React.useEffect(() => {
    if (!schemaResult || seededRef.current) return
    seededRef.current = true
    const seeded: Record<string, RelationshipDecision> = {}
    for (const c of [...schemaResult.auto, ...schemaResult.review]) {
      seeded[decisionKey(c)] = { ...c, status: 'approved' }
    }
    for (const c of schemaResult.manual) {
      seeded[decisionKey(c)] = { ...c, status: 'rejected' }
    }
    setDecisions(seeded)
  }, [schemaResult, setDecisions])

  if (!schemaResult) {
    return (
      <StageShell title={t('relationships.title')} description={t('relationships.needDiscoveryDescription')}>
        <Button size="sm" onClick={() => setStage('discover')}>{t('relationships.goToDiscoverButton')}</Button>
      </StageShell>
    )
  }

  const discovered: RelationshipCandidate[] = [...schemaResult.auto, ...schemaResult.review, ...schemaResult.manual]
  const all: RelationshipCandidate[] = [...discovered, ...manualRelationships]

  const setStatus = (c: RelationshipCandidate, status: 'approved' | 'rejected') => {
    setDecisions((prev: Record<string, RelationshipDecision>) => ({
      ...prev, [decisionKey(c)]: { ...c, status },
    }))
  }

  const addManual = (c: RelationshipCandidate) => {
    setManualRelationships((prev: RelationshipCandidate[]) => [...prev, c])
    setDecisions((prev: Record<string, RelationshipDecision>) => ({
      ...prev, [decisionKey(c)]: { ...c, status: 'approved' },
    }))
  }

  const removeManual = (c: RelationshipCandidate) => {
    const key = decisionKey(c)
    setManualRelationships((prev: RelationshipCandidate[]) => prev.filter((m) => decisionKey(m) !== key))
    setDecisions((prev: Record<string, RelationshipDecision>) => {
      const next = { ...prev }
      delete next[key]
      return next
    })
  }

  const approvedCount = (Object.values(decisions) as RelationshipDecision[]).filter((d) => d.status === 'approved').length

  return (
    <StageShell
      title={t('relationships.title')}
      description={t('relationships.description')}
      actions={
        relationshipsSubmitted ? (
          <Button size="sm" onClick={() => setStage('clean')}>
            <Icons.ArrowRight className="size-3.5 rtl:-scale-x-100" /> {t('relationships.continueButton')}
          </Button>
        ) : (
          <Button
            size="sm"
            disabled={submitRelationships.isPending}
            onClick={() =>
              submitRelationships.mutate(Object.values(decisions), {
                onSuccess: () => { setRelationshipsSubmitted(true); ctx.markDone('relationships') },
                onError: (err: unknown) => toast({ title: t('errors.relationshipsFailed'), description: describeError(err), variant: 'destructive' }),
              })
            }
          >
            {submitRelationships.isPending ? <><Icons.Loader2 className="size-3.5 animate-spin" /> {t('relationships.savingButton')}</> : <>{t('relationships.saveButton', { count: approvedCount })} <Icons.ArrowRight className="size-3.5 rtl:-scale-x-100" /></>}
          </Button>
        )
      }
    >
      {relationshipsSubmitted && (
        <Badge variant="success" className="mb-4"><Icons.Check className="size-3" /> {t('relationships.savedBadge')}</Badge>
      )}

      <ManualRelationshipForm tables={schemaResult.tables} existing={all} onAdd={addManual} />

      <Card padding="none" className="mb-4 overflow-hidden">
        <div className="px-4 py-3 border-b border-border">
          <h3 className="text-base font-semibold">{t('relationships.diagram.title')}</h3>
          <p className="text-xs text-muted-foreground mt-0.5">
            {t('relationships.diagram.description')}
          </p>
        </div>
        <RelationshipErdView
          tables={schemaResult.tables}
          primaryTable={primaryTable}
          relationships={all}
          decisions={decisions}
          onToggleStatus={setStatus}
        />
      </Card>

      {all.length === 0 ? (
        <Card padding="default">
          <p className="text-sm text-muted-foreground">
            {t('relationships.noneFound')}
          </p>
        </Card>
      ) : (
        <div className="space-y-2.5">
          {all.map((r) => {
            const key = decisionKey(r)
            const isManual = r.source === 'manual'
            const status = decisions[key]?.status ?? (isManual ? 'approved' : r.confidence >= 0.5 ? 'approved' : 'rejected')
            return (
              <Card key={key} padding="default" className={cn(status === 'rejected' && 'opacity-60')}>
                <div className="flex flex-wrap items-center gap-3">
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm font-mono min-w-0 flex-1">
                    <div className="flex items-center gap-1.5 rounded-md bg-muted/40 px-2 py-1">
                      <span className="size-1.5 rounded-full bg-info" />
                      {r.table_a}
                    </div>
                    <span className="text-muted-foreground font-sans text-xs">.{r.column_a}</span>
                    <Icons.ArrowRight className="size-3.5 text-muted-foreground shrink-0" />
                    <div className="flex items-center gap-1.5 rounded-md bg-muted/40 px-2 py-1">
                      <span className="size-1.5 rounded-full bg-info" />
                      {r.table_b}
                    </div>
                    <span className="text-muted-foreground font-sans text-xs">.{r.column_b}</span>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    {!isManual && <ConfidenceBadge score={r.confidence} />}
                    <Badge variant="outline" className="text-2xs capitalize">{isManual ? t('relationships.manualYouBadge') : r.source}</Badge>
                    {status === 'approved' && <Badge variant="success"><Icons.Check className="size-3" /> {t('relationships.approvedBadge')}</Badge>}
                    {status === 'rejected' && <Badge variant="outline"><Icons.X className="size-3" /> {t('relationships.rejectedBadge')}</Badge>}
                  </div>
                  <div className="flex items-center gap-1.5 w-full sm:w-auto">
                    <Button size="sm" variant={status === 'approved' ? 'default' : 'outline'} className="h-7 text-xs" onClick={() => setStatus(r, 'approved')}>
                      <Icons.Check className="size-3" /> {t('relationships.approveButton')}
                    </Button>
                    <Button size="sm" variant={status === 'rejected' ? 'destructive' : 'ghost'} className="h-7 text-xs" onClick={() => setStatus(r, 'rejected')}>
                      <Icons.X className="size-3" /> {t('relationships.rejectButton')}
                    </Button>
                    {isManual && (
                      <Button size="sm" variant="ghost" className="h-7 text-xs text-muted-foreground" onClick={() => removeManual(r)}>
                        <Icons.Trash2 className="size-3" /> {t('relationships.removeButton')}
                      </Button>
                    )}
                  </div>
                </div>
                {r.reasoning && (
                  <p className="mt-2 text-xs text-muted-foreground italic">{r.reasoning}</p>
                )}
              </Card>
            )
          })}
        </div>
      )}
    </StageShell>
  )
}

function ManualRelationshipForm({
  tables, existing, onAdd,
}: {
  tables: { name: string; columns: string[] }[]
  existing: RelationshipCandidate[]
  onAdd: (c: RelationshipCandidate) => void
}) {
  const t = useTranslations('dataWorkspace')
  const [tableA, setTableA] = React.useState(tables[0]?.name ?? '')
  const [columnA, setColumnA] = React.useState('')
  const [tableB, setTableB] = React.useState(tables[1]?.name ?? tables[0]?.name ?? '')
  const [columnB, setColumnB] = React.useState('')

  const colsA = tables.find((t) => t.name === tableA)?.columns ?? []
  const colsB = tables.find((t) => t.name === tableB)?.columns ?? []

  const duplicate = existing.some((c) =>
    c.table_a === tableA && c.column_a === columnA && c.table_b === tableB && c.column_b === columnB
  )
  const sameColumn = tableA === tableB && columnA === columnB
  const canAdd = Boolean(tableA && columnA && tableB && columnB) && !sameColumn && !duplicate

  const add = () => {
    if (!canAdd) return
    onAdd({
      table_a: tableA, column_a: columnA, table_b: tableB, column_b: columnB,
      confidence: 1, evidence: {}, source: 'manual', reasoning: t('relationships.form.manualReasoning'),
      validation: { pk_uniqueness: 1, fk_coverage: 1, orphan_ratio: 0, dtype_compatible: true },
    })
    setColumnA('')
    setColumnB('')
  }

  return (
    <Card padding="default" className="mb-4">
      <h3 className="text-base font-semibold mb-1">{t('relationships.form.title')}</h3>
      <p className="text-xs text-muted-foreground mb-3">
        {t('relationships.form.description')}
      </p>
      <div className="flex flex-wrap items-end gap-3">
        <RelationshipPicker
          label={t('relationships.form.tableA')}
          value={tableA}
          options={tables.map((tb) => tb.name)}
          onChange={(v) => { setTableA(v); setColumnA('') }}
        />
        <RelationshipPicker
          label={t('relationships.form.columnA')}
          value={columnA}
          options={colsA}
          placeholder={t('relationships.form.columnPlaceholder')}
          onChange={setColumnA}
        />
        <Icons.ArrowRight className="size-4 text-muted-foreground shrink-0 mb-2 rtl:-scale-x-100" />
        <RelationshipPicker
          label={t('relationships.form.tableB')}
          value={tableB}
          options={tables.map((tb) => tb.name)}
          onChange={(v) => { setTableB(v); setColumnB('') }}
        />
        <RelationshipPicker
          label={t('relationships.form.columnB')}
          value={columnB}
          options={colsB}
          placeholder={t('relationships.form.columnPlaceholder')}
          onChange={setColumnB}
        />
        <Button size="sm" disabled={!canAdd} onClick={add}>
          <Icons.Plus className="size-3.5" /> {t('relationships.form.addButton')}
        </Button>
      </div>
      {sameColumn && columnA && (
        <p className="mt-2 text-xs text-warning">{t('relationships.form.sameColumnWarning')}</p>
      )}
      {duplicate && !sameColumn && (
        <p className="mt-2 text-xs text-warning">{t('relationships.form.duplicateWarning')}</p>
      )}
    </Card>
  )
}

function RelationshipPicker({
  label, value, options, onChange, placeholder,
}: {
  label: string
  value: string
  options: string[]
  onChange: (v: string) => void
  placeholder?: string
}) {
  const t = useTranslations('dataWorkspace')
  return (
    <div>
      <label className="text-2xs uppercase tracking-wider text-muted-foreground/70 font-medium">{label}</label>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger className="h-9 w-[160px] mt-1 text-sm">
          <SelectValue placeholder={placeholder ?? t('relationships.form.selectPlaceholder')} />
        </SelectTrigger>
        <SelectContent>
          {options.map((o) => <SelectItem key={o} value={o}>{o}</SelectItem>)}
        </SelectContent>
      </Select>
    </div>
  )
}

// ============= CLEAN =============
function CleanStage({ ctx }: { ctx: any }) {
  const t = useTranslations('dataWorkspace')
  const { toast } = useToast()
  const {
    activeTable, plan, setPlan, editedPlan, setEditedPlan, planTable, editPlan, planStepOpinion,
    cleanResult, setCleanResult, cleanTable,
    remainingResult, setRemainingResult, cleanRemaining, setStage,
  } = ctx

  const [newStepText, setNewStepText] = React.useState('')
  const [opinions, setOpinions] = React.useState<Record<string, string>>({})
  const [opinionPendingFor, setOpinionPendingFor] = React.useState<string | null>(null)

  React.useEffect(() => {
    if (plan && editedPlan === null) setEditedPlan(plan.cleaning_plan)
  }, [plan, editedPlan, setEditedPlan])

  if (!plan) {
    return (
      <StageShell title={t('clean.title')} description={t('clean.generateDescription', { table: activeTable })}>
        {planTable.isPending ? (
          <LoadingState label={t('clean.generatingLabel')} />
        ) : (
          <Button size="sm" onClick={() => planTable.mutate(undefined, {
            onSuccess: (res: PlanResult) => { setPlan(res); setEditedPlan(res.cleaning_plan) },
            onError: (err: unknown) => toast({ title: t('errors.planFailed'), description: describeError(err), variant: 'destructive' }),
          })}>
            <Icons.Sparkles className="size-3.5" /> {t('clean.generateButton')}
          </Button>
        )}
      </StageShell>
    )
  }

  const steps: CleaningPlanStep[] = editedPlan ?? plan.cleaning_plan
  const isDirty = JSON.stringify(steps) !== JSON.stringify(plan.cleaning_plan)
  const generatedWasEmpty = plan.cleaning_plan.length === 0

  const removeStep = (id: string) => setEditedPlan(steps.filter((s) => s.id !== id))

  const addStep = () => {
    const text = newStepText.trim()
    if (!text) return
    const step: CleaningPlanStep = { id: `manual-${Date.now()}`, description: text, source: 'manual' }
    setEditedPlan([...steps, step])
    setNewStepText('')
    setOpinionPendingFor(step.id)
    planStepOpinion.mutate(text, {
      onSuccess: (res: { opinion: string }) => {
        setOpinions((prev) => ({ ...prev, [step.id]: res.opinion }))
        setOpinionPendingFor(null)
      },
      onError: (err: unknown) => {
        setOpinionPendingFor(null)
        toast({ title: t('errors.opinionFailed'), description: describeError(err), variant: 'destructive' })
      },
    })
  }

  return (
    <StageShell
      title={t('clean.title')}
      description={t('clean.editDescription', { table: activeTable })}
      actions={
        <>
          <Button
            size="sm"
            variant="outline"
            disabled={editPlan.isPending || !isDirty}
            onClick={() => editPlan.mutate(steps, {
              onSuccess: (res: PlanResult) => { setPlan(res); setEditedPlan(res.cleaning_plan) },
              onError: (err: unknown) => toast({ title: t('errors.editPlanFailed'), description: describeError(err), variant: 'destructive' }),
            })}
          >
            <Icons.Save className="size-3.5" /> {t('clean.saveEditsButton')}
          </Button>
          <Button size="sm" disabled={cleanTable.isPending} onClick={() => cleanTable.mutate(undefined, {
            onSuccess: (res: CleanResult) => { setCleanResult(res); ctx.markDone('clean') },
            onError: (err: unknown) => toast({ title: t('errors.cleanFailed'), description: describeError(err), variant: 'destructive' }),
          })}>
            {cleanTable.isPending ? <><Icons.Loader2 className="size-3.5 animate-spin" /> {t('clean.executingLabel')}</> : <><Icons.Play className="size-3.5" /> {t('clean.executeButton')}</>}
          </Button>
        </>
      }
    >
      {generatedWasEmpty && (
        <AIInsightCallout
          label={t('clean.alreadyCleanTitle')}
          body={t('clean.alreadyCleanBody', { table: activeTable })}
          className="mb-4"
        />
      )}

      <Card padding="default" className="mb-4">
        <h3 className="text-base font-semibold mb-1">{t('clean.planTitle')}</h3>
        <p className="text-xs text-muted-foreground mb-3">{t('clean.planSubtitle')}</p>

        {steps.length === 0 ? (
          <p className="mb-3 text-sm text-muted-foreground">{t('clean.noStepsYet')}</p>
        ) : (
          <div className="space-y-2 mb-3">
            {steps.map((s) => (
              <div key={s.id}>
                <div className="flex items-start gap-2 rounded-lg border border-border/60 p-2.5">
                  <span className="flex-1 text-sm">{s.description}</span>
                  {/* A typed operator runs deterministic, tested code; a step
                      without one is a free-text instruction handled by
                      generated code, which is worth distinguishing at a glance. */}
                  {s.op ? (
                    <Badge variant="success" className="shrink-0 font-mono text-2xs">{s.op}</Badge>
                  ) : (
                    <Badge variant="outline" className="shrink-0 text-2xs">{t('clean.customStepBadge')}</Badge>
                  )}
                  {s.source === 'manual' && (
                    <Badge variant="outline" className="text-2xs shrink-0">{t('clean.manualBadge')}</Badge>
                  )}
                  <Button
                    size="sm" variant="ghost" className="h-6 shrink-0 px-1.5 text-muted-foreground"
                    onClick={() => removeStep(s.id)}
                  >
                    <Icons.Trash2 className="size-3" />
                  </Button>
                </div>
                {opinions[s.id] && (
                  <p className="mt-1 ms-1 text-xs text-muted-foreground italic">
                    <Icons.Sparkles className="inline size-3 me-1 text-primary" /> {opinions[s.id]}
                  </p>
                )}
                {opinionPendingFor === s.id && (
                  <p className="mt-1 ms-1 text-xs text-muted-foreground">
                    <Icons.Loader2 className="inline size-3 animate-spin me-1" /> {t('clean.opinionLoading')}
                  </p>
                )}
              </div>
            ))}
          </div>
        )}

        <div className="flex items-end gap-2 pt-2 border-t border-border/60">
          <div className="flex-1">
            <Label className="text-xs">{t('clean.addStepLabel')}</Label>
            <Textarea
              className="mt-1 min-h-[52px] text-sm"
              placeholder={t('clean.addStepPlaceholder')}
              value={newStepText}
              onChange={(e) => setNewStepText(e.target.value)}
            />
          </div>
          <Button size="sm" disabled={!newStepText.trim()} onClick={addStep}>
            <Icons.Plus className="size-3.5" /> {t('clean.addStepButton')}
          </Button>
        </div>
      </Card>

      {cleanResult && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5">
            <Metric label={t('clean.metrics.result')} value={cleanResult.success ? t('common.passed') : t('common.failed')} tone={cleanResult.success ? 'success' : 'danger'} />
            <Metric label={t('clean.metrics.retries')} value={String(cleanResult.retry_count)} />
            <Metric label={t('clean.metrics.rowsAfter')} value={String(cleanResult.preview?.rows ?? '—')} />
            <Metric label={t('clean.metrics.transformations')} value={String(cleanResult.transformation_log.length)} />
          </div>

          <Card padding="default">
            <h3 className="text-base font-semibold mb-3">{t('clean.logTitle')}</h3>
            <div className="rounded-lg border border-border/60 bg-background/40 p-3 font-mono text-xs space-y-1 max-h-48 overflow-y-auto">
              {cleanResult.transformation_log.length === 0 ? (
                <p className="text-muted-foreground">{t('clean.noLogLines')}</p>
              ) : cleanResult.transformation_log.map((line, i) => (
                <div key={i} className="text-muted-foreground">{line}</div>
              ))}
            </div>
            {cleanResult.error && (
              <p className="mt-3 rounded-md bg-destructive/10 border border-destructive/30 px-3 py-2 text-xs text-destructive font-mono whitespace-pre-wrap">
                {cleanResult.error}
              </p>
            )}
          </Card>

          <Card padding="default" className="mt-4">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-base font-semibold">{t('clean.otherTablesTitle')}</h3>
              <Button size="sm" variant="outline" disabled={cleanRemaining.isPending} onClick={() => cleanRemaining.mutate(undefined, {
                onSuccess: (res: CleanRemainingResult) => { setRemainingResult(res); ctx.markDone('reconcile') },
                onError: (err: unknown) => toast({ title: t('errors.cleanRemainingFailed'), description: describeError(err), variant: 'destructive' }),
              })}>
                {cleanRemaining.isPending ? <><Icons.Loader2 className="size-3.5 animate-spin" /> {t('clean.cleaningRemainingLabel')}</> : <>{t('clean.cleanRemainingButton')}</>}
              </Button>
            </div>
            {remainingResult ? (
              <div className="space-y-2">
                {remainingResult.results.map((r) => (
                  <div key={r.table_name} className="flex items-center justify-between rounded-lg border border-border/60 p-2.5">
                    <span className="text-sm font-medium">{r.table_name}</span>
                    <Badge variant={r.success ? 'success' : 'danger'} className="text-2xs">{r.success ? t('common.passed') : t('common.failed')}</Badge>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">{t('clean.notRunYet')}</p>
            )}
          </Card>

          <div className="mt-4">
            <Button size="sm" onClick={() => setStage('validate')}><Icons.ArrowRight className="size-3.5 rtl:-scale-x-100" /> {t('clean.continueButton')}</Button>
          </div>
        </>
      )}
    </StageShell>
  )
}

// ============= VALIDATE =============
function ValidateStage({ ctx }: { ctx: any }) {
  const t = useTranslations('dataWorkspace')
  const { toast } = useToast()
  const { cleanResult, remainingResult, integrityResult, setIntegrityResult, integrity, setStage } = ctx

  const allCleaned = Boolean(cleanResult) && (remainingResult ? remainingResult.results.every((r: any) => r.success !== undefined) : true)

  return (
    <StageShell
      title={t('validate.title')}
      description={t('validate.description')}
      actions={
        allCleaned ? (
          <Button
            size="sm"
            disabled={integrity.isPending}
            onClick={() => integrity.mutate(undefined, {
              onSuccess: (res: IntegrityResult) => { setIntegrityResult(res); if (res.passed) ctx.markDone('validate') },
              onError: (err: unknown) => toast({ title: t('errors.integrityFailed'), description: describeError(err), variant: 'destructive' }),
            })}
          >
            {integrity.isPending ? <><Icons.Loader2 className="size-3.5 animate-spin" /> {t('validate.checkingButton')}</> : <>{t('validate.runButton')}</>}
          </Button>
        ) : undefined
      }
    >
      {!allCleaned && (
        <AIInsightCallout label={t('validate.notReadyLabel')} body={t('validate.notReadyBody')} />
      )}
      {integrityResult && (
        <>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-5">
            <Card padding="default">
              <div className="flex items-center justify-between">
                <p className="text-base font-medium">{t('validate.overallResult')}</p>
                <Badge variant={integrityResult.passed ? 'success' : 'danger'}>{integrityResult.passed ? t('common.passed') : t('common.blocked')}</Badge>
              </div>
            </Card>
            <Card padding="default">
              <div className="flex items-center justify-between">
                <p className="text-base font-medium">{t('validate.primaryKeyIssues')}</p>
                <Badge variant={(integrityResult.report.pk_blocking_issues?.length ?? 0) === 0 ? 'success' : 'danger'}>
                  {integrityResult.report.pk_blocking_issues?.length ?? 0}
                </Badge>
              </div>
            </Card>
            <Card padding="default">
              <div className="flex items-center justify-between">
                <p className="text-base font-medium">{t('validate.duplicateRowIssues')}</p>
                <Badge variant={(integrityResult.report.duplicate_row_issues?.length ?? 0) === 0 ? 'success' : 'warning'}>
                  {integrityResult.report.duplicate_row_issues?.length ?? 0}
                </Badge>
              </div>
            </Card>
            <Card padding="default">
              <div className="flex items-center justify-between">
                <p className="text-base font-medium">{t('validate.dtypeWarnings')}</p>
                <Badge variant={(integrityResult.report.dtype_issues?.length ?? 0) === 0 ? 'success' : 'warning'}>
                  {integrityResult.report.dtype_issues?.length ?? 0}
                </Badge>
              </div>
            </Card>
          </div>
          {!integrityResult.passed && (
            <AIInsightCallout
              label={t('common.blocked')}
              body={t('validate.blockedBody')}
            />
          )}
          {integrityResult.passed && (
            <Button size="sm" onClick={() => setStage('reconcile')}><Icons.ArrowRight className="size-3.5 rtl:-scale-x-100" /> {t('validate.continueButton')}</Button>
          )}
        </>
      )}
    </StageShell>
  )
}

// ============= RECONCILE =============
function ReconcileStage({ ctx }: { ctx: any }) {
  const t = useTranslations('dataWorkspace')
  const { remainingResult, setStage } = ctx
  const checks = remainingResult?.reconciliation ?? []

  return (
    <StageShell
      title={t('reconcile.title')}
      description={t('reconcile.description')}
    >
      {checks.length === 0 ? (
        <div className="space-y-3">
          <Card padding="default">
            <p className="text-sm text-muted-foreground">
              {t('reconcile.noChecks')}
            </p>
          </Card>
          <Button size="sm" onClick={() => setStage('save')}><Icons.ArrowRight className="size-3.5 rtl:-scale-x-100" /> {t('reconcile.continueButton')}</Button>
        </div>
      ) : (
        <div className="space-y-3">
          {checks.map((r: any, i: number) => (
            <Card key={i} padding="default">
              <div className="flex flex-wrap items-start gap-3">
                <div className={cn(
                  'flex size-8 shrink-0 items-center justify-center rounded-lg mt-0.5',
                  r.within_tolerance ? 'bg-success/12 text-success' : 'bg-warning/12 text-warning'
                )}>
                  <Icons.Scale className="size-4" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-start justify-between gap-2">
                    <p className="text-sm font-mono">{r.table_a}.{r.column_a} → {r.table_b}.{r.column_b}</p>
                    <Badge variant={r.within_tolerance ? 'success' : 'warning'}>{r.within_tolerance ? t('reconcile.withinTolerance') : t('reconcile.needsReview')}</Badge>
                  </div>
                  <div className="mt-2 grid grid-cols-2 gap-3 text-xs">
                    <div>
                      <p className="text-2xs uppercase tracking-wider text-muted-foreground/70 font-medium">{t('reconcile.orphanRateBefore')}</p>
                      <p className="mt-0.5 text-warning">{(r.orphan_rate_before * 100).toFixed(1)}%</p>
                    </div>
                    <div>
                      <p className="text-2xs uppercase tracking-wider text-muted-foreground/70 font-medium">{t('reconcile.orphanRateAfter')}</p>
                      <p className={cn('mt-0.5', r.within_tolerance ? 'text-success' : 'text-warning')}>{(r.orphan_rate_after * 100).toFixed(1)}%</p>
                    </div>
                  </div>
                </div>
              </div>
            </Card>
          ))}
          <Button size="sm" onClick={() => setStage('save')}><Icons.ArrowRight className="size-3.5 rtl:-scale-x-100" /> {t('reconcile.continueButton')}</Button>
        </div>
      )}
    </StageShell>
  )
}

// ============= SAVE =============
function SaveStage({ ctx }: { ctx: any }) {
  const t = useTranslations('dataWorkspace')
  const { toast } = useToast()
  const { integrityResult, saveResult, setSaveResult, save } = ctx

  return (
    <StageShell
      title={t('save.title')}
      description={t('save.description')}
    >
      <Card padding="lg" className="max-w-2xl">
        {saveResult?.saved ? (
          <>
            <div className="flex items-start gap-3 mb-4">
              <div className="flex size-10 items-center justify-center rounded-lg bg-success/12 text-success ring-1 ring-inset ring-success/20">
                <Icons.ShieldCheck className="size-5" />
              </div>
              <div>
                <h3 className="text-md font-semibold">{t('save.savedTitle')}</h3>
                <p className="mt-0.5 text-sm text-muted-foreground">{t.rich('save.schemaHolds', { schema: saveResult.schema_name, count: saveResult.tables.length, code: (chunks) => <code className="font-mono">{chunks}</code> })}</p>
              </div>
            </div>
            <div className="rounded-xl border border-border/60 divide-y divide-border/60">
              <Row label={t('save.tablesCommitted')} value={String(saveResult.tables.length)} />
              <Row label={t('save.tableNames')} value={saveResult.tables.join(', ')} />
              <Row label={t('save.schemaLabel')} value={saveResult.schema_name} />
            </div>
          </>
        ) : (
          <>
            <div className="flex items-start gap-3 mb-4">
              <div className={cn(
                'flex size-10 items-center justify-center rounded-lg ring-1 ring-inset',
                integrityResult?.passed ? 'bg-success/12 text-success ring-success/20' : 'bg-warning/12 text-warning ring-warning/20'
              )}>
                <Icons.ShieldCheck className="size-5" />
              </div>
              <div>
                <h3 className="text-md font-semibold">
                  {integrityResult?.passed ? t('save.readyTitle') : t('save.integrityRequiredTitle')}
                </h3>
                <p className="mt-0.5 text-sm text-muted-foreground">
                  {integrityResult?.passed
                    ? t('save.allGatesPassed')
                    : t('save.runValidateFirst')}
                </p>
              </div>
            </div>
            <Button
              size="sm"
              disabled={!integrityResult?.passed || save.isPending}
              onClick={() => save.mutate(undefined, {
                onSuccess: (res: SaveResult) => { setSaveResult(res); ctx.markDone('save') },
                onError: (err: unknown) => toast({ title: t('errors.saveFailed'), description: describeError(err), variant: 'destructive' }),
              })}
            >
              {save.isPending ? <><Icons.Loader2 className="size-3.5 animate-spin" /> {t('save.savingButton')}</> : <><Icons.Save className="size-3.5" /> {t('save.commitButton')}</>}
            </Button>
          </>
        )}
      </Card>
    </StageShell>
  )
}

// ============= RIGHT CONTEXT PANEL =============
function ContextContent({ stage, ctx }: { stage: Stage; ctx: any }) {
  const t = useTranslations('dataWorkspace')
  const { activeTable, cleanResult, integrityResult } = ctx
  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border p-4">
        <p className="text-xs uppercase tracking-wider text-muted-foreground/70 font-semibold mb-2">{t('context.activeContext')}</p>
        <div className="rounded-lg border border-border/60 bg-background/40 p-3">
          <p className="text-sm font-medium">{activeTable}</p>
          <p className="text-xs text-muted-foreground mt-0.5">{t('context.stageLabel')} <span className="text-foreground capitalize">{t(`stages.${stage}`)}</span></p>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {cleanResult && (
          <ContextSection title={t('context.lastCleaningResult')}>
            <div className="space-y-1.5 text-sm">
              <ContextRow label={t('context.statusLabel')} value={cleanResult.success ? t('common.passed') : t('common.failed')} tone={cleanResult.success ? 'success' : 'warning'} />
              <ContextRow label={t('context.rowsAfterLabel')} value={String(cleanResult.preview?.rows ?? '—')} />
              <ContextRow label={t('context.retriesLabel')} value={String(cleanResult.retry_count)} />
            </div>
          </ContextSection>
        )}

        {integrityResult && (
          <ContextSection title={t('context.integrityTitle')}>
            <div className="space-y-1.5 text-sm">
              <ContextRow label={t('context.passedLabel')} value={integrityResult.passed ? t('common.yes') : t('common.no')} tone={integrityResult.passed ? 'success' : 'warning'} />
            </div>
          </ContextSection>
        )}

        <ContextSection title={t('context.activeAgent')}>
          <div className="rounded-lg border border-info/25 bg-info/[0.05] p-2.5">
            <div className="flex items-center gap-2">
              <div className="flex size-7 items-center justify-center rounded-lg bg-info/15 text-info">
                <Icons.Sparkles className="size-3.5" />
              </div>
              <div className="min-w-0">
                <p className="text-sm font-medium leading-tight">{t('context.agentName')}</p>
                <p className="text-2xs text-info flex items-center gap-1">
                  <span className="size-1.5 rounded-full bg-info" /> {t(`stages.${stage}`)}
                </p>
              </div>
            </div>
          </div>
        </ContextSection>
      </div>
    </div>
  )
}

function ContextSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="text-2xs uppercase tracking-wider text-muted-foreground/70 font-semibold mb-2">{title}</p>
      {children}
    </div>
  )
}

function ContextRow({ label, value, tone }: { label: string; value: string; tone?: 'success' | 'warning' }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-muted-foreground">{label}</span>
      <span className={cn(
        'font-medium tabular-nums',
        tone === 'success' && 'text-success',
        tone === 'warning' && 'text-warning',
      )}>{value}</span>
    </div>
  )
}
