'use client'

import * as React from 'react'
import { SessionProvider } from 'next-auth/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { SessionSync } from '@/components/shared/session-sync'
import { ProjectSync } from '@/components/shared/project-sync'
import { LocaleProvider } from '@/components/locale-provider'

export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = React.useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { staleTime: 30_000, retry: 1 },
        },
      })
  )

  return (
    <SessionProvider>
      <LocaleProvider>
        <QueryClientProvider client={queryClient}>
          <SessionSync />
          <ProjectSync />
          {children}
        </QueryClientProvider>
      </LocaleProvider>
    </SessionProvider>
  )
}
