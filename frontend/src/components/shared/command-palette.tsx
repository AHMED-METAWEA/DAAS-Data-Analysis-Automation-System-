'use client'

import * as React from 'react'
import { useRouter } from 'next/navigation'
import { useTranslations } from 'next-intl'
import { CommandDialog, CommandInput, CommandList, CommandEmpty, CommandGroup, CommandItem, CommandSeparator } from '@/components/ui/command'
import { useAppStore, type Section } from '@/lib/store'
import * as Icons from 'lucide-react'

const navIcons: { id: Section; icon: string }[] = [
  { id: 'command-center', icon: 'LayoutDashboard' },
  { id: 'copilot', icon: 'Sparkles' },
  { id: 'projects', icon: 'FolderKanban' },
  { id: 'data', icon: 'Database' },
  { id: 'visualization', icon: 'BarChart3' },
  { id: 'insights', icon: 'Lightbulb' },
  { id: 'forecasting', icon: 'TrendingUp' },
  { id: 'marketing', icon: 'Megaphone' },
  { id: 'churn', icon: 'ShieldAlert' },
  { id: 'crm', icon: 'Contact' },
  { id: 'reports', icon: 'FileText' },
  { id: 'account', icon: 'UserCircle' },
  { id: 'settings', icon: 'Settings' },
]

const quickActionIcons: { key: string; icon: string; section: Section }[] = [
  { key: 'createProject', icon: 'Plus', section: 'projects' },
  { key: 'connectData', icon: 'Database', section: 'data' },
  { key: 'generateChart', icon: 'Sparkles', section: 'visualization' },
  { key: 'runForecast', icon: 'TrendingUp', section: 'forecasting' },
  { key: 'reviewChurn', icon: 'ShieldAlert', section: 'churn' },
  { key: 'buildCampaign', icon: 'Megaphone', section: 'marketing' },
  { key: 'generateReport', icon: 'FileText', section: 'reports' },
]

export function GlobalCommandPalette() {
  const t = useTranslations('nav')
  const { commandOpen, setCommandOpen, setSection, setPendingAskQuestion } = useAppStore()
  const [query, setQuery] = React.useState('')
  const commandSuggestions = t.raw('palette.suggestions') as string[]
  const navItems = navIcons.map((n) => ({
    ...n,
    label: n.id === 'account' || n.id === 'settings' ? t(`sidebar.${n.id}`) : t(`sidebar.items.${n.id}`),
    hint: t(`palette.hints.${n.id}`),
  }))
  const quickActions = quickActionIcons.map((a) => ({ ...a, label: t(`palette.quickActions.${a.key}`), hint: t(`palette.quickActions.${a.key}Hint`) }))

  const goTo = (s: Section) => {
    setSection(s)
    setCommandOpen(false)
    setQuery('')
  }

  const ask = (question: string) => {
    setPendingAskQuestion(question)
    setSection('copilot')
    setCommandOpen(false)
    setQuery('')
  }

  return (
    <CommandDialog
      open={commandOpen}
      onOpenChange={(o) => { setCommandOpen(o); if (!o) setQuery('') }}
    >
      <CommandInput
        placeholder={t('palette.placeholder')}
        value={query}
        onValueChange={setQuery}
      />
      <CommandList>
        <CommandEmpty>{t('palette.noResultsFor', { query })}</CommandEmpty>

        {query && (
          <CommandGroup heading={t('palette.askHeading')}>
            <CommandItem
              onSelect={() => ask(query)}
              className="items-start py-3"
            >
              <Icons.Sparkles className="size-4 mt-0.5 text-primary" />
              <div className="min-w-0 flex-1">
                <p className="text-base font-medium leading-tight">{query}</p>
                <p className="mt-0.5 text-xs text-muted-foreground">{t('palette.askHint')}</p>
              </div>
            </CommandItem>
          </CommandGroup>
        )}

        <CommandGroup heading={t('palette.navigateHeading')}>
          {navItems.map((item) => {
            const Icon = (Icons as any)[item.icon] ?? Icons.Circle
            return (
              <CommandItem key={item.id} onSelect={() => goTo(item.id)}>
                <Icon className="size-4" />
                <span>{item.label}</span>
                <span className="ms-auto text-xs text-muted-foreground">{item.hint}</span>
              </CommandItem>
            )
          })}
        </CommandGroup>

        <CommandSeparator />

        <CommandGroup heading={t('palette.quickActionsHeading')}>
          {quickActions.map((a) => {
            const Icon = (Icons as any)[a.icon] ?? Icons.Circle
            return (
              <CommandItem key={a.label} onSelect={() => goTo(a.section)}>
                <Icon className="size-4" />
                <span>{a.label}</span>
                <span className="ms-auto text-xs text-muted-foreground">{a.hint}</span>
              </CommandItem>
            )
          })}
        </CommandGroup>

        <CommandSeparator />

        <CommandGroup heading={t('palette.suggestedHeading')}>
          {commandSuggestions.map((p) => (
            <CommandItem
              key={p}
              onSelect={() => ask(p)}
              className="text-sm text-muted-foreground"
            >
              <Icons.MessageSquare className="size-4" />
              <span className="italic">“{p}”</span>
            </CommandItem>
          ))}
        </CommandGroup>
      </CommandList>
    </CommandDialog>
  )
}
