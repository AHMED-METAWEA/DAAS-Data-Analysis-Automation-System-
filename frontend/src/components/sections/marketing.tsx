'use client'

import * as React from 'react'
import ReactMarkdown from 'react-markdown'
import { useTranslations } from 'next-intl'
import { cn } from '@/lib/utils'
import { useAppStore } from '@/lib/store'
import {
  useRunMarketing, useGenerateCampaignPlan, useGenerateAdCopy, useMarketingChat,
  type MarketingRunResult, type ChatMessage,
} from '@/lib/queries/marketing'
import { Card } from '@/components/ui/card'
import { Badge, GroundingBadge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Label } from '@/components/ui/label'
import { Checkbox } from '@/components/ui/checkbox'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { SectionScroll, SectionHeader } from '@/components/shared/layout'
import { LoadingState, ErrorState } from '@/components/shared/states'
import { SaveReportButton } from '@/components/shared/save-report-button'
import * as Icons from 'lucide-react'

function groundingStatus(g: MarketingRunResult['grounding']): 'grounded' | 'partial' | 'review' {
  if (g.status === 'clean') return 'grounded'
  if (g.status === 'partial') return 'partial'
  return 'review'
}

const OBJECTIVES = [
  { value: 'Revenue growth', key: 'revenueGrowth' },
  { value: 'Customer retention', key: 'customerRetention' },
  { value: 'Win-back / reactivation', key: 'winBack' },
  { value: 'New customer acquisition', key: 'newAcquisition' },
  { value: 'Loyalty & advocacy', key: 'loyaltyAdvocacy' },
  { value: 'Maximise AOV', key: 'maximiseAov' },
] as const
const CHANNEL_OPTIONS = [
  { value: 'Email', key: 'email' },
  { value: 'SMS', key: 'sms' },
  { value: 'Paid social', key: 'paidSocial' },
  { value: 'Search ads', key: 'searchAds' },
  { value: 'Push', key: 'push' },
  { value: 'Retargeting', key: 'retargeting' },
  { value: 'Influencer', key: 'influencer' },
  { value: 'Content / SEO', key: 'contentSeo' },
  { value: 'Affiliate', key: 'affiliate' },
] as const
const SEGMENT_COLORS = ['bg-primary', 'bg-info', 'bg-success', 'bg-warning', 'bg-muted-foreground/50']

function formatMoney(n: number | null | undefined) {
  if (n == null) return '—'
  return `$${n.toLocaleString(undefined, { maximumFractionDigits: 0 })}`
}

function downloadCsv(ids: string[], filename: string) {
  const csv = ['customer', ...ids].join('\n')
  const blob = new Blob([csv], { type: 'text/csv' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

export function Marketing() {
  const t = useTranslations('marketing')
  const projectId = useAppStore((s) => s.activeProjectId)
  const lastForecastOutputs = useAppStore((s) => s.lastForecastOutputs)
  const lastChurnResult = useAppStore((s) => s.lastChurnResult)

  const [objective, setObjective] = React.useState<string>(OBJECTIVES[0].value)
  const [budget, setBudget] = React.useState('')
  const [channels, setChannels] = React.useState<string[]>(['Email', 'Paid social', 'Retargeting'])
  const [brandVoice, setBrandVoice] = React.useState('')
  const [businessContext, setBusinessContext] = React.useState('')
  const [useForecast, setUseForecast] = React.useState(false)
  const [useChurn, setUseChurn] = React.useState(false)

  const runMarketing = useRunMarketing(projectId)

  const toggleChannel = (c: string) => {
    setChannels((prev) => (prev.includes(c) ? prev.filter((x) => x !== c) : [...prev, c]))
  }

  const run = () => {
    runMarketing.mutate({
      objective, budget, channels, brand_voice: brandVoice, business_context: businessContext,
      forecast_outputs: useForecast ? lastForecastOutputs : null,
      churn_result: useChurn ? lastChurnResult : null,
    })
  }

  const payload = runMarketing.data

  return (
    <SectionScroll>
      <SectionHeader
        title={t('header.title')}
        description={t('header.description')}
        icon={<Icons.Megaphone className="size-4.5" />}
        actions={
          payload && (
            <Button size="sm" variant="outline" onClick={run} disabled={runMarketing.isPending}>
              <Icons.RefreshCw className="size-3.5" /> {t('header.reanalyze')}
            </Button>
          )
        }
      />

      {!payload && (
        <Card padding="lg" className="max-w-3xl">
          <h3 className="text-md font-semibold mb-1">{t('brief.title')}</h3>
          <p className="text-sm text-muted-foreground mb-4">
            {t('brief.description')}
          </p>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <Label className="text-sm">{t('brief.primaryObjective')}</Label>
              <Select value={objective} onValueChange={setObjective}>
                <SelectTrigger className="h-9 mt-1.5 w-full text-sm"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {OBJECTIVES.map((o) => <SelectItem key={o.value} value={o.value}>{t(`objectives.${o.key}`)}</SelectItem>)}
                </SelectContent>
              </Select>
              <Label className="text-sm mt-3 block">{t('brief.budgetLabel')}</Label>
              <Input className="mt-1.5" placeholder={t('brief.budgetPlaceholder')} value={budget} onChange={(e) => setBudget(e.target.value)} />
              <Label className="text-sm mt-3 block">{t('brief.brandVoiceLabel')}</Label>
              <Input className="mt-1.5" placeholder={t('brief.brandVoicePlaceholder')} value={brandVoice} onChange={(e) => setBrandVoice(e.target.value)} />
            </div>
            <div>
              <Label className="text-sm">{t('brief.allowedChannels')}</Label>
              <div className="mt-1.5 grid grid-cols-2 gap-1.5">
                {CHANNEL_OPTIONS.map((c) => (
                  <label key={c.value} className="flex items-center gap-2 text-sm rounded-md border border-border/60 px-2 py-1.5">
                    <Checkbox checked={channels.includes(c.value)} onCheckedChange={() => toggleChannel(c.value)} />
                    {t(`channels.${c.key}`)}
                  </label>
                ))}
              </div>
            </div>
          </div>
          <Label className="text-sm mt-3 block">{t('brief.additionalContext')}</Label>
          <Textarea className="mt-1.5 min-h-[70px]" placeholder={t('brief.additionalContextPlaceholder')} value={businessContext} onChange={(e) => setBusinessContext(e.target.value)} />

          <div className="mt-3 flex flex-col gap-1.5">
            <label className="flex items-center gap-2 text-sm">
              <Checkbox checked={useForecast} onCheckedChange={(v) => setUseForecast(!!v)} disabled={!lastForecastOutputs} />
              {t('brief.useForecast')} {!lastForecastOutputs && <span className="text-muted-foreground">{t('brief.useForecastHint')}</span>}
            </label>
            <label className="flex items-center gap-2 text-sm">
              <Checkbox checked={useChurn} onCheckedChange={(v) => setUseChurn(!!v)} disabled={!lastChurnResult?.available} />
              {t('brief.useChurn')} {!lastChurnResult?.available && <span className="text-muted-foreground">{t('brief.useChurnHint')}</span>}
            </label>
          </div>

          <Button className="mt-4" onClick={run} disabled={runMarketing.isPending}>
            {runMarketing.isPending
              ? <><Icons.Loader2 className="size-3.5 animate-spin" /> {t('brief.analyzing')}</>
              : <><Icons.Sparkles className="size-3.5" /> {t('brief.runAnalysis')}</>}
          </Button>
        </Card>
      )}

      {runMarketing.isPending && (
        <Card padding="none" className="mt-4">
          <LoadingState label={t('loading.label')} stage={t('loading.stage')} />
        </Card>
      )}

      {runMarketing.isError && (
        <Card padding="none" className="mt-4">
          <ErrorState body={(runMarketing.error as Error)?.message ?? t('error.runFailed')} onRetry={run} />
        </Card>
      )}

      {payload && (
        <MarketingResults
          projectId={projectId}
          payload={payload}
          objective={objective}
          budget={budget}
          channels={channels}
          brandVoice={brandVoice}
          businessContext={businessContext}
        />
      )}
    </SectionScroll>
  )
}

function MarketingResults({
  projectId, payload, objective, budget, channels, brandVoice, businessContext,
}: {
  projectId: string
  payload: MarketingRunResult
  objective: string
  budget: string
  channels: string[]
  brandVoice: string
  businessContext: string
}) {
  const t = useTranslations('marketing')
  const mk = payload.marketing_kpis
  return (
    <>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5">
        <Card padding="default">
          <p className="text-2xs uppercase tracking-wider text-muted-foreground/70 font-medium">{t('kpis.repeatRate')}</p>
          <p className="mt-1 text-xl font-semibold tabular-nums">{mk.repeat_purchase_rate != null ? `${mk.repeat_purchase_rate}%` : '—'}</p>
        </Card>
        <Card padding="default">
          <p className="text-2xs uppercase tracking-wider text-muted-foreground/70 font-medium">{t('kpis.avgCustomerValue')}</p>
          <p className="mt-1 text-xl font-semibold tabular-nums">{formatMoney(mk.avg_customer_value)}</p>
        </Card>
        <Card padding="default">
          <p className="text-2xs uppercase tracking-wider text-muted-foreground/70 font-medium">{t('kpis.aov')}</p>
          <p className="mt-1 text-xl font-semibold tabular-nums">{mk.aov != null ? `$${Number(mk.aov).toFixed(2)}` : '—'}</p>
        </Card>
        <Card padding="default">
          <p className="text-2xs uppercase tracking-wider text-muted-foreground/70 font-medium">{t('kpis.churnRiskRfm')}</p>
          <p className="mt-1 text-xl font-semibold tabular-nums">{mk.churn_risk_pct != null ? `${mk.churn_risk_pct}%` : '—'}</p>
        </Card>
      </div>

      <Tabs defaultValue="segments">
        <TabsList className="bg-muted/30 h-9">
          <TabsTrigger value="segments" className="text-sm">{t('tabs.segments')}</TabsTrigger>
          <TabsTrigger value="churn" className="text-sm">{t('tabs.churn')}</TabsTrigger>
          <TabsTrigger value="strategy" className="text-sm">{t('tabs.strategy')}</TabsTrigger>
          <TabsTrigger value="campaign" className="text-sm">{t('tabs.campaign')}</TabsTrigger>
          <TabsTrigger value="copy" className="text-sm">{t('tabs.copy')}</TabsTrigger>
        </TabsList>

        <TabsContent value="segments" className="mt-4">
          <SegmentsTab rfm={payload.rfm} />
        </TabsContent>
        <TabsContent value="churn" className="mt-4">
          <ChurnMarketingTab churn={payload.churn} />
        </TabsContent>
        <TabsContent value="strategy" className="mt-4">
          <StrategyTab projectId={projectId} payload={payload} />
        </TabsContent>
        <TabsContent value="campaign" className="mt-4">
          <CampaignBuilderTab
            projectId={projectId} payload={payload}
            objective={objective} budget={budget} channels={channels}
            brandVoice={brandVoice} businessContext={businessContext}
          />
        </TabsContent>
        <TabsContent value="copy" className="mt-4">
          <AdCopyTab projectId={projectId} rfm={payload.rfm} brandVoice={brandVoice} />
        </TabsContent>
      </Tabs>
    </>
  )
}

function SegmentsTab({ rfm }: { rfm: MarketingRunResult['rfm'] }) {
  const t = useTranslations('marketing')
  if (!rfm.available || !rfm.segments) {
    return (
      <Card padding="lg">
        <p className="text-base text-muted-foreground">{rfm.reason || t('segments.unavailable')}</p>
      </Card>
    )
  }
  const segmentNames = rfm.segment_order ?? Object.keys(rfm.segments)
  return (
    <div className="space-y-4">
      <p className="text-xs text-muted-foreground">
        {t('segments.summary', {
          count: (rfm.total_customers ?? 0).toLocaleString(),
          revenue: formatMoney(rfm.total_revenue),
          snapshot: rfm.snapshot_date || t('segments.notAvailable'),
        })}
      </p>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-3">
        {segmentNames.map((name, i) => {
          const s = rfm.segments![name]
          if (!s) return null
          const color = SEGMENT_COLORS[i % SEGMENT_COLORS.length]
          return (
            <Card key={name} padding="default">
              <div className="flex items-center justify-between mb-2">
                <div className={cn('flex size-7 items-center justify-center rounded-md text-2xs font-bold text-primary-foreground', color)}>
                  {name.charAt(0)}
                </div>
                <Badge variant="outline" className="text-2xs">{s.count.toLocaleString()}</Badge>
              </div>
              <p className="text-base font-semibold">{name}</p>
              <p className="mt-1 text-lg font-semibold tabular-nums">{formatMoney(s.revenue)}</p>
              <div className="mt-1.5 flex items-center justify-between text-2xs text-muted-foreground">
                <span>{t('segments.avg', { value: formatMoney(s.avg_monetary) })}</span>
                <span>{t('segments.pctOfRev', { pct: s.revenue_pct })}</span>
              </div>
              <div className="mt-2 h-1 rounded-full bg-muted/40 overflow-hidden">
                <div className={cn('h-full rounded-full', color)} style={{ width: `${s.revenue_pct}%` }} />
              </div>
              <p className="mt-2.5 text-xs text-muted-foreground/80 leading-snug">{s.playbook}</p>
            </Card>
          )
        })}
      </div>
    </div>
  )
}

function ChurnMarketingTab({ churn }: { churn: MarketingRunResult['churn'] }) {
  const t = useTranslations('marketing')
  if (!churn.available) {
    return (
      <Card padding="lg">
        <p className="text-base text-muted-foreground">
          {churn.reason || t('churn.unavailable')}
        </p>
      </Card>
    )
  }
  const targetLists = churn._target_lists
  const bySeg = churn.churn_risk_by_rfm_segment ?? {}
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-3 mb-2">
        <Card padding="default">
          <div className="flex items-center justify-between">
            <p className="text-sm text-muted-foreground">{t('churn.highRiskCustomers')}</p>
            <Icons.ShieldAlert className="size-4 text-destructive" />
          </div>
          <p className="mt-1 text-2xl font-semibold text-destructive tabular-nums">{churn.risk_distribution?.high ?? '—'}</p>
          <p className="text-xs text-muted-foreground mt-0.5">{t('churn.acrossAllSegments')}</p>
        </Card>
        <Card padding="default">
          <div className="flex items-center justify-between">
            <p className="text-sm text-muted-foreground">{t('churn.expectedRevenueAtRisk')}</p>
            <Icons.TrendingDown className="size-4 text-warning" />
          </div>
          <p className="mt-1 text-2xl font-semibold text-warning tabular-nums">{formatMoney(churn.expected_revenue_at_risk)}</p>
          <p className="text-xs text-muted-foreground mt-0.5">{t('churn.overModelHorizon')}</p>
        </Card>
        <Card padding="default">
          <div className="flex items-center justify-between">
            <p className="text-sm text-muted-foreground">{t('churn.modelAuc')}</p>
            <Icons.Gauge className="size-4 text-success" />
          </div>
          <p className="mt-1 text-2xl font-semibold text-success tabular-nums">{churn.model_auc != null ? churn.model_auc.toFixed(3) : '—'}</p>
          <p className="text-xs text-muted-foreground mt-0.5">{churn.model_name || '—'}</p>
        </Card>
      </div>

      <Card padding="default">
        <h3 className="text-base font-semibold mb-3">{t('churn.bySegmentTitle')}</h3>
        {Object.keys(bySeg).length ? (
          <div className="space-y-3">
            {Object.entries(bySeg).map(([seg, v]) => (
              <div key={seg}>
                <div className="flex items-center justify-between text-sm mb-1.5">
                  <span className="font-medium">{seg}</span>
                  <span className="text-muted-foreground">
                    {t.rich('churn.segmentStats', {
                      scored: v.customers_scored,
                      prob: v.avg_churn_probability.toFixed(2),
                      highRisk: v.high_risk_count,
                      pct: v.high_risk_pct,
                      risk: (chunks) => <span className="text-destructive"> {chunks}</span>,
                    })}
                  </span>
                </div>
                <div className="h-2 rounded-full overflow-hidden bg-muted/30">
                  <div className="h-full bg-destructive/70" style={{ width: `${v.high_risk_pct}%` }} />
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">{t('churn.noBreakdown')}</p>
        )}
        {targetLists && (
          <div className="mt-4 flex items-center gap-2 pt-3 border-t border-border/60">
            {targetLists.high_risk && targetLists.high_risk.length > 0 && (
              <Button size="sm" variant="outline" onClick={() => downloadCsv(targetLists.high_risk!, 'churn_high_risk_audience.csv')}>
                <Icons.Download className="size-3.5" /> {t('churn.highRiskAudience', { count: targetLists.high_risk.length })}
              </Button>
            )}
            {targetLists.medium_risk && targetLists.medium_risk.length > 0 && (
              <Button size="sm" variant="outline" onClick={() => downloadCsv(targetLists.medium_risk!, 'churn_medium_risk_audience.csv')}>
                <Icons.Download className="size-3.5" /> {t('churn.mediumRiskAudience', { count: targetLists.medium_risk.length })}
              </Button>
            )}
          </div>
        )}
      </Card>
    </div>
  )
}

function StrategyTab({ projectId, payload }: { projectId: string; payload: MarketingRunResult }) {
  const t = useTranslations('marketing')
  const [chatMessages, setChatMessages] = React.useState<ChatMessage[]>([])
  const [chatInput, setChatInput] = React.useState('')
  const chat = useMarketingChat(projectId)

  const marketingPayload = { rfm: payload.rfm, marketing_kpis: payload.marketing_kpis, channels: payload.channels, churn: payload.churn }

  const sendChat = () => {
    if (!chatInput.trim()) return
    const message = chatInput.trim()
    setChatInput('')
    const nextHistory: ChatMessage[] = [...chatMessages, { role: 'user', content: message }]
    setChatMessages(nextHistory)
    chat.mutate(
      { message, report_md: payload.report_md, marketing_payload: marketingPayload, history: chatMessages },
      { onSuccess: (res) => setChatMessages((h) => [...h, { role: 'assistant', content: res.answer }]) }
    )
  }

  return (
    <div className="space-y-4">
      <Card padding="lg" className="prose-report">
        <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
          <div className="flex items-center gap-2">
            <GroundingBadge status={groundingStatus(payload.grounding)} />
            <p className="text-xs text-muted-foreground">{payload.grounding.label}</p>
          </div>
          <SaveReportButton
            key={payload.report_md}
            projectId={projectId}
            type="marketing"
            title={t('strategy.reportTitle')}
            markdown={payload.report_md}
            grounding={payload.grounding}
          />
        </div>
        <div className="prose prose-sm dark:prose-invert max-w-none text-base leading-relaxed">
          <ReactMarkdown>{payload.report_md}</ReactMarkdown>
        </div>
      </Card>

      <Card padding="default">
        <h3 className="text-base font-semibold mb-3">{t('strategy.askHeading')}</h3>
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
          {chat.isPending && <LoadingState label={t('strategy.thinking')} />}
        </div>
        <div className="flex items-center gap-2">
          <Input
            placeholder={t('strategy.chatPlaceholder')}
            value={chatInput}
            onChange={(e) => setChatInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') sendChat() }}
          />
          <Button size="sm" onClick={sendChat} disabled={chat.isPending || !chatInput.trim()}>
            <Icons.Send className="size-3.5" />
          </Button>
        </div>
      </Card>
    </div>
  )
}

function CampaignBuilderTab({
  projectId, payload, objective, budget, channels, brandVoice, businessContext,
}: {
  projectId: string
  payload: MarketingRunResult
  objective: string
  budget: string
  channels: string[]
  brandVoice: string
  businessContext: string
}) {
  const t = useTranslations('marketing')
  const generatePlan = useGenerateCampaignPlan(projectId)

  const generate = () => {
    generatePlan.mutate({
      marketing_payload: { rfm: payload.rfm, marketing_kpis: payload.marketing_kpis, channels: payload.channels, churn: payload.churn },
      objective, budget, channels, brand_voice: brandVoice, business_context: businessContext,
    })
  }

  return (
    <Card padding="lg">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h3 className="text-md font-semibold">{t('campaign.title')}</h3>
          <p className="mt-1 text-sm text-muted-foreground">{t('campaign.description')}</p>
        </div>
        <Button size="sm" onClick={generate} disabled={generatePlan.isPending}>
          {generatePlan.isPending
            ? <><Icons.Loader2 className="size-3.5 animate-spin" /> {t('campaign.designing')}</>
            : <><Icons.Sparkles className="size-3.5" /> {t('campaign.generate')}</>}
        </Button>
      </div>

      {generatePlan.isError && (
        <ErrorState body={t('campaign.generateError')} onRetry={generate} />
      )}

      {generatePlan.data && (
        generatePlan.data.campaigns.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t('campaign.noCampaigns')} {generatePlan.data.error}</p>
        ) : (
          <div className="space-y-3">
            {generatePlan.data.summary && (
              <p className="text-sm"><span className="font-medium">{t('campaign.planSummary')}</span> {generatePlan.data.summary}</p>
            )}
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-2xs uppercase tracking-wider text-muted-foreground/70 font-medium">
                  <tr className="border-b border-border">
                    <th className="py-2 pe-3 text-start">{t('campaign.table.campaign')}</th>
                    <th className="py-2 px-3 text-start">{t('campaign.table.segment')}</th>
                    <th className="py-2 px-3 text-start">{t('campaign.table.channel')}</th>
                    <th className="py-2 px-3 text-end">{t('campaign.table.budgetPct')}</th>
                    <th className="py-2 ps-3 text-start">{t('campaign.table.priority')}</th>
                  </tr>
                </thead>
                <tbody>
                  {generatePlan.data.campaigns.map((c, i) => (
                    <tr key={i} className="border-b border-border/60 last:border-0">
                      <td className="py-2.5 pe-3 font-medium">{c.name}</td>
                      <td className="py-2.5 px-3">{c.target_segment}</td>
                      <td className="py-2.5 px-3">{c.channel}</td>
                      <td className="py-2.5 px-3 text-end tabular-nums">{c.budget_allocation_pct ?? '—'}</td>
                      <td className="py-2.5 ps-3">
                        <Badge variant={c.priority === 'high' ? 'danger' : c.priority === 'medium' ? 'warning' : 'outline'} className="text-2xs">{c.priority}</Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="space-y-2">
              {generatePlan.data.campaigns.map((c, i) => (
                <div key={i} className="rounded-lg border border-border/60 bg-background/40 p-3">
                  <p className="text-sm font-medium">{c.name} → {c.target_segment}</p>
                  <p className="text-xs text-muted-foreground mt-1"><span className="font-medium text-foreground">{t('campaign.offer')}</span> {c.offer || '—'}</p>
                  <p className="text-xs text-muted-foreground mt-0.5"><span className="font-medium text-foreground">{t('campaign.angle')}</span> {c.message_angle || '—'}</p>
                  <p className="text-xs text-muted-foreground mt-0.5"><span className="font-medium text-foreground">{t('campaign.expectedImpact')}</span> {c.expected_impact || '—'}</p>
                </div>
              ))}
            </div>
          </div>
        )
      )}
    </Card>
  )
}

function AdCopyTab({ projectId, rfm, brandVoice }: { projectId: string; rfm: MarketingRunResult['rfm']; brandVoice: string }) {
  const t = useTranslations('marketing')
  const segmentNames = rfm.available && rfm.segments ? (rfm.segment_order ?? Object.keys(rfm.segments)) : []
  const [segment, setSegment] = React.useState('')
  const effectiveSegment = segment || segmentNames[0] || ''
  const [channel, setChannel] = React.useState('Email')
  const [platform, setPlatform] = React.useState('Generic')
  const [offer, setOffer] = React.useState('')
  const generateCopy = useGenerateAdCopy(projectId)

  const generate = () => {
    if (!effectiveSegment) return
    const stats = rfm.segments?.[effectiveSegment]
    generateCopy.mutate({
      segment: effectiveSegment, channel, platform, offer, brand_voice: brandVoice,
      segment_stats: stats, playbook: stats?.playbook ?? '',
    })
  }

  if (!segmentNames.length) {
    return <Card padding="lg"><p className="text-base text-muted-foreground">{t('adCopy.noSegments')}</p></Card>
  }

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
      <div className="space-y-3">
        <Card padding="default">
          <h3 className="text-base font-semibold mb-1">{t('adCopy.generateHeading')}</h3>
          <p className="text-xs text-muted-foreground mb-3">{t('adCopy.generateDescription')}</p>
          <div className="space-y-2">
            <div>
              <label className="text-2xs uppercase tracking-wider text-muted-foreground/70 font-medium">{t('adCopy.segmentLabel')}</label>
              <Select value={effectiveSegment} onValueChange={setSegment}>
                <SelectTrigger className="h-9 w-full mt-1 text-sm"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {segmentNames.map((s) => <SelectItem key={s} value={s}>{s} · {rfm.segments?.[s]?.count.toLocaleString()}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div>
              <label className="text-2xs uppercase tracking-wider text-muted-foreground/70 font-medium">{t('adCopy.channelLabel')}</label>
              <div className="mt-1 grid grid-cols-3 gap-1.5">
                {[
                  { value: 'Email', key: 'email' },
                  { value: 'SMS', key: 'sms' },
                  { value: 'Push', key: 'push' },
                ].map((c) => (
                  <button key={c.value} onClick={() => setChannel(c.value)} className={cn('rounded-md border py-1.5 text-xs', channel === c.value ? 'border-primary bg-primary/[0.05] text-primary' : 'border-border')}>{t(`channels.${c.key}`)}</button>
                ))}
              </div>
            </div>
            <div>
              <label className="text-2xs uppercase tracking-wider text-muted-foreground/70 font-medium">{t('adCopy.platformLabel')}</label>
              <Select value={platform} onValueChange={setPlatform}>
                <SelectTrigger className="h-9 w-full mt-1 text-sm"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {['Generic', 'Instagram', 'Facebook', 'TikTok', 'Google', 'Klaviyo / Email'].map((p) => (
                    <SelectItem key={p} value={p}>{p}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div>
              <label className="text-2xs uppercase tracking-wider text-muted-foreground/70 font-medium">{t('adCopy.offerLabel')}</label>
              <Input className="mt-1" placeholder={t('adCopy.offerPlaceholder')} value={offer} onChange={(e) => setOffer(e.target.value)} />
            </div>
          </div>
          <Button size="sm" className="w-full mt-3" onClick={generate} disabled={generateCopy.isPending}>
            {generateCopy.isPending ? <><Icons.Loader2 className="size-3.5 animate-spin" /> {t('adCopy.writing')}</> : <><Icons.Sparkles className="size-3.5" /> {t('adCopy.generate')}</>}
          </Button>
        </Card>
      </div>

      <div className="lg:col-span-2 space-y-3">
        {generateCopy.isError && <ErrorState body={t('adCopy.generateError')} onRetry={generate} />}
        {generateCopy.data?.error && <p className="text-sm text-destructive">{t('adCopy.generationFailed', { error: generateCopy.data.error })}</p>}
        {generateCopy.data && !generateCopy.data.error && (
          <>
            {generateCopy.data.headlines.length > 0 && (
              <CopyCard icon="Mail" title={t('adCopy.headlines')} items={generateCopy.data.headlines} />
            )}
            {generateCopy.data.primary_text.length > 0 && (
              <CopyCard icon="MessageSquare" title={t('adCopy.bodyCopy')} items={generateCopy.data.primary_text} />
            )}
            <Card padding="default" className="relative">
              <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-primary/40 to-transparent" />
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-1.5 text-2xs uppercase tracking-wider text-primary font-semibold">
                  <Icons.Smartphone className="size-3" /> {t('adCopy.ctasLabel')}
                </div>
                <Badge variant="success" className="text-2xs">{t('adCopy.grounded')}</Badge>
              </div>
              {generateCopy.data.cta.length > 0 && (
                <p className="text-sm mb-2"><span className="font-medium">{t('adCopy.ctas')}</span> {generateCopy.data.cta.join(' · ')}</p>
              )}
              {generateCopy.data.email_subject && (
                <p className="text-sm"><span className="font-medium">{t('adCopy.emailSubject')}</span> {generateCopy.data.email_subject}</p>
              )}
              {generateCopy.data.email_preview && (
                <p className="text-sm mt-1"><span className="font-medium">{t('adCopy.preview')}</span> {generateCopy.data.email_preview}</p>
              )}
              {generateCopy.data.sms && (
                <div className="mt-2 rounded-md border border-border/60 bg-background/40 p-3 text-sm">{generateCopy.data.sms}</div>
              )}
              {generateCopy.data.hashtags.length > 0 && (
                <p className="mt-2 text-sm text-muted-foreground">{generateCopy.data.hashtags.map((h) => `#${h.replace(/^#/, '')}`).join(' ')}</p>
              )}
              {generateCopy.data.notes && <p className="mt-2 text-xs text-muted-foreground/80">{generateCopy.data.notes}</p>}
            </Card>
          </>
        )}
      </div>
    </div>
  )
}

function CopyCard({ icon, title, items }: { icon: string; title: string; items: string[] }) {
  const t = useTranslations('marketing')
  const Icon = (Icons as any)[icon] ?? Icons.FileText
  return (
    <Card padding="default" className="relative">
      <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-primary/40 to-transparent" />
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-1.5 text-2xs uppercase tracking-wider text-primary font-semibold">
          <Icon className="size-3" /> {title}
        </div>
        <Badge variant="success" className="text-2xs">{t('adCopy.grounded')}</Badge>
      </div>
      <div className="space-y-1.5">
        {items.map((s, i) => (
          <div key={i} className="rounded-md border border-border/60 bg-background/40 p-2.5 text-sm flex items-start gap-2">
            <Icons.Copy className="size-3 text-muted-foreground/60 mt-0.5 shrink-0" />
            <span className="flex-1">{s}</span>
          </div>
        ))}
      </div>
    </Card>
  )
}
