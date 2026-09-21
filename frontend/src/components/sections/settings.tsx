'use client'

import * as React from 'react'
import { useTheme } from 'next-themes'
import { useTranslations } from 'next-intl'
import { cn } from '@/lib/utils'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { SectionScroll, SectionHeader } from '@/components/shared/layout'
import { ProvidersManager } from '@/components/shared/providers-manager'
import * as Icons from 'lucide-react'

const navItems = [
  { id: 'providers', icon: 'Cpu' },
  { id: 'storage', icon: 'Database' },
  { id: 'appearance', icon: 'Palette' },
  { id: 'advanced', icon: 'Settings2' },
] as const

export function Settings() {
  const t = useTranslations('settings')
  const [active, setActive] = React.useState('providers')

  return (
    <SectionScroll>
      <SectionHeader
        title={t('title')}
        description={t('description')}
        icon={<Icons.Settings className="size-4.5" />}
        actions={<Button size="sm"><Icons.Check className="size-3.5" /> {t('saveChanges')}</Button>}
      />

      <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
        {/* Side nav */}
        <Card padding="sm" className="lg:col-span-1 h-fit sticky top-0">
          <nav className="space-y-0.5">
            {navItems.map((s) => {
              const Icon = (Icons as any)[s.icon]
              const isActive = active === s.id
              return (
                <button
                  key={s.id}
                  onClick={() => setActive(s.id)}
                  className={cn(
                    'flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium outline-none transition-colors text-start focus-visible:ring-[3px] focus-visible:ring-ring/50',
                    isActive ? 'bg-primary/12 text-primary' : 'text-muted-foreground hover:bg-accent/40 hover:text-foreground'
                  )}
                >
                  <Icon className="size-4" />
                  {t(`nav.${s.id}`)}
                </button>
              )
            })}
          </nav>
        </Card>

        <div className="lg:col-span-3 space-y-4">
          {active === 'providers' && <ProvidersSection />}
          {active === 'storage' && <StorageSection />}
          {active === 'appearance' && <AppearanceSection />}
          {active === 'advanced' && <AdvancedSection />}
        </div>
      </div>
    </SectionScroll>
  )
}

function ProvidersSection() {
  return <ProvidersManager />
}

const storageItems = [
  { key: 'database', status: 'success' },
  { key: 'projectHealth', status: 'success' },
  { key: 'cache', status: 'success' },
  { key: 'sheets', status: 'success' },
  { key: 'externalDb', status: 'success' },
] as const

const storageProjects = [
  { key: 'atlas', pct: 30 },
  { key: 'northwind', pct: 48 },
  { key: 'meridian', pct: 10 },
  { key: 'orion', pct: 12 },
] as const

function StorageSection() {
  const t = useTranslations('settings')
  return (
    <>
      <Card padding="default">
        <h2 className="text-md font-semibold mb-1">{t('storage.title')}</h2>
        <p className="text-sm text-muted-foreground mb-4">{t('storage.description')}</p>
        <div className="space-y-2.5">
          {storageItems.map((s) => (
            <div key={s.key} className="flex items-center gap-3 rounded-lg border border-border/60 bg-background/40 p-3">
              <div className={cn(
                'flex size-7 shrink-0 items-center justify-center rounded-md',
                s.status === 'success' ? 'bg-success/12 text-success' : 'bg-warning/12 text-warning'
              )}>
                <Icons.Check className="size-3.5" />
              </div>
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium">{t(`storage.items.${s.key}.label`)}</p>
                <p className="text-xs text-muted-foreground">{t(`storage.items.${s.key}.detail`)}</p>
              </div>
              <Badge variant="success" className="text-2xs">{t(`storage.items.${s.key}.value`)}</Badge>
            </div>
          ))}
        </div>
      </Card>

      <Card padding="default">
        <h3 className="text-base font-semibold mb-3">{t('storage.usageTitle')}</h3>
        <div className="space-y-3">
          {storageProjects.map((s) => (
            <div key={s.key}>
              <div className="flex items-center justify-between text-sm mb-1">
                <span className="font-medium">{t(`storage.projects.${s.key}.label`)}</span>
                <span className="text-muted-foreground tabular-nums">{t(`storage.projects.${s.key}.size`)}</span>
              </div>
              <div className="h-1.5 rounded-full bg-muted/40 overflow-hidden">
                <div className="h-full bg-primary/50 rounded-full" style={{ width: `${s.pct}%` }} />
              </div>
            </div>
          ))}
        </div>
      </Card>
    </>
  )
}

function AppearanceSection() {
  const t = useTranslations('settings')
  const { resolvedTheme, setTheme } = useTheme()
  const [mounted, setMounted] = React.useState(false)
  React.useEffect(() => {
    queueMicrotask(() => setMounted(true))
  }, [])
  const isDark = mounted ? resolvedTheme === 'dark' : true

  return (
    <Card padding="default">
      <h2 className="text-md font-semibold mb-1">{t('appearance.title')}</h2>
      <p className="text-sm text-muted-foreground mb-4">{t('appearance.description')}</p>
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm font-medium">{t('appearance.theme.label')}</p>
            <p className="text-xs text-muted-foreground">{t('appearance.theme.description')}</p>
          </div>
          <div className="grid grid-cols-2 gap-1.5">
            <button
              onClick={() => setTheme('dark')}
              className={cn(
                'rounded-lg border p-2 text-start outline-none transition-colors focus-visible:ring-[3px] focus-visible:ring-ring/50',
                isDark ? 'border-primary bg-primary/[0.05]' : 'border-border hover:bg-accent/40'
              )}
            >
              <div className="h-8 w-full rounded bg-[oklch(0.145_0.006_240)] border border-white/10 mb-1.5" />
              <p className={cn('text-xs font-medium', isDark ? 'text-primary' : 'text-foreground')}>{t('appearance.theme.dark')}</p>
            </button>
            <button
              onClick={() => setTheme('light')}
              className={cn(
                'rounded-lg border p-2 text-start outline-none transition-colors focus-visible:ring-[3px] focus-visible:ring-ring/50',
                !isDark ? 'border-primary bg-primary/[0.05]' : 'border-border hover:bg-accent/40'
              )}
            >
              <div className="h-8 w-full rounded bg-white border border-gray-200 mb-1.5" />
              <p className={cn('text-xs font-medium', !isDark ? 'text-primary' : 'text-foreground')}>{t('appearance.theme.light')}</p>
            </button>
          </div>
        </div>
        <div className="flex items-center justify-between pt-3 border-t border-border/60">
          <div>
            <p className="text-sm font-medium">{t('appearance.density.label')}</p>
            <p className="text-xs text-muted-foreground">{t('appearance.density.description')}</p>
          </div>
          <Select defaultValue="comfortable">
            <SelectTrigger className="h-8 w-[140px] text-sm"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="compact">{t('appearance.density.options.compact')}</SelectItem>
              <SelectItem value="comfortable">{t('appearance.density.options.comfortable')}</SelectItem>
              <SelectItem value="spacious">{t('appearance.density.options.spacious')}</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="flex items-center justify-between pt-3 border-t border-border/60">
          <div>
            <p className="text-sm font-medium">{t('appearance.reduceMotion.label')}</p>
            <p className="text-xs text-muted-foreground">{t('appearance.reduceMotion.description')}</p>
          </div>
          <Switch />
        </div>
        <div className="flex items-center justify-between pt-3 border-t border-border/60">
          <div>
            <p className="text-sm font-medium">{t('appearance.activityBar.label')}</p>
            <p className="text-xs text-muted-foreground">{t('appearance.activityBar.description')}</p>
          </div>
          <Switch defaultChecked />
        </div>
      </div>
    </Card>
  )
}

const syncItems = [
  { key: 'sheets', defaultValue: '15-minutes' },
  { key: 'database', defaultValue: '5-minutes' },
  { key: 'files', defaultValue: 'manual-only' },
] as const

const syncOptions = ['manual-only', '5-minutes', '15-minutes', '1-hour', 'daily'] as const

function AdvancedSection() {
  const t = useTranslations('settings')
  return (
    <>
      <Card padding="default">
        <h2 className="text-md font-semibold mb-1">{t('advanced.agentConfig.title')}</h2>
        <p className="text-sm text-muted-foreground mb-4">{t('advanced.agentConfig.description')}</p>
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-medium">{t('advanced.agentConfig.autoApprove.label')}</p>
              <p className="text-xs text-muted-foreground">{t('advanced.agentConfig.autoApprove.description')}</p>
            </div>
            <Switch defaultChecked />
          </div>
          <div className="flex items-center justify-between pt-3 border-t border-border/60">
            <div>
              <p className="text-sm font-medium">{t('advanced.agentConfig.autoRunCleaning.label')}</p>
              <p className="text-xs text-muted-foreground">{t('advanced.agentConfig.autoRunCleaning.description')}</p>
            </div>
            <Switch defaultChecked />
          </div>
          <div className="flex items-center justify-between pt-3 border-t border-border/60">
            <div>
              <p className="text-sm font-medium">{t('advanced.agentConfig.pauseOnRegression.label')}</p>
              <p className="text-xs text-muted-foreground">{t('advanced.agentConfig.pauseOnRegression.description')}</p>
            </div>
            <Switch defaultChecked />
          </div>
        </div>
      </Card>

      <Card padding="default">
        <h3 className="text-base font-semibold mb-1">{t('advanced.forecastDefaults.title')}</h3>
        <p className="text-sm text-muted-foreground mb-3">{t('advanced.forecastDefaults.description')}</p>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <p className="text-xs text-muted-foreground mb-1.5">{t('advanced.forecastDefaults.granularity.label')}</p>
            <Select defaultValue="daily">
              <SelectTrigger className="h-9 text-sm"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="daily">{t('advanced.forecastDefaults.granularity.options.daily')}</SelectItem>
                <SelectItem value="weekly">{t('advanced.forecastDefaults.granularity.options.weekly')}</SelectItem>
                <SelectItem value="monthly">{t('advanced.forecastDefaults.granularity.options.monthly')}</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div>
            <p className="text-xs text-muted-foreground mb-1.5">{t('advanced.forecastDefaults.horizon.label')}</p>
            <Select defaultValue="15">
              <SelectTrigger className="h-9 text-sm"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="7">{t('advanced.forecastDefaults.horizon.options.7')}</SelectItem>
                <SelectItem value="15">{t('advanced.forecastDefaults.horizon.options.15')}</SelectItem>
                <SelectItem value="30">{t('advanced.forecastDefaults.horizon.options.30')}</SelectItem>
                <SelectItem value="90">{t('advanced.forecastDefaults.horizon.options.90')}</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div>
            <p className="text-xs text-muted-foreground mb-1.5">{t('advanced.forecastDefaults.model.label')}</p>
            <Select defaultValue="auto">
              <SelectTrigger className="h-9 text-sm"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="auto">{t('advanced.forecastDefaults.model.options.auto')}</SelectItem>
                <SelectItem value="prophet">{t('advanced.forecastDefaults.model.options.prophet')}</SelectItem>
                <SelectItem value="ets">{t('advanced.forecastDefaults.model.options.ets')}</SelectItem>
                <SelectItem value="hw">{t('advanced.forecastDefaults.model.options.hw')}</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div>
            <p className="text-xs text-muted-foreground mb-1.5">{t('advanced.forecastDefaults.backtestFolds.label')}</p>
            <Select defaultValue="6">
              <SelectTrigger className="h-9 text-sm"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="3">{t('advanced.forecastDefaults.backtestFolds.options.3')}</SelectItem>
                <SelectItem value="6">{t('advanced.forecastDefaults.backtestFolds.options.6')}</SelectItem>
                <SelectItem value="12">{t('advanced.forecastDefaults.backtestFolds.options.12')}</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
      </Card>

      <Card padding="default">
        <h3 className="text-base font-semibold mb-1">{t('advanced.dataSync.title')}</h3>
        <p className="text-sm text-muted-foreground mb-3">{t('advanced.dataSync.description')}</p>
        <div className="space-y-2.5">
          {syncItems.map((s) => (
            <div key={s.key} className="flex items-center justify-between py-2 border-b border-border/40 last:border-0">
              <span className="text-sm">{t(`advanced.dataSync.items.${s.key}`)}</span>
              <Select defaultValue={s.defaultValue}>
                <SelectTrigger className="h-8 w-[150px] text-sm"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {syncOptions.map((o) => (
                    <SelectItem key={o} value={o}>{t(`advanced.dataSync.options.${o}`)}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          ))}
        </div>
      </Card>
    </>
  )
}
