// Development-only Chromium browser race gate. No live OAuth or backend.
import { createServer } from 'vite'
import react from '@vitejs/plugin-react'
import { spawn } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

process.env.VITE_ENABLE_AUTHENTICATED_JOURNEY = 'true'
process.env.VITE_VIDEO_PROJECT_ID = 'c7e6a845-cb0c-468c-9a18-1e9df0b20545'
process.env.VITE_VIDEO_API_ORIGIN = ''
const fakeAuth = resolve('e2e/fakeAuth.mjs')
const server = await createServer({
  configFile: false,
  root: process.cwd(),
  plugins: [react()],
  resolve: { alias: [{find: /^\.\/entraAuth$/, replacement: fakeAuth}] },
  server: { host: '127.0.0.1', port: 0, strictPort: false },
  logLevel: 'error',
})
let browser
const profile = await mkdtemp(join(tmpdir(), 'c42-browser-'))
try {
  await server.listen()
  const port = server.httpServer.address().port
  const url = 'http://127.0.0.1:' + port + '/e2e/session-race.html'
  const chrome = process.env.CHROME_BIN || 'google-chrome'
  browser = spawn(chrome, [
    '--headless=new', '--no-sandbox', '--disable-gpu',
    '--disable-dev-shm-usage', '--no-first-run', '--no-default-browser-check',
    '--disable-background-networking', '--disable-extensions',
    '--user-data-dir=' + profile, '--virtual-time-budget=24000',
    '--dump-dom', url,
  ], {stdio:['ignore','pipe','pipe']})
  let stdout = '', stderr = ''
  browser.stdout.on('data', c => { stdout += c.toString() })
  browser.stderr.on('data', c => { stderr += c.toString() })
  const exitCode = await Promise.race([
    new Promise(resolve => browser.on('close', resolve)),
    new Promise((_, reject) => {
      const timeout = setTimeout(() => reject(new Error('Browser race timed out')), 60000)
      timeout.unref()
    }),
  ])
  if (exitCode !== 0 || !stdout.includes('data-result="PASS"') || stdout.includes('data-result="FAIL"')) {
    throw new Error('Chromium session-race test failed (exit=' + exitCode + '): ' +
      stdout.slice(Math.max(0, stdout.indexOf('race-result') - 50), stdout.indexOf('race-result') + 1600) +
      '\\n' + stderr.slice(-1000))
  }
  console.log('PASS: headless Chromium account-switch, stale request, revoked URL, logout race')
} finally {
  if (browser && browser.exitCode === null) browser.kill('SIGKILL')
  await server.close()
  await rm(profile, {recursive:true,force:true})
}
