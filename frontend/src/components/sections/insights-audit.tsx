'use client'

import * as React from 'react'
import { useTranslations } from 'next-intl'
import type { EvidenceItem, Figure, Verification } from '@/lib/queries/insights'
import { useAppStore } from '@/lib/store'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import * as Icons from 'lucide-react'

/**
 * The audit surface for an insights report.
 *
 * The report's credibility rests on a claim — every number was computed, not
 * written — and a claim the reader cannot check is just a nicer-sounding claim.
 * These three blocks make it checkable: where each figure came from, why the
 * findings are in that order, and what the data provably could not answer.
 */

const TAG_STYLE: Record<EvidenceItem['tag'], { variant: 'danger' | 'warning' | 'info' | 'success' | 'default'; icon: keyof typeof Icons }> = {
  THREAT: { variant: 'danger', icon: 'TrendingDown' },
  LEAK: { variant: 'danger', icon: 'Droplets' },
  RISK: { variant: 'warning', icon: 'AlertTriangle' },
  OPPORTUNITY: { variant: 'info', icon: 'Target' },
  STRENGTH: { variant: 'success', icon: 'TrendingUp' },
  CAVEAT: { variant: 'default', icon: 'Info' },
}

function formatMoney(value: number) {
  return value.toLocaleString(undefined, { maximumFractionDigits: 0 })
}

export function VerificationPanel({
  verification,
  figures,
}: {
  verification: Verification
  figures: Figure[]
}) {
  const t = useTranslations('insights.verification')
  const [open, setOpen] = React.useState(false)

  const problems = [
    ...verification.unverified_figures.map((f) => `${f.figure} — ${f.context}`),
    ...verification.unknown_citations.map((k) => `Unknown citation: ${k}`),
    ...verification.unsupported_currency_claims.map((c) => `Unsupported currency symbol: ${c}`),
    ...verification.misvalued_actions,
    ...verification.ambiguous_citations,
  ]

  return (
    <Card padding="default" className="mb-4">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="min-w-0">
          <h3 className="text-base font-semibold flex items-center gap-2">
            <Icons.ShieldCheck className="size-4 text-success" />
            {t('title')}
          </h3>
          <ul className="mt-2 space-y-1 text-sm text-muted-foreground">
            <li>{t('fromEngine', { count: verification.figures_cited_from_engine })}</li>
            {verification.figures_typed_by_model > 0 && (
              <li>
                {t('typed', {
                  count: verification.figures_typed_by_model,
                  verified: verification.typed_and_verified,
                })}
              </li>
            )}
            {verification.corrected && <li>{t('corrected')}</li>}
          </ul>
        </div>
        {figures.length > 0 && (
          <Button size="sm" variant="outline" onClick={() => setOpen((v) => !v)}>
            <Icons.ListChecks className="size-3.5" />
            {open ? t('hideFigures') : t('showFigures', { count: figures.length })}
          </Button>
        )}
      </div>

      {problems.length > 0 && (
        <div className="mt-3 rounded-md border border-warning/30 bg-warning/8 p-3">
          <p className="text-sm font-medium text-warning">{t('problemsTitle')}</p>
          <ul className="mt-1.5 space-y-1 text-sm text-muted-foreground list-disc ps-4">
            {problems.map((p, i) => <li key={i}>{p}</li>)}
          </ul>
        </div>
      )}

      {open && (
        <div className="mt-4">
          <p className="text-sm text-muted-foreground mb-2">{t('figuresSubtitle')}</p>
          {/* Formulas are long; the table scrolls inside itself so the page never does. */}
          <div className="overflow-x-auto rounded-md border border-border/60">
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="bg-muted/30 text-muted-foreground">
                  <th className="text-start font-medium px-2.5 py-1.5">{t('colFigure')}</th>
                  <th className="text-end font-medium px-2.5 py-1.5 whitespace-nowrap">{t('colValue')}</th>
                  <th className="text-start font-medium px-2.5 py-1.5">{t('colHow')}</th>
                </tr>
              </thead>
              <tbody>
                {figures.map((f) => (
                  <tr key={f.key} className="border-t border-border/50 align-top">
                    <td className="px-2.5 py-1.5">
                      {f.label}
                      {f.quality === 'scenario' && (
                        <Badge variant="warning" className="ms-1.5">{t('scenarioTag')}</Badge>
                      )}
                    </td>
                    <td className="px-2.5 py-1.5 text-end font-mono whitespace-nowrap">{f.display}</td>
                    <td className="px-2.5 py-1.5 text-muted-foreground">
                      {f.formula}
                      {f.period && <span className="block opacity-70">{t('period')}: {f.period}</span>}
                      {f.assumption && (
                        <span className="block text-warning/90">
                          {t('scenarioNote')} {f.assumption}
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </Card>
  )
}

export function PriorityList({ evidence }: { evidence: EvidenceItem[] }) {
  const t = useTranslations('insights.priorities')
  if (evidence.length === 0) return null

  return (
    <Card padding="default" className="mb-4">
      <h3 className="text-base font-semibold">{t('title')}</h3>
      <p className="text-sm text-muted-foreground mb-3">{t('subtitle')}</p>
      <ol className="space-y-2.5">
        {evidence.map((item) => {
          const style = TAG_STYLE[item.tag] ?? TAG_STYLE.CAVEAT
          const Icon = Icons[style.icon] as React.ComponentType<{ className?: string }>
          return (
            <li key={item.rank} className="flex gap-2.5">
              <Icon className="size-4 mt-0.5 shrink-0 opacity-70" />
              <div className="min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <Badge variant={style.variant}>{item.tag}</Badge>
                  {item.money_at_stake > 0 && (
                    <span className="text-xs text-muted-foreground font-mono">
                      {formatMoney(item.money_at_stake)} {t('atStake')}
                    </span>
                  )}
                </div>
                <p className="text-sm leading-relaxed mt-1">{item.text}</p>
                {DRILLABLE_TAGS.has(item.tag) && <WhyButton />}
              </div>
            </li>
          )
        })}
      </ol>
    </Card>
  )
}

// A finding worth drilling into is one about money that moved. Concentration
// risks and caveats are standing facts about the shape of the business — there
// is no *change* for a drill-down to attribute.
const DRILLABLE_TAGS = new Set(['THREAT', 'LEAK', 'OPPORTUNITY'])

/** "In which segment?" — the question that always follows a finding.
 *
 * Hands the drill-down its starting point instead of making the user re-enter
 * the same question on another page. The metric travels through the store; the
 * Root Cause section picks it up and clears it. */
function WhyButton() {
  const t = useTranslations('insights.priorities')
  const setSection = useAppStore((s) => s.setSection)
  const setMeasure = useAppStore((s) => s.setPendingRootCauseMeasure)
  return (
    <button
      onClick={() => { setMeasure('revenue'); setSection('root-cause') }}
      className="mt-1.5 inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs font-medium text-primary outline-none transition-colors hover:bg-primary/10 focus-visible:ring-[3px] focus-visible:ring-ring/50"
    >
      <Icons.Crosshair className="size-3" />
      {t('whichSegment')}
    </button>
  )
}

export function BlindSpots({ items }: { items: string[] }) {
  const t = useTranslations('insights.blindSpots')
  if (items.length === 0) return null

  return (
    <Card padding="default" className="mb-4">
      <h3 className="text-base font-semibold flex items-center gap-2">
        <Icons.EyeOff className="size-4 opacity-70" />
        {t('title')}
      </h3>
      <p className="text-sm text-muted-foreground mb-2.5">{t('subtitle')}</p>
      <ul className="space-y-1.5 text-sm leading-relaxed list-disc ps-4">
        {items.map((item, i) => <li key={i}>{item}</li>)}
      </ul>
    </Card>
  )
}
