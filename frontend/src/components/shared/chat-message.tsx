'use client'

import * as React from 'react'
import { useTranslations } from 'next-intl'
import ReactMarkdown from 'react-markdown'
import type { CopilotArtifact, PlanStepInfo } from '@/lib/queries/copilot'
import { useSaveReport } from '@/lib/queries/reports'
import { useToast } from '@/hooks/use-toast'
import { PlotlyChart } from '@/components/shared/plotly-chart'
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from '@/components/ui/table'
import { Accordion, AccordionItem, AccordionTrigger, AccordionContent } from '@/components/ui/accordion'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import * as Icons from 'lucide-react'

export type PlanStep = PlanStepInfo & { status: 'pending' | 'running' | 'done' }

export type ChatMessageData = {
  role: 'user' | 'assistant'
  content: string
  /** The agents this turn planned to run, with live status (multi-step). */
  plan?: PlanStep[]
  /** One block per agent that ran — chart / table / report. */
  artifacts?: CopilotArtifact[]
  /** True while this assistant message is still being streamed in. */
  streaming?: boolean
}

function TypingDots() {
  return (
    <span className="inline-flex items-center gap-1 py-1" aria-label="Assistant is typing">
      <span className="size-1.5 rounded-full bg-muted-foreground/50 animate-bounce [animation-delay:-0.3s]" />
      <span className="size-1.5 rounded-full bg-muted-foreground/50 animate-bounce [animation-delay:-0.15s]" />
      <span className="size-1.5 rounded-full bg-muted-foreground/50 animate-bounce" />
    </span>
  )
}

/** Compact progress strip for a multi-agent turn. */
function PlanProgress({ plan }: { plan: PlanStep[] }) {
  if (plan.length < 2) return null
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {plan.map((step, i) => (
        <span
          key={i}
          className="inline-flex items-center gap-1 rounded-full border border-border/60 bg-background/40 px-2 py-0.5 text-2xs"
        >
          {step.status === 'done'
            ? <Icons.CheckCircle2 className="size-3 text-success" />
            : step.status === 'running'
              ? <Icons.Loader2 className="size-3 animate-spin text-primary" />
              : <Icons.Circle className="size-3 text-muted-foreground/50" />}
          {step.route_label}
        </span>
      ))}
    </div>
  )
}

function ArtifactBlock({ artifact, projectId }: { artifact: CopilotArtifact; projectId?: string }) {
  const t = useTranslations('copilot')
  const columns = artifact.table && artifact.table.length > 0 ? Object.keys(artifact.table[0]) : []

  return (
    <div className="space-y-2">
      {artifact.tool_error && (
        <p className="text-xs text-destructive">{artifact.tool_error}</p>
      )}
      {artifact.figure && (
        <PlotlyChart figure={artifact.figure} height={320} projectId={projectId} title={artifact.route_label} />
      )}
      {columns.length > 0 && (
        <div className="rounded-lg border border-border/60 overflow-hidden">
          <Table>
            <TableHeader>
              <TableRow>{columns.map((c) => <TableHead key={c}>{c}</TableHead>)}</TableRow>
            </TableHeader>
            <TableBody>
              {artifact.table!.slice(0, 20).map((row, i) => (
                <TableRow key={i}>
                  {columns.map((c) => <TableCell key={c}>{String(row[c] ?? '')}</TableCell>)}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
      {artifact.report_md && (
        <Accordion type="single" collapsible>
          <AccordionItem value="report">
            <AccordionTrigger className="text-sm">{t('viewFullReport')}</AccordionTrigger>
            <AccordionContent className="prose prose-sm dark:prose-invert max-w-none">
              <ReactMarkdown>{artifact.report_md}</ReactMarkdown>
            </AccordionContent>
          </AccordionItem>
        </Accordion>
      )}
    </div>
  )
}

function MessageActions({
  data, projectId, onRegenerate,
}: {
  data: ChatMessageData
  projectId?: string
  onRegenerate?: () => void
}) {
  const t = useTranslations('copilot')
  const { toast } = useToast()
  const saveReport = useSaveReport(projectId ?? '')

  const copy = () => {
    void navigator.clipboard.writeText(data.content).then(
      () => toast({ title: t('copied') }),
      () => toast({ title: t('copyFailed'), variant: 'destructive' }),
    )
  }

  const save = () => {
    // Bundle the answer plus every agent's full report into one saved document.
    const reports = (data.artifacts ?? []).map((a) => a.report_md).filter(Boolean) as string[]
    const markdown = [data.content, ...reports].join('\n\n---\n\n')
    const title = data.content.replace(/[#*`]/g, '').trim().slice(0, 80) || 'Copilot answer'
    saveReport.mutate(
      { type: 'copilot', title, markdown },
      {
        onSuccess: () => toast({ title: t('savedToReports') }),
        onError: () => toast({ title: t('saveFailed'), variant: 'destructive' }),
      },
    )
  }

  return (
    <div className="flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100">
      <Button size="sm" variant="ghost" className="h-6 px-1.5 text-xs text-muted-foreground" onClick={copy}>
        <Icons.Copy className="size-3" /> {t('copy')}
      </Button>
      {projectId && (
        <Button
          size="sm" variant="ghost" className="h-6 px-1.5 text-xs text-muted-foreground"
          onClick={save} disabled={saveReport.isPending || saveReport.isSuccess}
        >
          {saveReport.isSuccess
            ? <><Icons.CheckCircle2 className="size-3 text-success" /> {t('savedToReports')}</>
            : <><Icons.Save className="size-3" /> {t('saveToReports')}</>}
        </Button>
      )}
      {onRegenerate && (
        <Button size="sm" variant="ghost" className="h-6 px-1.5 text-xs text-muted-foreground" onClick={onRegenerate}>
          <Icons.RotateCcw className="size-3" /> {t('regenerate')}
        </Button>
      )}
    </div>
  )
}

export function ChatMessage({
  data, projectId, onRegenerate,
}: {
  data: ChatMessageData
  projectId?: string
  onRegenerate?: () => void
}) {
  if (data.role === 'user') {
    return (
      <div className="text-right">
        <div className="inline-block rounded-lg bg-primary/12 px-3 py-2 text-sm max-w-[85%] text-left">
          {data.content}
        </div>
      </div>
    )
  }

  const artifacts = data.artifacts ?? []
  const showActions = !data.streaming && Boolean(data.content)

  return (
    <div className="group space-y-2.5">
      {data.plan && data.plan.length > 1 && <PlanProgress plan={data.plan} />}
      {artifacts.length <= 1 && artifacts[0]?.route_label && !data.streaming && (
        <Badge variant="outline" className="text-2xs">{artifacts[0].route_label}</Badge>
      )}
      <div className="rounded-lg border border-border/60 bg-background/40 px-3 py-2 text-sm prose prose-sm dark:prose-invert max-w-none">
        {data.content ? <ReactMarkdown>{data.content}</ReactMarkdown> : data.streaming ? <TypingDots /> : null}
      </div>
      {artifacts.map((art, i) => (
        <div key={i} className="space-y-1.5">
          {artifacts.length > 1 && (
            <Badge variant="outline" className="text-2xs">{art.route_label}</Badge>
          )}
          <ArtifactBlock artifact={art} projectId={projectId} />
        </div>
      ))}
      {showActions && <MessageActions data={data} projectId={projectId} onRegenerate={onRegenerate} />}
    </div>
  )
}
