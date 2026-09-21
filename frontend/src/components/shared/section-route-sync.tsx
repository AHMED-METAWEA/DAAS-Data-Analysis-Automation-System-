'use client'

import * as React from 'react'
import { usePathname, useRouter } from 'next/navigation'
import { useAppStore, type Section } from '@/lib/store'

const VALID_SECTIONS: Section[] = [
  'command-center',
  'copilot',
  'projects',
  'data',
  'visualization',
  'insights',
  'root-cause',
  'monitoring',
  'forecasting',
  'marketing',
  'churn',
  'crm',
  'reports',
  'account',
  'settings',
]

/** Two-way bridge between the URL and the zustand `section` field, so every
 * existing component that calls `setSection(...)` (sidebar nav, command
 * palette, the create-project wizard's "finish" step, etc.) gets a real,
 * deep-linkable route change for free, without having to touch each call
 * site individually. */
export function SectionRouteSync() {
  const pathname = usePathname()
  const router = useRouter()

  // pathname -> store (covers back/forward nav and direct links)
  React.useEffect(() => {
    const segment = pathname.split('/').filter(Boolean)[0]
    const section = (VALID_SECTIONS as string[]).includes(segment ?? '')
      ? (segment as Section)
      : 'command-center'
    if (useAppStore.getState().section !== section) {
      useAppStore.setState({ section })
    }
  }, [pathname])

  // store -> router (covers setSection() being called from anywhere)
  React.useEffect(() => {
    return useAppStore.subscribe((state, prevState) => {
      if (state.section === prevState.section) return
      const target = `/${state.section}`
      if (typeof window !== 'undefined' && window.location.pathname !== target) {
        router.push(target)
      }
    })
  }, [router])

  return null
}
