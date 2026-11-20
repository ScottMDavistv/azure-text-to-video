import { execFileSync } from 'node:child_process'
import { defineConfig, devices } from '@playwright/test'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { WEBRTC_UDP_POLICY_ARG, headedWebrtcLaunchOptions } from './e2e/c1-webrtc-policy.mjs'

const live = process.env.C1_LIVE === '1'
const unconfigured = process.env.C1_UNCONFIGURED === '1'
const sha = execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim()
const dirty = execFileSync('git', ['status', '--porcelain', '--untracked-files=all'], { encoding: 'utf8' }).trim()
const trackedFlags = execFileSync('git', ['ls-files', '-v'], { encoding: 'utf8' }).split('\n')
const hiddenIndex = trackedFlags.filter(line => /^[a-zS] /.test(line))
if (hiddenIndex.length) throw new Error('C1 exact-head validation rejects skip-worktree or assume-unchanged index entries')
const expectedSha = process.env.C1_EXPECTED_SHA || ''
const mutationMode = process.env.C1_MUTATION_MODE === '1'
if (live && mutationMode) throw new Error('C1_MUTATION_MODE cannot run with live Entra credentials')
if (dirty && !mutationMode) throw new Error('C1 exact-head validation requires a clean tracked and untracked Git checkout')
if (mutationMode && !process.env.CI) console.log('C1 OFFLINE MUTATION MODE: dirty checkout permitted; no live certification')
if (live && (!/^[0-9a-f]{40}$/i.test(expectedSha) || expectedSha !== sha)) {
  throw new Error('C1_LIVE requires C1_EXPECTED_SHA equal to the exact tested HEAD')
}
if (live && unconfigured) throw new Error('C1_LIVE and C1_UNCONFIGURED cannot both be set')
if (live && !/^[0-9a-f-]{36}$/i.test(process.env.VITE_ENTRA_SPA_CLIENT_ID || '')) {
  throw new Error('Live C1 requires the public VITE_ENTRA_SPA_CLIENT_ID (never a secret)')
}
export default defineConfig({
  testDir: './e2e',
  outputDir: join(tmpdir(), 'c1-playwright-' + sha.slice(0, 12)),
  timeout: live ? 300000 : 45000,
  fullyParallel: false, retries: 0, reporter: 'list',
  use: {
    ...devices['Desktop Chrome'],
    // L-2: browser-enforced policy applies to all pages, popups, frames and contexts.
    // HTTP/WebSocket routing alone does not intercept WebRTC STUN UDP.
    launchOptions: live ? headedWebrtcLaunchOptions : { args: [WEBRTC_UDP_POLICY_ARG] },
    baseURL: 'http://localhost:5173',
    trace: 'off', screenshot: 'off', video: 'off',
  },
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1',
    url: 'http://127.0.0.1:5173',
    reuseExistingServer: false,
    timeout: 60000,
    env: {
      VITE_C1_SHA: sha,
      VITE_C1_DIRTY: dirty ? 'true' : 'false',
      VITE_ENTRA_SPA_CLIENT_ID: live
        ? process.env.VITE_ENTRA_SPA_CLIENT_ID
        : unconfigured ? '' : '00000000-0000-4000-8000-000000000000',
    },
  },
})
