import { execFileSync } from 'node:child_process'
import { defineConfig, devices } from '@playwright/test'

const live = process.env.C1_LIVE === '1'
const unconfigured = process.env.C1_UNCONFIGURED === '1'
const sha = execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim()
if (live && unconfigured) throw new Error('C1_LIVE and C1_UNCONFIGURED cannot both be set')
if (live && !/^[0-9a-f-]{36}$/i.test(process.env.VITE_ENTRA_SPA_CLIENT_ID || '')) {
  throw new Error('Live C1 requires the public VITE_ENTRA_SPA_CLIENT_ID (never a secret)')
}
export default defineConfig({
  testDir: './e2e',
  timeout: live ? 300000 : 45000,
  fullyParallel: false, retries: 0, reporter: 'list',
  use: {
    ...devices['Desktop Chrome'],
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
      VITE_ENTRA_SPA_CLIENT_ID: live
        ? process.env.VITE_ENTRA_SPA_CLIENT_ID
        : unconfigured ? '' : '00000000-0000-4000-8000-000000000000',
    },
  },
})
