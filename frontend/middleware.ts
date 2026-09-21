import { withAuth } from 'next-auth/middleware'
import { NextResponse } from 'next/server'
import { authSecret } from '@/lib/auth-secret'

export default withAuth(
  function middleware(req) {
    const isAuthed = Boolean(req.nextauth.token)
    const { pathname } = req.nextUrl
    if (isAuthed && (pathname === '/' || pathname === '/login')) {
      return NextResponse.redirect(new URL('/command-center', req.url))
    }
    return NextResponse.next()
  },
  {
    // Must be the exact value auth-options.ts signs with — the route handler
    // encodes the session cookie and this decodes it. Passed explicitly for the
    // same reason it is there: withAuth's own fallback reads NEXTAUTH_SECRET
    // with `??`, so a blank line would leave every protected route 500ing.
    secret: authSecret,
    callbacks: {
      authorized: ({ req, token }) => {
        const { pathname } = req.nextUrl
        // Public: landing page, login page, and NextAuth's own API routes.
        if (pathname === '/' || pathname === '/login' || pathname.startsWith('/api/auth')) {
          return true
        }
        return Boolean(token)
      },
    },
    pages: { signIn: '/login' },
  }
)

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico|logo.svg|robots.txt).*)'],
}
