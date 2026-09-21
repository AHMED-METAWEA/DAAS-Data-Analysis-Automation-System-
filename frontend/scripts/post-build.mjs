#!/usr/bin/env node
/**
 * Finishes a `next build` with `output: "standalone"`.
 *
 * Next writes the standalone server to `.next/standalone` but deliberately leaves
 * out `.next/static` and `public` — they are meant to be served by a CDN, so a
 * self-hosted standalone build has to copy them in itself or every stylesheet,
 * chunk and image 404s.
 *
 * This used to be `cp -r ... && cp -r ...` inside the npm script, which is POSIX
 * only: on Windows (where npm runs scripts through cmd.exe) `cp` does not exist and
 * the build failed after Next had already succeeded. `fs.cpSync` is the portable
 * equivalent and needs no dependency.
 */

import { cpSync, existsSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const frontendDir = dirname(dirname(fileURLToPath(import.meta.url)))
const standalone = join(frontendDir, '.next', 'standalone')

if (!existsSync(standalone)) {
  console.error(
    '[post-build] .next/standalone is missing — expected `next build` to have run first ' +
      'with output: "standalone" set in next.config.ts.'
  )
  process.exit(1)
}

/** Copies a build artifact into the standalone tree, skipping what does not exist. */
function copyInto(relativeSource, relativeTarget) {
  const source = join(frontendDir, relativeSource)
  if (!existsSync(source)) {
    console.log(`[post-build] no ${relativeSource} to copy — skipping`)
    return
  }
  cpSync(source, join(standalone, relativeTarget), { recursive: true })
  console.log(`[post-build] copied ${relativeSource} -> .next/standalone/${relativeTarget}`)
}

copyInto(join('.next', 'static'), join('.next', 'static'))
copyInto('public', 'public')
