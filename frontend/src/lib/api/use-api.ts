'use client'

import { useSession } from 'next-auth/react'
import { useCallback } from 'react'
import { apiFetch, type ApiFetchOptions } from '@/lib/api/client'

/** Client-side hook: returns a fetch function bound to the signed-in user's
 * FastAPI access token, for use inside react-query hooks. */
export function useApi() {
  const { data: session } = useSession()
  const accessToken = (session as any)?.accessToken as string | undefined

  const call = useCallback(
    <T = unknown>(path: string, options: ApiFetchOptions = {}) =>
      apiFetch<T>(path, { ...options, accessToken }),
    [accessToken]
  )

  return { call, accessToken, ready: Boolean(accessToken) }
}
