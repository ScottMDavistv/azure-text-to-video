import { defineConfig, devices } from '@playwright/test'
export default defineConfig({
  testDir: './e2e', timeout: process.env.C1_LIVE === '1' ? 300000 : 30000,
  fullyParallel: false, retries: 0, reporter: 'list',
  use: { ...devices['Desktop Chrome'], baseURL: 'http://localhost:5173', trace: 'off', screenshot: 'off', video: 'off' },
  webServer: { command: 'npm run dev -- --host 127.0.0.1', url: 'http://127.0.0.1:5173', reuseExistingServer: !process.env.CI },
})
