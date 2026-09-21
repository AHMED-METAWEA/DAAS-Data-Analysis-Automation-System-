import type { NextAuthOptions } from 'next-auth'
import CredentialsProvider from 'next-auth/providers/credentials'
import GoogleProvider from 'next-auth/providers/google'
import GitHubProvider from 'next-auth/providers/github'
import { authSecret } from '@/lib/auth-secret'

// 127.0.0.1, not localhost — see the note in lib/api/client.ts (IPv4/IPv6 loopback).
const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://127.0.0.1:8000'

type BackendTokenPair = {
  access_token: string
  refresh_token: string
}

type BackendUser = {
  id: string
  email: string
  name: string
  role: string
  organization: string | null
  timezone: string | null
  bio: string | null
}

async function fetchMe(accessToken: string): Promise<BackendUser | null> {
  const res = await fetch(`${API_URL}/api/v1/auth/me`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  })
  if (!res.ok) return null
  return res.json()
}

// Distinct codes so the login form can show the true cause of a failure
// instead of a single guessed message. NextAuth passes an Error's `message`
// straight through as `signIn()`'s `res.error` (only a `null` return from
// `authorize` collapses to the generic "CredentialsSignin").
type AuthErrorCode =
  | 'EmailAlreadyRegistered'
  | 'WeakPassword'
  | 'InvalidInput'
  | 'InvalidCredentials'
  | 'AccountDisabled'
  | 'ServerUnreachable'
  | 'AuthFailed'

async function parseRegisterError(res: Response): Promise<AuthErrorCode> {
  if (res.status === 409) return 'EmailAlreadyRegistered'
  if (res.status === 422) {
    const body = await res.json().catch(() => null)
    const issues: Array<{ loc?: string[] }> = body?.detail ?? []
    if (issues.some((issue) => issue.loc?.includes('password'))) return 'WeakPassword'
    return 'InvalidInput'
  }
  return 'AuthFailed'
}

function parseLoginError(res: Response): AuthErrorCode {
  if (res.status === 401) return 'InvalidCredentials'
  if (res.status === 403) return 'AccountDisabled'
  return 'AuthFailed'
}

const providers: NextAuthOptions['providers'] = [
  CredentialsProvider({
    name: 'Credentials',
    credentials: {
      email: { label: 'Email', type: 'email' },
      password: { label: 'Password', type: 'password' },
      mode: { label: 'Mode', type: 'text' }, // "signin" | "signup"
      name: { label: 'Name', type: 'text' },
    },
    async authorize(credentials) {
      if (!credentials?.email || !credentials.password) throw new Error('InvalidInput' satisfies AuthErrorCode)
      const isSignup = credentials.mode === 'signup'
      const endpoint = isSignup ? 'register' : 'login'
      const email = credentials.email.trim().toLowerCase()
      const body = isSignup
        ? { email, password: credentials.password, name: (credentials.name || credentials.email).trim() }
        : { email, password: credentials.password }

      let res: Response
      try {
        res = await fetch(`${API_URL}/api/v1/auth/${endpoint}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        })
      } catch {
        // Backend unreachable (not started, Postgres down, network issue) —
        // must not be confused with "email already registered".
        throw new Error('ServerUnreachable' satisfies AuthErrorCode)
      }
      if (!res.ok) {
        const code = isSignup ? await parseRegisterError(res) : parseLoginError(res)
        throw new Error(code satisfies AuthErrorCode)
      }
      const tokens: BackendTokenPair = await res.json()
      const me = await fetchMe(tokens.access_token)
      if (!me) throw new Error('ServerUnreachable' satisfies AuthErrorCode)
      return {
        id: me.id,
        email: me.email,
        name: me.name,
        accessToken: tokens.access_token,
        refreshToken: tokens.refresh_token,
        role: me.role,
        organization: me.organization,
        timezone: me.timezone,
        bio: me.bio,
      } as any
    },
  }),
]

// Google/GitHub SSO only appear once their app credentials are configured —
// per the "don't fabricate a working flow" rule, rather than a button that
// silently does nothing.
if (process.env.GOOGLE_CLIENT_ID && process.env.GOOGLE_CLIENT_SECRET) {
  providers.push(
    GoogleProvider({
      clientId: process.env.GOOGLE_CLIENT_ID,
      clientSecret: process.env.GOOGLE_CLIENT_SECRET,
    })
  )
}
if (process.env.GITHUB_CLIENT_ID && process.env.GITHUB_CLIENT_SECRET) {
  providers.push(
    GitHubProvider({
      clientId: process.env.GITHUB_CLIENT_ID,
      clientSecret: process.env.GITHUB_CLIENT_SECRET,
    })
  )
}

export const authOptions: NextAuthOptions = {
  providers,
  // Set explicitly rather than left to NextAuth's own `process.env` lookup: that
  // lookup uses `??`, so a blank `NEXTAUTH_SECRET=` line survives as an empty
  // string and 500s every sign-in inside HKDF. See lib/auth-secret.ts.
  secret: authSecret,
  session: { strategy: 'jwt' },
  pages: { signIn: '/login' },
  callbacks: {
    async jwt({ token, user }) {
      if (user) {
        const u = user as any
        token.accessToken = u.accessToken
        token.refreshToken = u.refreshToken
        token.role = u.role
        token.organization = u.organization
        token.timezone = u.timezone
        token.bio = u.bio
      }
      return token
    },
    async session({ session, token }) {
      if (session.user) {
        ;(session.user as any).id = token.sub
        ;(session.user as any).role = token.role
        ;(session.user as any).organization = token.organization
        ;(session.user as any).timezone = token.timezone
        ;(session.user as any).bio = token.bio
      }
      ;(session as any).accessToken = token.accessToken
      return session
    },
  },
}
