'use client'

import * as React from 'react'
import { useTranslations } from 'next-intl'
import { cn } from '@/lib/utils'
import { useAppStore, type Section } from '@/lib/store'
import { useSignOut } from '@/components/shared/session-sync'
import { useProjects } from '@/lib/queries/projects'
import { useProvidersStatus } from '@/lib/queries/settings'
import { useAlertSummary } from '@/lib/queries/monitoring'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { GlobalCommandPalette } from '@/components/shared/command-palette'
import { ThemeToggle } from '@/components/shared/theme-toggle'
import { LanguageToggle } from '@/components/shared/language-toggle'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import * as Icons from 'lucide-react'

const navItems: { id: Section; label: string; icon: string; badge?: string }[] = [
  { id: 'command-center', label: 'Command Center', icon: 'LayoutDashboard' },
  { id: 'copilot', label: 'Copilot', icon: 'Sparkles', badge: 'New' },
  { id: 'projects', label: 'Projects', icon: 'FolderKanban' },
  { id: 'data', label: 'Data', icon: 'Database' },
  { id: 'visualization', label: 'Visualization', icon: 'BarChart3' },
  { id: 'insights', label: 'Insights', icon: 'Lightbulb' },
  { id: 'root-cause', label: 'Root Cause', icon: 'Crosshair', badge: 'New' },
  { id: 'monitoring', label: 'Autopilot', icon: 'RadioTower', badge: 'New' },
  { id: 'forecasting', label: 'Forecasting', icon: 'TrendingUp', badge: 'Live' },
  { id: 'marketing', label: 'Marketing', icon: 'Megaphone', badge: '2' },
  { id: 'churn', label: 'Churn', icon: 'ShieldAlert' },
  { id: 'crm', label: 'CRM', icon: 'Contact', badge: 'New' },
  { id: 'reports', label: 'Reports', icon: 'FileText' },
]

/** Unread alerts from scheduled runs.
 *
 * The bell used to be permanently disabled because nothing produced
 * notifications. Now that the Autopilot does, it is the one place in the app
 * where a finding can reach a user who never opened the Monitoring page — which
 * is the entire point of the feature. Polled on an interval, because the
 * scheduler runs server-side and the browser has no other way of hearing it. */
function AlertBell() {
  const t = useTranslations('nav')
  const setSection = useAppStore((s) => s.setSection)
  const summary = useAlertSummary()
  const unread = summary.data?.unread ?? 0

  return (
    <TooltipProvider delayDuration={200}>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            className="size-9 relative"
            onClick={() => setSection('monitoring')}
            aria-label={
              unread > 0 ? t('header.alertsUnread', { count: unread }) : t('header.alertsNone')
            }
          >
            <Icons.Bell className="size-4" />
            {unread > 0 && (
              <span className="absolute -end-0.5 -top-0.5 flex min-w-4 items-center justify-center rounded-full bg-destructive px-1 text-2xs font-semibold leading-4 text-destructive-foreground">
                {unread > 9 ? '9+' : unread}
              </span>
            )}
          </Button>
        </TooltipTrigger>
        <TooltipContent side="bottom">
          {unread > 0 ? t('header.alertsUnread', { count: unread }) : t('header.alertsNone')}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const t = useTranslations('nav')
  const { section, setSection, sidebarCollapsed, toggleSidebar, activeProjectId, setActiveProjectId, setCommandOpen, rightPanelOpen, toggleRightPanel, user } = useAppStore()
  const logout = useSignOut()
  const projectsQuery = useProjects()
  const providersQuery = useProvidersStatus()
  const projects = projectsQuery.data ?? []
  const activeProject = projects.find((p) => p.id === activeProjectId)
  const configuredProviders = providersQuery.data?.fallback_order.filter((p) => p.configured).length ?? 0

  // The (app) route layout already redirects unauthenticated requests
  // server-side; this just covers the brief moment before the session
  // finishes loading client-side.
  if (!user) return null

  const navLabel = (id: Section) => t(`sidebar.items.${id}`)
  const breadcrumbLabel = section === 'account'
    ? t('sidebar.account')
    : section === 'settings'
    ? t('sidebar.settings')
    : (navItems.some((n) => n.id === section) ? navLabel(section) : section)

  return (
    <div className="flex h-screen w-full overflow-hidden bg-background">
      {/* ===== Sidebar ===== */}
      <aside
        className={cn(
          'flex shrink-0 flex-col border-r border-sidebar-border bg-sidebar transition-[width] duration-200',
          sidebarCollapsed ? 'w-[64px]' : 'w-[232px]'
        )}
      >
        {/* Brand */}
        <div className="flex h-14 items-center gap-2.5 px-4 border-b border-sidebar-border">
          <div className="flex size-7 shrink-0 items-center justify-center rounded-lg bg-primary/15 ring-1 ring-inset ring-primary/25">
            <Icons.Activity className="size-4 text-primary" />
          </div>
          {!sidebarCollapsed && (
            <div className="min-w-0 flex-1">
              <p className="text-base font-semibold tracking-tight leading-none">{t('brand')}</p>
              <p className="mt-0.5 text-2xs text-muted-foreground leading-none">{t('brandSubtitle')}</p>
            </div>
          )}
        </div>

        {/* Active Project selector */}
        <div className="px-2.5 pt-3">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button
                aria-label={sidebarCollapsed ? `${t('sidebar.switchProject')} (${activeProject?.name ?? '—'})` : undefined}
                className={cn(
                  'flex w-full items-center gap-2 rounded-lg border border-sidebar-border bg-background/40 px-2.5 py-2 text-left transition-colors outline-none hover:bg-accent/40 focus-visible:ring-[3px] focus-visible:ring-ring/50',
                  sidebarCollapsed && 'justify-center px-0'
                )}
              >
                <div className="flex size-6 shrink-0 items-center justify-center rounded-md bg-gradient-to-br from-primary/30 to-primary/5 text-2xs font-bold text-primary ring-1 ring-inset ring-primary/20">
                  {(activeProject?.name ?? '?').charAt(0)}
                </div>
                {!sidebarCollapsed && (
                  <>
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium leading-tight truncate">{activeProject?.name ?? t('sidebar.noProjectSelected')}</p>
                      <p className="text-2xs text-muted-foreground leading-none mt-0.5 capitalize">{activeProject?.data_source_mode ?? '—'}</p>
                    </div>
                    <Icons.ChevronsUpDown className="size-3.5 text-muted-foreground shrink-0" />
                  </>
                )}
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" className="w-[240px]">
              <DropdownMenuLabel className="text-xs text-muted-foreground uppercase tracking-wider">{t('sidebar.switchProject')}</DropdownMenuLabel>
              <DropdownMenuSeparator />
              {projects.map((p) => (
                <DropdownMenuItem
                  key={p.id}
                  onClick={() => setActiveProjectId(p.id)}
                  className="flex items-start gap-2.5 py-2"
                >
                  <div className="flex size-6 shrink-0 items-center justify-center rounded-md bg-gradient-to-br from-primary/30 to-primary/5 text-2xs font-bold text-primary ring-1 ring-inset ring-primary/20 mt-0.5">
                    {p.name.charAt(0)}
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium leading-tight">{p.name}</p>
                    <p className="text-xs text-muted-foreground mt-0.5 capitalize">{p.data_source_mode} · {p.status}</p>
                  </div>
                  {p.id === activeProjectId && <Icons.Check className="size-3.5 text-primary mt-1" />}
                </DropdownMenuItem>
              ))}
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={() => setSection('projects')} className="text-primary">
                <Icons.Plus className="size-3.5" /> {t('sidebar.createNewProject')}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>

        {/* Primary nav */}
        <nav className="flex-1 px-2 pt-3 pb-2 overflow-y-auto no-scrollbar">
          <p className={cn('px-2.5 pb-1.5 text-2xs uppercase tracking-wider text-muted-foreground/60 font-semibold', sidebarCollapsed && 'sr-only')}>{t('sidebar.workspaceLabel')}</p>
          <ul className="space-y-0.5">
            {navItems.map((item) => {
              const Icon = (Icons as any)[item.icon] ?? Icons.Circle
              const active = section === item.id
              const label = navLabel(item.id)
              return (
                <li key={item.id}>
                  <TooltipProvider delayDuration={200}>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <button
                          onClick={() => setSection(item.id)}
                          aria-label={sidebarCollapsed ? `${label}${item.badge ? ` · ${item.badge}` : ''}` : undefined}
                          className={cn(
                            'group relative flex w-full items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-sm font-medium outline-none transition-colors focus-visible:ring-[3px] focus-visible:ring-ring/50',
                            sidebarCollapsed && 'justify-center px-0',
                            active
                              ? 'bg-primary/12 text-primary'
                              : 'text-muted-foreground hover:bg-accent/40 hover:text-foreground'
                          )}
                        >
                          {active && <span className="absolute start-0 top-1/2 -translate-y-1/2 h-4 w-0.5 rounded-e-full bg-primary" />}
                          <Icon className={cn('size-4 shrink-0', active && 'text-primary')} />
                          {!sidebarCollapsed && <span className="flex-1 text-start truncate">{label}</span>}
                          {!sidebarCollapsed && item.badge && (
                            <span className={cn(
                              'inline-flex items-center rounded px-1 py-0 text-2xs font-semibold leading-[1.4]',
                              item.badge === 'Live'
                                ? 'bg-info/15 text-info'
                                : 'bg-warning/15 text-warning'
                            )}>
                              {item.badge}
                            </span>
                          )}
                        </button>
                      </TooltipTrigger>
                      {sidebarCollapsed && (
                        <TooltipContent side="right" sideOffset={6}>
                          {label}{item.badge ? ` · ${item.badge}` : ''}
                        </TooltipContent>
                      )}
                    </Tooltip>
                  </TooltipProvider>
                </li>
              )
            })}
          </ul>
        </nav>

        {/* Bottom utilities */}
        <div className="border-t border-sidebar-border p-2 space-y-0.5">
          <button
            onClick={() => setSection('account')}
            aria-label={sidebarCollapsed ? t('sidebar.account') : undefined}
            className={cn(
              'flex w-full items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-sm font-medium outline-none transition-colors focus-visible:ring-[3px] focus-visible:ring-ring/50',
              sidebarCollapsed && 'justify-center px-0',
              section === 'account'
                ? 'bg-primary/12 text-primary'
                : 'text-muted-foreground hover:bg-accent/40 hover:text-foreground'
            )}
          >
            <Icons.UserCircle className="size-4" />
            {!sidebarCollapsed && <span>{t('sidebar.account')}</span>}
          </button>

          <button
            onClick={() => setSection('settings')}
            aria-label={sidebarCollapsed ? t('sidebar.settings') : undefined}
            className={cn(
              'flex w-full items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-sm font-medium outline-none transition-colors focus-visible:ring-[3px] focus-visible:ring-ring/50',
              sidebarCollapsed && 'justify-center px-0',
              section === 'settings'
                ? 'bg-primary/12 text-primary'
                : 'text-muted-foreground hover:bg-accent/40 hover:text-foreground'
            )}
          >
            <Icons.Settings className="size-4" />
            {!sidebarCollapsed && <span>{t('sidebar.settings')}</span>}
          </button>

          <ThemeToggle collapsed={sidebarCollapsed} />
          <LanguageToggle collapsed={sidebarCollapsed} />

          {/* Provider status */}
          <button
            onClick={() => setSection('settings')}
            aria-label={sidebarCollapsed ? t('sidebar.providersConfigured', { count: configuredProviders }) : undefined}
            className={cn('flex w-full items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-xs text-muted-foreground outline-none transition-colors hover:bg-accent/40 hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50', sidebarCollapsed && 'justify-center px-0')}
          >
            <span className="relative flex size-2 shrink-0">
              {configuredProviders > 0 && (
                <span className="absolute inline-flex h-full w-full rounded-full bg-success opacity-75 animate-ping" />
              )}
              <span className={cn('relative inline-flex size-2 rounded-full', configuredProviders > 0 ? 'bg-success' : 'bg-muted-foreground/40')} />
            </span>
            {!sidebarCollapsed && <span>{t('sidebar.providersConfigured', { count: configuredProviders })}</span>}
          </button>

          <button
            onClick={toggleSidebar}
            aria-label={sidebarCollapsed ? t('sidebar.expandSidebar') : t('sidebar.collapseSidebar')}
            className={cn(
              'flex w-full items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-sm font-medium text-muted-foreground outline-none transition-colors hover:bg-accent/40 hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50',
              sidebarCollapsed && 'justify-center px-0'
            )}
          >
            {sidebarCollapsed ? <Icons.ChevronsRight className="size-4 rtl:-scale-x-100" /> : <Icons.ChevronsLeft className="size-4 rtl:-scale-x-100" />}
            {!sidebarCollapsed && <span>{t('sidebar.collapse')}</span>}
          </button>
        </div>
      </aside>

      {/* ===== Main column ===== */}
      <div className="flex min-w-0 flex-1 flex-col">
        {/* Top bar */}
        <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border bg-background/80 backdrop-blur px-4">
          {/* Breadcrumb */}
          <div className="hidden md:flex items-center gap-1.5 text-sm">
            <span className="text-muted-foreground">{activeProject?.name ?? t('header.noProject')}</span>
            <Icons.ChevronRight className="size-3.5 text-muted-foreground/50 rtl:-scale-x-100" />
            <span className="font-medium capitalize">{breadcrumbLabel}</span>
          </div>

          {/* Global command input */}
          <button
            onClick={() => setCommandOpen(true)}
            className="ms-auto flex h-9 w-full max-w-md items-center gap-2.5 rounded-lg border border-border bg-muted/30 px-3 text-sm text-muted-foreground outline-none transition-colors hover:bg-muted/50 hover:border-border/80 focus-visible:ring-[3px] focus-visible:ring-ring/50"
          >
            <Icons.Search className="size-3.5" />
            <span className="flex-1 text-start truncate">{t('header.askPlaceholder')}</span>
            <kbd className="hidden sm:inline-flex items-center gap-0.5 rounded border border-border bg-background px-1.5 py-0.5 text-2xs font-mono text-muted-foreground">⌘K</kbd>
          </button>

          <div className="flex items-center gap-1.5">
            <TooltipProvider delayDuration={200}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button variant="ghost" size="icon" onClick={toggleRightPanel} className="size-9" aria-label={t('header.toggleContextPanel')}>
                    <Icons.PanelRight className="size-4 rtl:-scale-x-100" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent side="bottom">{t('header.toggleContextPanel')}</TooltipContent>
              </Tooltip>
            </TooltipProvider>

            <AlertBell />

            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button className="ml-1 flex items-center gap-2 rounded-lg pl-1 pr-2 py-1 outline-none transition-colors hover:bg-accent/40 cursor-pointer focus-visible:ring-[3px] focus-visible:ring-ring/50">
                  <div className="flex size-7 items-center justify-center rounded-full bg-gradient-to-br from-primary/40 to-info/30 text-xs font-semibold text-primary-foreground ring-1 ring-inset ring-primary/30">
                    {user.initials}
                  </div>
                  <div className="hidden lg:block min-w-0 text-left">
                    <p className="text-sm font-medium leading-none truncate max-w-[120px]">{user.name}</p>
                    <p className="text-2xs text-muted-foreground mt-0.5 leading-none">{user.role}</p>
                  </div>
                  <Icons.ChevronDown className="size-3 text-muted-foreground hidden lg:block" />
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-[240px]">
                <div className="px-2 py-2.5 border-b border-border">
                  <p className="text-base font-semibold leading-tight">{user.name}</p>
                  <p className="text-xs text-muted-foreground mt-0.5 truncate">{user.email}</p>
                  <div className="mt-1.5 flex items-center gap-1.5">
                    <Badge variant="brand" className="text-2xs">{user.plan}</Badge>
                    <Badge variant="outline" className="text-2xs">{user.role}</Badge>
                  </div>
                </div>
                <DropdownMenuItem onClick={() => setSection('account')}>
                  <Icons.UserCircle className="size-3.5" /> {t('sidebar.account')}
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => setSection('settings')}>
                  <Icons.Settings className="size-3.5" /> {t('sidebar.settings')}
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => setSection('command-center')}>
                  <Icons.LayoutDashboard className="size-3.5" /> {t('header.commandCenter')}
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem className="text-muted-foreground" disabled>
                  <Icons.Users className="size-3.5" /> {t('header.inviteTeammates')} <Badge variant="warning" className="ms-auto text-2xs">{t('header.soon')}</Badge>
                </DropdownMenuItem>
                <DropdownMenuItem className="text-muted-foreground" disabled>
                  <Icons.LifeBuoy className="size-3.5" /> {t('header.helpDocs')}
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem onClick={logout} className="text-destructive focus:text-destructive">
                  <Icons.LogOut className="size-3.5" /> {t('header.signOut')}
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </header>

        {/* Active content */}
        <main className="flex-1 min-h-0 overflow-hidden">
          {children}
        </main>
      </div>

      <GlobalCommandPalette />
    </div>
  )
}
