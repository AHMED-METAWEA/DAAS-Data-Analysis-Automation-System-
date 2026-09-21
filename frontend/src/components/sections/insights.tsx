'use client'

import * as React from 'react'
import ReactMarkdown from 'react-markdown'
import { useTranslations } from 'next-intl'
import { useAppStore } from '@/lib/store'
import { useGenerateInsights, useInsightsChat, type ChatMessage, type Grounding } from '@/lib/queries/insights'
import { Card } from '@/components/ui/card'
import { Badge, GroundingBadge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Label } from '@/components/ui/label'
import { SectionScroll, SectionHeader } from '@/components/shared/layout'
import { PlotlyChart } from '@/components/shared/plotly-chart'
import { KpiGrid, ChartGrid } from '@/components/shared/dashboard-blocks'
import { LoadingState, ErrorState } from '@/components/shared/states'
import { SaveReportButton } from '@/components/shared/save-report-button'
import { BlindSpots, PriorityList, VerificationPanel } from '@/components/sections/insights-audit'
import * as Icons from 'lucide-react'

function groundingStatus(g: Grounding): 'grounded' | 'partial' | 'review' {
  if (g.status === 'clean') return 'grounded'
  if (g.status === 'partial') return 'partial'
  return 'review'
}

export function BusinessInsights() {
  const t = useTranslations('insights')
  const projectId = useAppStore((s) => s.activeProjectId)
  const [industry, setIndustry] = React.useState('')
  const [goal, setGoal] = React.useState('')
  const [context, setContext] = React.useState('')
  const [chatMessages, setChatMessages] = React.useState<ChatMessage[]>([])
  const [chatInput, setChatInput] = React.useState('')

  const generate = useGenerateInsights(projectId)
  const chat = useInsightsChat(projectId)

  const businessContext = [
    industry && `Industry: ${industry}`,
    goal && `Goal: ${goal}`,
    context,
  ].filter(Boolean).join('\n')

  const runGenerate = () => {
    setChatMessages([])
    generate.mutate({ business_context: businessContext })
  }

  const sendChat = () => {
    if (!chatInput.trim() || !generate.data) return
    const message = chatInput.trim()
    setChatInput('')
    const nextHistory: ChatMessage[] = [...chatMessages, { role: 'user', content: message }]
    setChatMessages(nextHistory)
    chat.mutate(
      { message, report_md: generate.data.report_md, template_label: generate.data.template, history: chatMessages },
      { onSuccess: (res) => setChatMessages((h) => [...h, { role: 'assistant', content: res.answer }]) }
    )
  }

  return (
    <SectionScroll>
      <SectionHeader
        title={t('title')}
        description={t('description')}
        icon={<Icons.Lightbulb className="size-4.5" />}
        actions={
          generate.data && (
            <>
              <SaveReportButton
                key={generate.data.report_md}
                projectId={projectId}
                type="insights"
                title={generate.data.template}
                markdown={generate.data.report_md}
                grounding={generate.data.grounding}
              />
              <Button size="sm" variant="outline" onClick={runGenerate} disabled={generate.isPending}>
                <Icons.RefreshCw className="size-3.5" /> {t('reanalyze')}
              </Button>
            </>
          )
        }
      />

      {!generate.data && (
        <Card padding="lg" className="max-w-2xl">
          <h3 className="text-md font-semibold mb-1">{t('context.title')}</h3>
          <p className="text-sm text-muted-foreground mb-4">
            {t('context.description')}
          </p>
          <div className="space-y-3">
            <div>
              <Label className="text-sm">{t('context.industryLabel')}</Label>
              <Input className="mt-1.5" placeholder={t('context.industryPlaceholder')} value={industry} onChange={(e) => setIndustry(e.target.value)} />
            </div>
            <div>
              <Label className="text-sm">{t('context.goalLabel')}</Label>
              <Input className="mt-1.5" placeholder={t('context.goalPlaceholder')} value={goal} onChange={(e) => setGoal(e.target.value)} />
            </div>
            <div>
              <Label className="text-sm">{t('context.additionalLabel')}</Label>
              <Textarea className="mt-1.5 min-h-[80px]" placeholder={t('context.additionalPlaceholder')} value={context} onChange={(e) => setContext(e.target.value)} />
            </div>
          </div>
          <Button className="mt-4" onClick={runGenerate} disabled={generate.isPending}>
            {generate.isPending ? <><Icons.Loader2 className="size-3.5 animate-spin" /> {t('context.generating')}</> : <><Icons.Sparkles className="size-3.5" /> {t('context.generate')}</>}
          </Button>
        </Card>
      )}

      {generate.isPending && (
        <Card padding="none" className="mt-4">
          <LoadingState label={t('loading.label')} stage={t('loading.stage')} />
        </Card>
      )}

      {generate.isError && (
        <Card padding="none" className="mt-4">
          <ErrorState body={(generate.error as Error)?.message ?? t('errorFallback')} onRetry={runGenerate} />
        </Card>
      )}

      {generate.data && (
        <>
          <Card padding="default" className="mb-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <Badge variant="outline">{generate.data.template}</Badge>
                <GroundingBadge status={groundingStatus(generate.data.grounding)} />
              </div>
              <p className="text-xs text-muted-foreground">{generate.data.grounding.label}</p>
            </div>
          </Card>

          <KpiGrid kpis={generate.data.dashboard.kpis} />

          <Card padding="lg" className="mb-4 prose-report">
            <div className="prose prose-sm dark:prose-invert max-w-none text-base leading-relaxed">
              <ReactMarkdown>{generate.data.report_md}</ReactMarkdown>
            </div>
          </Card>

          {/* The report makes a strong claim about its own numbers; these blocks
              let the reader check it rather than take it on trust. */}
          {generate.data.verification && (
            <VerificationPanel
              verification={generate.data.verification}
              figures={generate.data.figures ?? []}
            />
          )}
          <PriorityList evidence={generate.data.evidence ?? []} />
          <BlindSpots items={generate.data.blind_spots ?? []} />

          {generate.data.dashboard.charts.length > 0 && (
            <div className="mb-4">
              <h3 className="text-base font-semibold mb-3">{t('dashboard.chartsTitle')}</h3>
              <ChartGrid charts={generate.data.dashboard.charts} projectId={projectId} computedLabel={t('dashboard.computed')} />
            </div>
          )}

          {/* Follow-up chat */}
          <Card padding="default">
            <h3 className="text-base font-semibold mb-3">{t('chat.title')}</h3>
            <div className="space-y-3 mb-3 max-h-[400px] overflow-y-auto">
              {chatMessages.map((m, i) => (
                <div key={i} className={m.role === 'user' ? 'text-end' : ''}>
                  <div className={m.role === 'user'
                    ? 'inline-block rounded-lg bg-primary/12 px-3 py-2 text-sm max-w-[85%] text-start'
                    : 'rounded-lg border border-border/60 bg-background/40 px-3 py-2 text-sm'}>
                    {m.role === 'assistant' ? <ReactMarkdown>{m.content}</ReactMarkdown> : m.content}
                  </div>
                </div>
              ))}
              {chat.isPending && <LoadingState label={t('chat.thinking')} />}
              {chat.data?.figure && <PlotlyChart figure={chat.data.figure} height={300} projectId={projectId} />}
            </div>
            <div className="flex items-center gap-2">
              <Input
                placeholder={t('chat.placeholder')}
                value={chatInput}
                onChange={(e) => setChatInput(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') sendChat() }}
              />
              <Button size="sm" onClick={sendChat} disabled={chat.isPending || !chatInput.trim()}>
                <Icons.Send className="size-3.5" />
              </Button>
            </div>
          </Card>
        </>
      )}
    </SectionScroll>
  )
}
