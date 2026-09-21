#!/usr/bin/env node
/**
 * Idempotent bootstrap for `frontend/.env.local`, run automatically by the
 * `dev` and `build` scripts.
 *
 * Why this exists: `.env.local` is gitignored, so a fresh clone on another
 * machine has no NextAuth secret. The documented setup step
 * (`cp .env.local.example .env.local`) copies a file whose `NEXTAUTH_SECRET=`
 * line is *blank*, and a blank secret is worse than a missing one — NextAuth
 * resolves it with `??`, so an empty string is treated as a real value and
 * every `POST /api/auth/callback/credentials` dies inside HKDF with
 * `TypeError: "ikm" must be at least one byte in length`. Sign-in and sign-up
 * both fail, and the UI reports it as "Incorrect email or password".
 *
 * So: generate a real secret before Next.js starts, rather than relying on
 * anyone remembering to. Doing it here (as opposed to only defaulting inside
 * the app) means *every* consumer of the value agrees — the route handler,
 * `middleware.ts`, and any `getToken()` call all read `process.env` directly.
 *
 * Never overwrites a value that is already set.
 */

import { randomBytes } from 'node:crypto'
import { existsSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const frontendDir = dirname(dirname(fileURLToPath(import.meta.url)))
const envPath = join(frontendDir, '.env.local')
const examplePath = join(frontendDir, '.env.local.example')

/** Keys that must hold a real value, and how to invent one when they don't. */
const GENERATED = {
  NEXTAUTH_SECRET: () => randomBytes(32).toString('base64'),
}

/**
 * The value currently assigned to `key`, or `null` if the key is absent,
 * commented out, or assigned an empty/whitespace-only value. Quotes are
 * stripped so `NEXTAUTH_SECRET=""` counts as blank too.
 */
function currentValue(lines, key) {
  const pattern = new RegExp(`^\\s*(?:export\\s+)?${key}\\s*=(.*)$`)
  for (const line of lines) {
    if (line.trimStart().startsWith('#')) continue
    const match = line.match(pattern)
    if (!match) continue
    const value = match[1].trim().replace(/^(['"])(.*)\1$/, '$2').trim()
    return value === '' ? null : value
  }
  return null
}

/** Replaces `key`'s line in place, or appends the assignment if absent. */
function setValue(lines, key, value) {
  const pattern = new RegExp(`^\\s*(?:export\\s+)?${key}\\s*=`)
  const index = lines.findIndex((line) => !line.trimStart().startsWith('#') && pattern.test(line))
  if (index === -1) {
    if (lines.length && lines[lines.length - 1].trim() !== '') lines.push('')
    lines.push(`${key}=${value}`)
    return lines
  }
  lines[index] = `${key}=${value}`
  return lines
}

function main() {
  let created = false
  if (!existsSync(envPath)) {
    // Start from the example so the comments documenting every optional key
    // (the OAuth pairs especially) survive into the generated file.
    const seed = existsSync(examplePath) ? readFileSync(examplePath, 'utf8') : ''
    writeFileSync(envPath, seed, 'utf8')
    created = true
  }

  const original = readFileSync(envPath, 'utf8')
  let lines = original.split(/\r?\n/)
  const filled = []

  for (const [key, generate] of Object.entries(GENERATED)) {
    if (currentValue(lines, key) !== null) continue
    lines = setValue(lines, key, generate())
    filled.push(key)
  }

  if (filled.length === 0) {
    if (created) console.log('[setup-env] created frontend/.env.local from the example')
    return
  }

  writeFileSync(envPath, lines.join('\n'), 'utf8')
  console.log(
    `[setup-env] ${created ? 'created frontend/.env.local and generated' : 'generated a missing'} ` +
      `${filled.join(', ')} — sign-in/sign-up would 500 without it.`
  )
}

try {
  main()
} catch (error) {
  // A bootstrap failure must not be silent: without a secret, auth breaks in a
  // way whose symptom ("Incorrect email or password") points nowhere near here.
  console.error('[setup-env] could not prepare frontend/.env.local:', error.message)
  console.error('[setup-env] set NEXTAUTH_SECRET manually — see the README setup section.')
  process.exitCode = 1
}
