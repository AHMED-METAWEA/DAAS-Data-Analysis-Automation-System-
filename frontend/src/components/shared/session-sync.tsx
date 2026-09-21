'use client'

import * as React from 'react'
import { useSession, signOut } from 'next-auth/react'
import { useAppStore } from '@/lib/store'

/** Keeps the zustand store's `user` mirror in sync with the real NextAuth
 * session, so existing components that read `useAppStore().user` (account
 * page, app shell, etc.) keep working unchanged while being backed by a real
 * session instead of the old fake login. */
export function SessionSync() {
  const { data: session, status } = useSession()
  const setUser = useAppStore((s) => s.setUser)

  React.useEffect(() => {
    if (status === 'loading') return
    if (!session?.user) {
      setUser(null)
      return
    }
    const name = session.user.name ?? session.user.email ?? 'Account'
    setUser({
      name,
      email: session.user.email ?? '',
      role: (session.user as any).role ?? 'Owner',
      organization: (session.user as any).organization ?? '',
      initials: name
        .split(' ')
        .map((n) => n[0])
        .join('')
        .slice(0, 2)
        .toUpperCase(),
      plan: 'Business',
    })
  }, [session, status, setUser])

  return null
}

export function useSignOut() {
  return () => signOut({ callbackUrl: '/' })
}
