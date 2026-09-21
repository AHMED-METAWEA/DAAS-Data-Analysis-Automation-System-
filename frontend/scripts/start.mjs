#!/usr/bin/env node
/**
 * Starts the production (standalone) server.
 *
 * Replaces `NODE_ENV=production bun .next/standalone/server.js`, which assumed two
 * things that do not hold on a fresh Windows checkout: that `bun` is installed (it
 * is optional — the README offers npm as an alternative, and nothing else in the
 * project needs it), and that `VAR=value cmd` sets an environment variable, which is
 * POSIX shell syntax and a parse error in cmd.exe. Either one left `npm start`
 * broken on a machine where everything else worked.
 *
 * NODE_ENV is set before the import, not after: the server reads it as it loads.
 */

import { existsSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const frontendDir = dirname(dirname(fileURLToPath(import.meta.url)))
const server = join(frontendDir, '.next', 'standalone', 'server.js')

if (!existsSync(server)) {
  console.error('[start] .next/standalone/server.js is missing — run `npm run build` first.')
  process.exit(1)
}

/**
 * Loads frontend/.env* into the process before the server starts.
 *
 * Required, not optional: Next's standalone server does `process.chdir(__dirname)`
 * into `.next/standalone`, which has no `.env.local` — Next never copies it there.
 * Runtime-only server variables (NEXTAUTH_SECRET above all) are therefore invisible
 * to a production start, even though the identical `.env.local` works in dev.
 * `NEXT_PUBLIC_*` values are unaffected either way; those are inlined at build time.
 *
 * Loading here rather than copying `.env.local` into `.next/standalone` keeps the
 * secret out of the build output.
 */
async function loadEnv() {
  try {
    const { loadEnvConfig } = await import('@next/env')
    loadEnvConfig(frontendDir, false)
    return
  } catch {
    // @next/env is a transitive dependency of next; if a package manager did not
    // hoist it, fall back rather than starting with no configuration at all.
  }
  for (const file of ['.env', '.env.production', '.env.local']) {
    const path = join(frontendDir, file)
    if (!existsSync(path)) continue
    for (const line of readFileSync(path, 'utf8').split(/\r?\n/)) {
      if (!line.trim() || line.trimStart().startsWith('#')) continue
      const match = line.match(/^\s*(?:export\s+)?([\w.-]+)\s*=(.*)$/)
      if (!match) continue
      const value = match[2].trim().replace(/^(['"])([\s\S]*)\1$/, '$2')
      // Real environment variables win over file values, as Next itself does.
      process.env[match[1]] ??= value
    }
  }
}

await loadEnv()
process.env.NODE_ENV ??= 'production'

// pathToFileURL, not the bare path: a dynamic import() of an absolute Windows path
// like C:\... is parsed as a URL whose scheme is "c:" and fails with ERR_UNSUPPORTED_ESM_URL_SCHEME.
await import(pathToFileURL(server).href)
