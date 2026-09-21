/**
 * Typed fetch wrapper for the FastAPI backend. Every domain hook in
 * src/lib/queries/* goes through this so auth headers, base URL, and error
 * shapes are handled in exactly one place.
 */

// `127.0.0.1` rather than `localhost`, deliberately: uvicorn binds 127.0.0.1 by
// default (IPv4 only — verified: `--host ::` on Windows binds IPv6 *only* and then
// refuses 127.0.0.1, so no single bind covers both), while `localhost` resolves to
// `::1` first on a dual-stack machine. Current Node and Chrome retry on the other
// family and hide it, but where that retry doesn't happen the first connection is
// refused and the UI reports "Cannot reach the API server". The literal removes DNS
// ordering from the path entirely. Override with NEXT_PUBLIC_API_URL when the API is
// not on this machine.
const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://127.0.0.1:8000'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
    this.name = 'ApiError'
  }
}

/** The request never reached the API (server down, wrong `NEXT_PUBLIC_API_URL`,
 * or an origin the backend's CORS policy rejects). `fetch` reports all of these
 * as a bare `TypeError: Failed to fetch`, which tells a user nothing — so it is
 * translated into something they can act on. `status` is 0: there was no
 * response. */
export class ApiUnreachableError extends ApiError {
  constructor(url: string, cause?: unknown) {
    super(
      0,
      `Cannot reach the API server at ${url}. Check that the backend is running ` +
        `(uvicorn backend.app.main:app --port 8000) and that NEXT_PUBLIC_API_URL points to it.`
    )
    this.name = 'ApiUnreachableError'
    if (cause !== undefined) this.cause = cause
  }
}

export type ApiFetchOptions = Omit<RequestInit, 'body'> & {
  accessToken?: string | null
  json?: unknown
  body?: BodyInit | null
}

export async function apiFetch<T = unknown>(path: string, options: ApiFetchOptions = {}): Promise<T> {
  const { accessToken, json, headers, ...rest } = options

  const finalHeaders = new Headers(headers)
  if (accessToken) finalHeaders.set('Authorization', `Bearer ${accessToken}`)

  let body = rest.body
  if (json !== undefined) {
    finalHeaders.set('Content-Type', 'application/json')
    body = JSON.stringify(json)
  }

  let res: Response
  try {
    res = await fetch(`${API_URL}${path}`, { ...rest, headers: finalHeaders, body })
  } catch (err) {
    // A rejected fetch means no HTTP response at all — never a 4xx/5xx, which
    // resolve normally and are handled below.
    throw new ApiUnreachableError(API_URL, err)
  }

  if (!res.ok) {
    let detail = res.statusText
    try {
      const payload = await res.json()
      detail = payload?.detail ? String(payload.detail) : detail
    } catch {
      // response wasn't JSON — keep statusText
    }
    throw new ApiError(res.status, detail)
  }

  if (res.status === 204) return undefined as T
  const contentType = res.headers.get('content-type') ?? ''
  if (contentType.includes('application/json')) return res.json()
  return (await res.text()) as unknown as T
}
