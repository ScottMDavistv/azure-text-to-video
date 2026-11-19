// C1 harness supplemental SUPERVISED config (consent denial, cancelled logout).
// It never replaces the approved frontend/playwright.config.mjs; it serves the
// SAME approved, clean, exact-head worktree with the same fresh-server,
// no-trace/no-screenshot/no-video and exact-SHA rules. Live mode only.
import { execFileSync } from 'node:child_process'
import { defineConfig, devices } from '@playwright/test'
import { tmpdir } from 'node:os'
import { dirname, isAbsolute, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const frontend = process.env.C1_WORKTREE_FRONTEND || ''
if (!isAbsolute(frontend)) throw new Error('C1_WORKTREE_FRONTEND must be the absolute approved worktree frontend path')
// Reuse the exact tested frontend policy in supplemental supervised cases.
const { WEBRTC_UDP_POLICY_ARG } = await import(pathToFileURL(join(frontend, 'e2e/c1-webrtc-policy.mjs')).href)
const git = args => execFileSync('git', ['-C', frontend, ...args], { encoding: 'utf8' })
const sha = git(['rev-parse', 'HEAD']).trim()
const dirty = git(['status', '--porcelain', '--untracked-files=all']).trim()
const hidden = git(['ls-files', '-v']).split('\n').filter(line => /^[a-zS] /.test(line))
const expectedSha = process.env.C1_EXPECTED_SHA || ''
if (process.env.C1_LIVE !== '1') throw new Error('Supplemental supervised checks run only with C1_LIVE=1')
if (process.env.C1_MUTATION_MODE) throw new Error('C1_MUTATION_MODE is not permitted for supervised checks')
if (hidden.length) throw new Error('C1 exact-head validation rejects skip-worktree or assume-unchanged index entries')
if (dirty) throw new Error('C1 exact-head validation requires a clean tracked and untracked Git checkout')
if (!/^[0-9a-f]{40}$/.test(expectedSha) || expectedSha !== sha) throw new Error('C1_EXPECTED_SHA must equal the exact tested HEAD')
if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(process.env.VITE_ENTRA_SPA_CLIENT_ID || '')) {
  throw new Error('Live C1 requires the public VITE_ENTRA_SPA_CLIENT_ID (never a secret)')
}
if (!['consent-denial', 'cancelled-logout'].includes(process.env.C1_SUPERVISED_CASE || '')) {
  throw new Error('C1_SUPERVISED_CASE must be consent-denial or cancelled-logout')
}

export default defineConfig({
  testDir: dirname(fileURLToPath(import.meta.url)),
  testMatch: 'c1-supervised.spec.mjs',
  outputDir: join(tmpdir(), 'c1-supplemental-' + sha.slice(0, 12)),
  timeout: 300000,
  fullyParallel: false, workers: 1, retries: 0, reporter: 'list',
  use: {
    ...devices['Desktop Chrome'],
    launchOptions: { args: [WEBRTC_UDP_POLICY_ARG] },
    baseURL: 'http://localhost:5173',
    trace: 'off', screenshot: 'off', video: 'off',
  },
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1',
    cwd: frontend,
    url: 'http://127.0.0.1:5173',
    reuseExistingServer: false,
    timeout: 60000,
    env: {
      VITE_C1_SHA: sha,
      VITE_C1_DIRTY: 'false',
      VITE_ENTRA_SPA_CLIENT_ID: process.env.VITE_ENTRA_SPA_CLIENT_ID,
    },
  },
})
