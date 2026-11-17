import { execFileSync } from 'node:child_process'
import { defineConfig, devices } from '@playwright/test'

const live = process.env.C1_LIVE === '1'
const sha = execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim()
export default defineConfig({
  testDir: './e2e',
  timeout: live ? 300000 : 30000,
  fullyParallel: false, retries: 0, reporter: 'list',
  use: {
    ...devices['Desktop Chrome'],
    baseURL: 'http://localhost:5173',
    trace: 'off', screenshot: 'off', video: 'off',
  },
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1',
    url: 'http://127.0.0.1:5173',
    reuseExistingServer: live ? false : !process.env.CI,
    env: { VITE_C1_SHA: sha },
  },
})
