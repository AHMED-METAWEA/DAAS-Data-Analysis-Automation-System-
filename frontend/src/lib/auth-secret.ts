/**
 * The one resolved NextAuth signing secret, shared by `auth-options.ts` (the
 * route handler) and `middleware.ts` (which decodes the session cookie).
 *
 * Both must agree: the route handler encodes the JWT and the middleware decodes
 * it, so a mismatch logs everyone out on the next request.
 *
 * `scripts/setup-env.mjs` normally guarantees `NEXTAUTH_SECRET` holds a real
 * value before Next.js boots. This module is the second line of defence, for
 * when the app is started in a way that bypasses it (a bare `next dev`, a
 * container with a half-populated env). It exists because NextAuth resolves the
 * secret with `??`, which treats the *blank* `NEXTAUTH_SECRET=` line in
 * `.env.local.example` as a legitimate value and then dies inside HKDF with
 * `TypeError: "ikm" must be at least one byte in length` — a 500 on every
 * sign-in that the UI can only report as "Incorrect email or password".
 * Normalising blank to "unset" here is what stops that.
 *
 * Kept dependency-free and synchronous so it is safe to import from the Edge
 * middleware bundle.
 */

// Deliberately a fixed string rather than a random one: a value regenerated per
// process would invalidate every session on each dev-server restart. It is only
// ever reachable in development — production throws below instead.
const DEVELOPMENT_FALLBACK_SECRET = 'daas-development-only-secret-not-for-production-use'

function resolveAuthSecret(): string {
  const configured = process.env.NEXTAUTH_SECRET?.trim()
  if (configured) return configured

  if (process.env.NODE_ENV === 'production') {
    throw new Error(
      'NEXTAUTH_SECRET is not set. Sign-in cannot work without it. Generate one with ' +
        '`openssl rand -base64 32` (or run `npm run setup` in frontend/) and put it in ' +
        'frontend/.env.local before starting the production server.'
    )
  }

  console.warn(
    '[auth] NEXTAUTH_SECRET is empty — falling back to a shared development secret. ' +
      'Run `npm run setup` in frontend/ to generate a real one.'
  )
  return DEVELOPMENT_FALLBACK_SECRET
}

export const authSecret = resolveAuthSecret()
