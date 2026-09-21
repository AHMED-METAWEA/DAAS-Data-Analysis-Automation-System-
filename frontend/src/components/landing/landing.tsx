'use client'

import * as React from 'react'
import { useRouter } from 'next/navigation'
import { useTranslations } from 'next-intl'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import * as Icons from 'lucide-react'
import { Sparkline } from '@/components/ui/sparkline'

const agentList = [
  { key: 'cleaning', icon: 'Sparkles', color: 'text-info' },
  { key: 'visualization', icon: 'BarChart3', color: 'text-primary' },
  { key: 'insights', icon: 'Lightbulb', color: 'text-warning' },
  { key: 'forecasting', icon: 'TrendingUp', color: 'text-success' },
  { key: 'marketing', icon: 'Megaphone', color: 'text-chart-4' },
  { key: 'churn', icon: 'ShieldAlert', color: 'text-destructive' },
] as const

const pipeline = [
  { key: 'dataSources', icon: 'Database' },
  { key: 'schemaDiscovery', icon: 'Network' },
  { key: 'relationshipReview', icon: 'GitBranch' },
  { key: 'aiCleaning', icon: 'Sparkles' },
  { key: 'humanApproval', icon: 'CheckCircle2' },
  { key: 'reconciliation', icon: 'Scale' },
  { key: 'projectStorage', icon: 'Save' },
  { key: 'analytics', icon: 'BarChart3' },
  { key: 'visualization', icon: 'PieChart' },
  { key: 'insights', icon: 'Lightbulb' },
  { key: 'forecasting', icon: 'TrendingUp' },
  { key: 'marketingIntelligence', icon: 'Megaphone' },
  { key: 'churnPrediction', icon: 'ShieldAlert' },
  { key: 'groundedReports', icon: 'FileText' },
] as const

const featureCards = [
  { key: 'connectData', icon: 'Database' },
  { key: 'dataPipeline', icon: 'GitBranch' },
  { key: 'analytics', icon: 'BarChart3' },
  { key: 'insights', icon: 'Lightbulb' },
  { key: 'forecasting', icon: 'TrendingUp' },
  { key: 'marketing', icon: 'Megaphone' },
  { key: 'churn', icon: 'ShieldAlert' },
  { key: 'trust', icon: 'ShieldCheck' },
] as const

const howItWorksSteps = [
  { key: 'bringData', icon: 'Database', n: '01' },
  { key: 'trustData', icon: 'ShieldCheck', n: '02' },
  { key: 'decideConfidently', icon: 'Sparkles', n: '03' },
] as const

const humanLoopItems = [
  { key: 'relationshipReview', icon: 'GitBranch' },
  { key: 'cleaningPlans', icon: 'Sparkles' },
  { key: 'reconciliationCheckpoint', icon: 'Scale' },
  { key: 'aiActionApproval', icon: 'ShieldCheck' },
] as const

type PricingTier = {
  name: string
  price: string
  tagline: string
  features: string[]
  cta: string
  featured: boolean
}

type FooterColumn = {
  title: string
  links: string[]
}

type TrustStat = {
  stat: string
  label: string
}

type FaqItem = {
  q: string
  a: string
}

export function Landing() {
  const t = useTranslations('landing')
  const router = useRouter()
  const openLogin = () => router.push('/login')
  const openApp = (section: string = 'command-center') => router.push(`/${section}`)
  const [openFaq, setOpenFaq] = React.useState<number | null>(0)

  const trustStats = t.raw('trust.stats') as TrustStat[]
  const pricingTiers = t.raw('pricing.tiers') as PricingTier[]
  const faqs = t.raw('faq.items') as FaqItem[]
  const footerColumns = t.raw('footer.columns') as FooterColumn[]

  return (
    <div className="min-h-screen bg-background text-foreground">
      {/* ===== Top nav ===== */}
      <header className="sticky top-0 z-40 border-b border-border/60 bg-background/80 backdrop-blur-xl">
        <div className="mx-auto flex h-14 max-w-7xl items-center gap-4 px-5">
          <div className="flex items-center gap-2.5">
            <div className="flex size-7 items-center justify-center rounded-lg bg-primary/15 ring-1 ring-inset ring-primary/25">
              <Icons.Activity className="size-4 text-primary" />
            </div>
            <div>
              <p className="text-md font-semibold leading-none tracking-tight">{t('nav.brand')}</p>
              <p className="mt-0.5 text-2xs text-muted-foreground leading-none">{t('nav.brandSubtitle')}</p>
            </div>
          </div>
          <nav className="ms-6 hidden md:flex items-center gap-1 text-base">
            {(['product', 'agents', 'pipeline', 'trust', 'pricing', 'docs'] as const).map((n) => (
              <button key={n} className="rounded-md px-2.5 py-1.5 text-muted-foreground hover:text-foreground hover:bg-accent/40 transition-colors">{t(`nav.links.${n}`)}</button>
            ))}
          </nav>
          <div className="ms-auto flex items-center gap-2">
            <Button variant="ghost" size="sm" onClick={openLogin} className="text-base">{t('nav.signIn')}</Button>
            <Button size="sm" onClick={openLogin} className="text-base">
              {t('nav.openWorkspace')} <Icons.ArrowRight className="size-3.5 rtl:-scale-x-100" />
            </Button>
          </div>
        </div>
      </header>

      {/* ===== Hero ===== */}
      <section className="relative overflow-hidden">
        {/* Background */}
        <div className="absolute inset-0 bg-grid opacity-40" />
        <div className="absolute inset-0 bg-gradient-to-b from-transparent via-transparent to-background" />
        <div className="absolute left-1/2 top-0 -translate-x-1/2 h-[480px] w-[820px] rounded-full bg-primary/10 blur-[120px] pointer-events-none" />

        <div className="relative mx-auto max-w-7xl px-5 pt-20 pb-16 lg:pt-28 lg:pb-24">
          <div className="max-w-3xl">
            <div className="inline-flex items-center gap-2 rounded-full border border-border/60 bg-card/50 px-3 py-1 text-xs text-muted-foreground mb-6">
              <span className="relative flex size-1.5">
                <span className="absolute inline-flex h-full w-full rounded-full bg-primary opacity-75 animate-ping" />
                <span className="relative inline-flex size-1.5 rounded-full bg-primary" />
              </span>
              {t('hero.badge')}
            </div>

            <h1 className="text-4xl sm:text-5xl lg:text-6xl font-semibold leading-[1.02] tracking-[-0.02em]">
              {t('hero.titlePrefix')}<span className="text-gradient-brand">{t('hero.titleHighlight')}</span>
            </h1>
            <p className="mt-5 max-w-2xl text-lg sm:text-lg leading-relaxed text-muted-foreground">
              {t('hero.subtitle')}
            </p>

            <div className="mt-7 flex flex-wrap items-center gap-3">
              <Button size="lg" onClick={openLogin} className="h-11 px-5 text-md">
                <Icons.Plus className="size-4" /> {t('hero.startProject')}
              </Button>
              <Button size="lg" variant="outline" onClick={() => openApp('command-center')} className="h-11 px-5 text-md">
                <Icons.Play className="size-4" /> {t('hero.exploreDemo')}
              </Button>
            </div>

            <div className="mt-8 flex flex-wrap items-center gap-x-6 gap-y-2 text-xs text-muted-foreground">
              <span className="inline-flex items-center gap-1.5"><Icons.Check className="size-3.5 text-success" /> {t('hero.noCreditCard')}</span>
              <span className="inline-flex items-center gap-1.5"><Icons.Check className="size-3.5 text-success" /> {t('hero.byod')}</span>
              <span className="inline-flex items-center gap-1.5"><Icons.Check className="size-3.5 text-success" /> {t('hero.readOnly')}</span>
            </div>
          </div>

          {/* Hero preview — feels like an active operating system */}
          <div className="mt-14 relative">
            <HeroPreview onOpen={() => openApp('command-center')} />
          </div>
        </div>
      </section>

      {/* ===== Trust strip ===== */}
      <section className="border-y border-border/60 bg-card/30">
        <div className="mx-auto max-w-7xl px-5 py-8">
          <p className="text-center text-xs uppercase tracking-[0.18em] text-muted-foreground/60 font-medium">
            {t('trust.eyebrow')}
          </p>
          <div className="mt-5 grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-6">
            {trustStats.map((s) => (
              <div key={s.label} className="text-center">
                <p className="text-2xl font-semibold tracking-tight text-gradient-brand">{s.stat}</p>
                <p className="mt-1 text-xs text-muted-foreground">{s.label}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ===== How it works ===== */}
      <section className="py-20 lg:py-28">
        <div className="mx-auto max-w-7xl px-5">
          <SectionHeader
            eyebrow={t('howItWorks.eyebrow')}
            title={t('howItWorks.title')}
            body={t('howItWorks.body')}
          />
          <div className="mt-12 grid grid-cols-1 md:grid-cols-3 gap-4">
            {howItWorksSteps.map((s) => {
              const Icon = (Icons as any)[s.icon]
              return (
                <div key={s.n} className="rounded-xl border border-border/60 bg-card/40 p-6 relative overflow-hidden">
                  <div className="absolute top-4 end-4 text-4xl font-semibold text-muted-foreground/10 leading-none">{s.n}</div>
                  <div className="flex size-9 items-center justify-center rounded-lg bg-primary/12 text-primary ring-1 ring-inset ring-primary/20">
                    <Icon className="size-4.5" />
                  </div>
                  <h3 className="mt-4 text-md font-semibold">{t(`howItWorks.steps.${s.key}.title`)}</h3>
                  <p className="mt-1.5 text-base text-muted-foreground leading-relaxed">{t(`howItWorks.steps.${s.key}.body`)}</p>
                </div>
              )
            })}
          </div>

          {/* Pipeline */}
          <div className="mt-8 rounded-xl border border-border/60 bg-card/40 p-6">
            <p className="text-xs uppercase tracking-wider text-muted-foreground/70 font-medium mb-4">{t('howItWorks.pipelineLabel')}</p>
            <div className="flex flex-wrap items-center gap-2">
              {pipeline.map((p, i) => {
                const Icon = (Icons as any)[p.icon] ?? Icons.Circle
                return (
                  <React.Fragment key={`${p.key}-${i}`}>
                    <div className="inline-flex items-center gap-2 rounded-lg border border-border/60 bg-background/40 px-3 py-1.5 text-sm font-medium">
                      <Icon className="size-3.5 text-primary" />
                      {t(`howItWorks.pipeline.${p.key}`)}
                    </div>
                    {i < pipeline.length - 1 && <Icons.ChevronRight className="size-3.5 text-muted-foreground/40 rtl:-scale-x-100" />}
                  </React.Fragment>
                )
              })}
            </div>
          </div>
        </div>
      </section>

      {/* ===== Multi-agent system ===== */}
      <section className="py-20 lg:py-28 border-y border-border/60 bg-card/20">
        <div className="mx-auto max-w-7xl px-5">
          <SectionHeader
            eyebrow={t('agentsSection.eyebrow')}
            title={t('agentsSection.title')}
            body={t('agentsSection.body')}
          />
          <div className="mt-12 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {agentList.map((a) => {
              const Icon = (Icons as any)[a.icon] ?? Icons.Sparkles
              return (
                <div key={a.key} className="group relative rounded-xl border border-border/60 bg-card/60 p-5 hover:border-primary/30 hover:bg-card transition-all">
                  <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-primary/40 to-transparent opacity-0 group-hover:opacity-100 transition-opacity" />
                  <div className="flex items-center gap-3">
                    <div className={cn('flex size-9 items-center justify-center rounded-lg bg-muted/40 ring-1 ring-inset ring-border', a.color)}>
                      <Icon className="size-4.5" />
                    </div>
                    <div>
                      <p className="text-md font-semibold">{t(`agentsSection.list.${a.key}.fullName`)}</p>
                      <p className="text-xs text-muted-foreground">{t('agentsSection.specializedCapability')}</p>
                    </div>
                  </div>
                  <p className="mt-3 text-sm text-muted-foreground leading-relaxed">
                    {t(`agentsSection.list.${a.key}.body`)}
                  </p>
                </div>
              )
            })}
          </div>
        </div>
      </section>

      {/* ===== Feature sections ===== */}
      <section className="py-20 lg:py-28">
        <div className="mx-auto max-w-7xl px-5">
          <SectionHeader
            eyebrow={t('features.eyebrow')}
            title={t('features.title')}
            body={t('features.body')}
          />
          <div className="mt-12 grid grid-cols-1 md:grid-cols-2 gap-4">
            {featureCards.map((f) => {
              const Icon = (Icons as any)[f.icon] ?? Icons.Circle
              const points = t.raw(`features.items.${f.key}.points`) as string[]
              return (
                <button
                  key={f.key}
                  onClick={openLogin}
                  className="group text-start rounded-xl border border-border/60 bg-card/40 p-6 hover:border-primary/30 hover:bg-card/60 transition-all relative overflow-hidden"
                >
                  <div className="flex items-start gap-4">
                    <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-primary/12 text-primary ring-1 ring-inset ring-primary/20">
                      <Icon className="size-5" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <h3 className="text-md font-semibold">{t(`features.items.${f.key}.title`)}</h3>
                        <Icons.ArrowUpRight className="size-3.5 text-muted-foreground/60 group-hover:text-primary transition-colors" />
                      </div>
                      <p className="mt-1.5 text-base text-muted-foreground leading-relaxed">{t(`features.items.${f.key}.body`)}</p>
                      <div className="mt-3 flex flex-wrap gap-1.5">
                        {points.map((p) => (
                          <span key={p} className="inline-flex items-center gap-1 rounded-md bg-muted/40 px-2 py-0.5 text-xs text-muted-foreground">
                            <Icons.Check className="size-2.5 text-success" /> {p}
                          </span>
                        ))}
                      </div>
                    </div>
                  </div>
                </button>
              )
            })}
          </div>
        </div>
      </section>

      {/* ===== Human-in-the-loop ===== */}
      <section className="py-20 lg:py-28 border-y border-border/60 bg-card/20">
        <div className="mx-auto max-w-7xl px-5 grid grid-cols-1 lg:grid-cols-2 gap-12 items-center">
          <div>
            <SectionHeader
              eyebrow={t('humanLoop.eyebrow')}
              title={t('humanLoop.title')}
              body={t('humanLoop.body')}
              align="left"
            />
            <ul className="mt-8 space-y-3">
              {humanLoopItems.map((item) => {
                const Icon = (Icons as any)[item.icon]
                return (
                  <li key={item.key} className="flex items-start gap-3">
                    <div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary/12 text-primary ring-1 ring-inset ring-primary/20 mt-0.5">
                      <Icon className="size-4" />
                    </div>
                    <div>
                      <p className="text-base font-semibold">{t(`humanLoop.items.${item.key}.title`)}</p>
                      <p className="mt-0.5 text-sm text-muted-foreground leading-relaxed">{t(`humanLoop.items.${item.key}.body`)}</p>
                    </div>
                  </li>
                )
              })}
            </ul>
          </div>
          <ApprovalPreview />
        </div>
      </section>

      {/* ===== Testimonials placeholder ===== */}
      <section className="py-20 lg:py-28">
        <div className="mx-auto max-w-7xl px-5">
          <SectionHeader
            eyebrow={t('testimonials.eyebrow')}
            title={t('testimonials.title')}
            body={t('testimonials.body')}
          />
          <div className="mt-12 grid grid-cols-1 md:grid-cols-3 gap-4">
            {[1, 2, 3].map((i) => (
              <div key={i} className="rounded-xl border border-border/60 bg-card/40 p-6">
                <div className="flex items-center gap-1 text-warning">
                  {Array.from({ length: 5 }).map((_, j) => <Icons.Star key={j} className="size-3.5 fill-current" />)}
                </div>
                <p className="mt-3 text-base text-foreground/85 leading-relaxed italic">
                  “{t('testimonials.quote')}”
                </p>
                <div className="mt-4 flex items-center gap-2.5 pt-4 border-t border-border/60">
                  <div className="flex size-8 items-center justify-center rounded-full bg-gradient-to-br from-primary/30 to-info/20 text-xs font-semibold text-primary ring-1 ring-inset ring-primary/30">
                    {String.fromCharCode(64 + i)}
                  </div>
                  <div>
                    <p className="text-sm font-medium">{t('testimonials.customerName', { n: i })}</p>
                    <p className="text-xs text-muted-foreground">{t('testimonials.roleCompany')}</p>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ===== Pricing placeholder ===== */}
      <section className="py-20 lg:py-28 border-y border-border/60 bg-card/20">
        <div className="mx-auto max-w-7xl px-5">
          <SectionHeader
            eyebrow={t('pricing.eyebrow')}
            title={t('pricing.title')}
            body={t('pricing.body')}
          />
          <div className="mt-12 grid grid-cols-1 md:grid-cols-3 gap-4 max-w-5xl mx-auto">
            {pricingTiers.map((tier) => (
              <div
                key={tier.name}
                className={cn(
                  'relative rounded-xl border p-6',
                  tier.featured
                    ? 'border-primary/40 bg-card/80 ring-1 ring-primary/30 shadow-floating'
                    : 'border-border/60 bg-card/40'
                )}
              >
                {tier.featured && (
                  <Badge variant="brand" className="absolute -top-2.5 start-6">{t('pricing.mostPopular')}</Badge>
                )}
                <p className="text-md font-semibold">{tier.name}</p>
                <p className="mt-1 text-sm text-muted-foreground">{tier.tagline}</p>
                <p className="mt-4 text-3xl font-semibold tracking-tight">{tier.price}</p>
                <Button
                  className="mt-4 w-full"
                  variant={tier.featured ? 'default' : 'outline'}
                  onClick={openLogin}
                >
                  {tier.cta}
                </Button>
                <ul className="mt-5 space-y-2">
                  {tier.features.map((f) => (
                    <li key={f} className="flex items-center gap-2 text-sm text-muted-foreground">
                      <Icons.Check className="size-3.5 text-success shrink-0" /> {f}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
          <p className="mt-6 text-center text-xs text-muted-foreground">
            {t('pricing.footnote')}
          </p>
        </div>
      </section>

      {/* ===== FAQ ===== */}
      <section className="py-20 lg:py-28">
        <div className="mx-auto max-w-3xl px-5">
          <SectionHeader
            eyebrow={t('faq.eyebrow')}
            title={t('faq.title')}
            body={t('faq.body')}
          />
          <div className="mt-10 space-y-2.5">
            {faqs.map((f, i) => (
              <div key={i} className="rounded-xl border border-border/60 bg-card/40 overflow-hidden">
                <button
                  onClick={() => setOpenFaq(openFaq === i ? null : i)}
                  className="flex w-full items-center justify-between gap-4 px-5 py-4 text-start"
                >
                  <span className="text-md font-medium">{f.q}</span>
                  <Icons.ChevronDown className={cn('size-4 text-muted-foreground shrink-0 transition-transform', openFaq === i && 'rotate-180')} />
                </button>
                {openFaq === i && (
                  <div className="px-5 pb-4 -mt-1 animate-fade-up">
                    <p className="text-base text-muted-foreground leading-relaxed">{f.a}</p>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ===== Final CTA ===== */}
      <section className="py-20 lg:py-28 border-t border-border/60">
        <div className="mx-auto max-w-4xl px-5 text-center">
          <h2 className="text-3xl sm:text-4xl font-semibold leading-tight tracking-tight">
            {t('finalCta.titlePrefix')}<span className="text-gradient-brand">{t('finalCta.titleHighlight')}</span>
          </h2>
          <p className="mt-4 text-md text-muted-foreground max-w-xl mx-auto">
            {t('finalCta.body')}
          </p>
          <div className="mt-7 flex items-center justify-center gap-3">
            <Button size="lg" onClick={openLogin} className="h-11 px-5">
              <Icons.Plus className="size-4" /> {t('hero.startProject')}
            </Button>
            <Button size="lg" variant="outline" onClick={() => openApp('command-center')} className="h-11 px-5">
              {t('nav.openWorkspace')}
            </Button>
          </div>
        </div>
      </section>

      {/* ===== Footer ===== */}
      <footer className="border-t border-border/60 bg-card/20">
        <div className="mx-auto max-w-7xl px-5 py-12">
          <div className="grid grid-cols-2 md:grid-cols-5 gap-8">
            <div className="col-span-2">
              <div className="flex items-center gap-2.5">
                <div className="flex size-7 items-center justify-center rounded-lg bg-primary/15 ring-1 ring-inset ring-primary/25">
                  <Icons.Activity className="size-4 text-primary" />
                </div>
                <p className="text-md font-semibold">{t('nav.brand')}</p>
              </div>
              <p className="mt-3 text-sm text-muted-foreground max-w-xs leading-relaxed">
                {t('footer.tagline')}
              </p>
              <p className="mt-4 text-xs text-muted-foreground/60">{t('footer.copyright')}</p>
            </div>
            {footerColumns.map((col) => (
              <div key={col.title}>
                <p className="text-xs uppercase tracking-wider text-muted-foreground/70 font-semibold">{col.title}</p>
                <ul className="mt-3 space-y-2">
                  {col.links.map((l) => (
                    <li key={l}>
                      <button className="text-sm text-muted-foreground hover:text-foreground transition-colors">{l}</button>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </div>
      </footer>
    </div>
  )
}

function SectionHeader({ eyebrow, title, body, align = 'center' }: { eyebrow: string; title: string; body: string; align?: 'center' | 'left' }) {
  return (
    <div className={cn('max-w-2xl', align === 'center' && 'mx-auto text-center')}>
      <p className="text-xs uppercase tracking-[0.18em] text-primary font-semibold mb-3">{eyebrow}</p>
      <h2 className="text-2xl sm:text-3xl font-semibold leading-tight tracking-tight">{title}</h2>
      <p className="mt-3 text-md sm:text-md text-muted-foreground leading-relaxed">{body}</p>
    </div>
  )
}

const heroStateKey: Record<string, string> = {
  'completed': 'completed',
  'back-testing': 'backTesting',
  'waiting-approval': 'waitingApproval',
}

const heroKpis = [
  { key: 'revenue', v: '$1.84M', d: '+12.4%', s: [38, 42, 47, 52, 58, 64, 72, 78, 84] },
  { key: 'orders', v: '24,318', d: '+8.1%', s: [22, 24, 26, 28, 31, 33, 35, 36, 38] },
  { key: 'growth', v: '12.4%', d: '+2.1pp', s: [4, 5, 6, 7, 8, 9, 10, 11, 12] },
  { key: 'aov', v: '$75.62', d: '+3.9%', s: [62, 64, 66, 68, 69, 71, 72, 74, 76] },
  { key: 'customers', v: '8,412', d: '+5.7%', s: [62, 65, 68, 71, 73, 75, 78, 80, 84] },
  { key: 'atRisk', v: '$184K', d: '−4.2%', s: [22, 21, 20, 19, 18, 18, 17, 16, 16], danger: true },
] as const

const heroSidebarNav = ['commandCenter', 'data', 'visualization', 'insights', 'forecasting', 'marketing', 'churn', 'reports'] as const

function HeroPreview({ onOpen }: { onOpen: () => void }) {
  const t = useTranslations('landing')
  return (
    <div className="relative">
      <div className="absolute -inset-x-4 -top-4 bottom-0 rounded-2xl bg-gradient-to-b from-primary/10 to-transparent blur-2xl pointer-events-none" />
      <div
        className="relative rounded-2xl border border-border/60 glass-strong shadow-floating overflow-hidden cursor-pointer group"
        onClick={onOpen}
      >
        {/* Window chrome */}
        <div className="flex items-center gap-2 border-b border-border/60 px-4 py-2.5">
          <div className="flex gap-1.5">
            <div className="size-2.5 rounded-full bg-destructive/50" />
            <div className="size-2.5 rounded-full bg-warning/50" />
            <div className="size-2.5 rounded-full bg-success/50" />
          </div>
          <div className="mx-auto flex items-center gap-2 rounded-md bg-background/60 px-3 py-1 text-xs text-muted-foreground">
            <Icons.Lock className="size-2.5" /> {t('heroPreview.url')}
          </div>
          <Badge variant="brand" className="hidden sm:inline-flex">{t('heroPreview.live')}</Badge>
        </div>

        {/* Workspace body */}
        <div className="grid grid-cols-12 gap-0 min-h-[440px]">
          {/* Sidebar */}
          <div className="hidden md:flex md:col-span-2 flex-col border-r border-border/60 bg-background/40 p-2.5">
            <div className="flex items-center gap-2 rounded-md bg-primary/12 px-2 py-1.5">
              <div className="size-5 rounded bg-gradient-to-br from-primary/40 to-primary/10 ring-1 ring-primary/20" />
              <div className="min-w-0">
                <p className="text-2xs font-medium truncate">{t('heroPreview.projectName')}</p>
                <p className="text-2xs text-muted-foreground">{t('heroPreview.projectStats')}</p>
              </div>
            </div>
            <div className="mt-3 space-y-0.5">
              {heroSidebarNav.map((n, i) => (
                <div key={n} className={cn('flex items-center gap-2 rounded px-2 py-1 text-2xs', i === 0 ? 'bg-primary/12 text-primary' : 'text-muted-foreground')}>
                  <div className="size-3 rounded-sm bg-current opacity-60" />
                  <span className="truncate">{t(`heroPreview.nav.${n}`)}</span>
                </div>
              ))}
            </div>
          </div>

          {/* Main canvas */}
          <div className="col-span-12 md:col-span-10 p-4 space-y-3">
            <div className="flex items-center justify-between gap-2">
              <div>
                <p className="text-base font-semibold">{t('heroPreview.commandCenterTitle')}</p>
                <p className="text-2xs text-muted-foreground">{t('heroPreview.lastSync')}</p>
              </div>
              <div className="flex items-center gap-1.5">
                <div className="flex items-center gap-1.5 rounded-md border border-border/60 bg-background/40 px-2 py-1 text-2xs text-muted-foreground">
                  <Icons.Search className="size-2.5" /> {t('heroPreview.askPlaceholder')}
                </div>
              </div>
            </div>

            {/* KPI cards */}
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
              {heroKpis.map((k) => (
                <div key={k.key} className="rounded-lg border border-border/60 bg-background/40 p-2.5">
                  <p className="text-2xs text-muted-foreground">{t(`heroPreview.kpis.${k.key}`)}</p>
                  <p className="mt-0.5 text-md font-semibold tabular-nums">{k.v}</p>
                  <div className="mt-1 flex items-center justify-between">
                    <span className={cn('text-2xs', 'danger' in k && k.danger ? 'text-destructive' : 'text-success')}>{k.d}</span>
                    <div className="h-5 w-12">
                      <Sparkline data={[...k.s]} danger={'danger' in k ? k.danger : false} height={20} width={48} />
                    </div>
                  </div>
                </div>
              ))}
            </div>

            {/* Two-up: agents + insight */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
              <div className="lg:col-span-2 rounded-lg border border-border/60 bg-background/40 p-3">
                <div className="flex items-center justify-between mb-2.5">
                  <p className="text-xs font-semibold">{t('heroPreview.agentActivity')}</p>
                  <span className="text-2xs text-muted-foreground">{t('heroPreview.readyCount')}</span>
                </div>
                <div className="grid grid-cols-2 sm:grid-cols-3 gap-1.5">
                  {agentList.map((a, i) => {
                    const Icon = (Icons as any)[a.icon]
                    const states = ['completed', 'completed', 'completed', 'back-testing', 'waiting-approval', 'completed']
                    const st = states[i]
                    return (
                      <div key={a.key} className="rounded-md border border-border/60 bg-background/30 p-2">
                        <div className="flex items-center justify-between">
                          <Icon className={cn('size-3.5', a.color)} />
                          <span className={cn(
                            'size-1.5 rounded-full',
                            st === 'completed' ? 'bg-success'
                            : st === 'back-testing' ? 'bg-info pulse-ring'
                            : 'bg-warning'
                          )} />
                        </div>
                        <p className="mt-1 text-2xs font-medium truncate">{t(`agentsSection.list.${a.key}.name`)}</p>
                        <p className="text-2xs text-muted-foreground">{t(`heroPreview.states.${heroStateKey[st]}`)}</p>
                      </div>
                    )
                  })}
                </div>
              </div>
              <div className="rounded-lg border border-primary/20 bg-primary/[0.04] p-3">
                <div className="flex items-center gap-1.5 text-2xs uppercase tracking-wider text-primary font-semibold">
                  <Icons.Sparkles className="size-3" /> {t('heroPreview.aiInsight')}
                </div>
                <p className="mt-1.5 text-xs leading-snug text-foreground/90">
                  {t.rich('heroPreview.insightBody', { hl: (chunks) => <span className="font-semibold text-primary">{chunks}</span> })}
                </p>
                <div className="mt-2 inline-flex items-center gap-1 rounded-md bg-success/12 px-1.5 py-0.5 text-2xs text-success">
                  <Icons.CheckCircle2 className="size-2.5" /> {t('heroPreview.groundedBacktested')}
                </div>
              </div>
            </div>

            {/* Forecast preview row */}
            <div className="rounded-lg border border-border/60 bg-background/40 p-3">
              <div className="flex items-center justify-between mb-2">
                <p className="text-xs font-semibold">{t('heroPreview.forecastTitle')}</p>
                <Badge variant="success" className="text-2xs">{t('heroPreview.highConfidence')}</Badge>
              </div>
              <div className="flex items-end gap-[2px] h-16">
                {Array.from({ length: 36 }).map((_, i) => {
                  const actual = i < 27
                  const h = 30 + Math.sin(i / 2) * 12 + (i / 36) * 30 + (i % 5 === 0 ? 10 : 0)
                  return (
                    <div
                      key={i}
                      className={cn(
                        'flex-1 rounded-sm',
                        actual ? 'bg-primary/40' : 'bg-primary/15 border-t border-dashed border-primary/40'
                      )}
                      style={{ height: `${Math.max(8, h)}%` }}
                    />
                  )
                })}
              </div>
              <div className="mt-1.5 flex items-center justify-between text-2xs text-muted-foreground">
                <span>{t('heroPreview.historyLabel')}</span>
                <span>{t('heroPreview.forecastArrow')}</span>
                <span>{t('heroPreview.horizonLabel')}</span>
              </div>
            </div>
          </div>
        </div>

        <div className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity bg-background/40 backdrop-blur-sm">
          <Button size="lg"><Icons.ArrowRight className="size-4 rtl:-scale-x-100" /> {t('heroPreview.openWorkspace')}</Button>
        </div>
      </div>
    </div>
  )
}

const approvalStats = ['audience', 'cost', 'roi'] as const
const approvalStatValues = ['412', '$2.4K', '35x']

function ApprovalPreview() {
  const t = useTranslations('landing')
  const evidence = t.raw('approvalPreview.evidence') as string[]
  return (
    <div className="relative">
      <div className="absolute -inset-4 rounded-2xl bg-gradient-to-br from-primary/10 to-transparent blur-2xl pointer-events-none" />
      <div className="relative rounded-2xl border border-border/60 glass-strong shadow-floating p-5 space-y-3">
        <div className="flex items-center gap-2">
          <Badge variant="warning"><Icons.Clock className="size-3" /> {t('approvalPreview.waitingApproval')}</Badge>
          <span className="text-xs text-muted-foreground">{t('approvalPreview.agentName')}</span>
        </div>
        <div>
          <p className="text-md font-semibold">{t('approvalPreview.title')}</p>
          <p className="mt-1 text-sm text-muted-foreground leading-relaxed">
            {t.rich('approvalPreview.body', {
              hl1: (chunks) => <span className="text-foreground font-medium">{chunks}</span>,
              hl2: (chunks) => <span className="text-success font-medium">{chunks}</span>,
            })}
          </p>
        </div>
        <div className="grid grid-cols-3 gap-2">
          {approvalStats.map((s, i) => (
            <div key={s} className="rounded-lg border border-border/60 bg-background/40 p-2.5">
              <p className="text-2xs text-muted-foreground">{t(`approvalPreview.stats.${s}`)}</p>
              <p className="text-md font-semibold tabular-nums">{approvalStatValues[i]}</p>
            </div>
          ))}
        </div>
        <div className="rounded-lg border border-border/60 bg-background/40 p-2.5">
          <p className="text-2xs uppercase tracking-wider text-muted-foreground/70 font-medium mb-1.5">{t('approvalPreview.groundedEvidence')}</p>
          <div className="space-y-1 text-xs text-muted-foreground">
            {evidence.map((e) => (
              <div key={e} className="flex items-center gap-1.5"><Icons.CheckCircle2 className="size-3 text-success" /> {e}</div>
            ))}
          </div>
        </div>
        <div className="flex items-center gap-2 pt-1">
          <Button size="sm" className="flex-1"><Icons.Check className="size-3.5" /> {t('approvalPreview.approve')}</Button>
          <Button size="sm" variant="outline">{t('approvalPreview.edit')}</Button>
          <Button size="sm" variant="ghost">{t('approvalPreview.reject')}</Button>
        </div>
      </div>
    </div>
  )
}
