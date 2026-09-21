'use client'

import * as React from 'react'
import { useAppStore } from '@/lib/store'
import { AppShell } from '@/components/shared/app-shell'
import { SectionRouteSync } from '@/components/shared/section-route-sync'

export function AppShellClient({ children }: { children: React.ReactNode }) {
  // Command-K / Cmd+K to open the global command palette
  React.useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault()
        useAppStore.getState().setCommandOpen(true)
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

  return (
    <>
      <SectionRouteSync />
      <AppShell>{children}</AppShell>
    </>
  )
}
