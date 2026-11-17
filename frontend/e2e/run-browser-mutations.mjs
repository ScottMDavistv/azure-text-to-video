// Real offline browser mutations in isolated disposable Git worktrees.
// This runner never calls Microsoft, Azure, FAL or other external providers.
import { execFileSync, spawnSync } from 'node:child_process'
import { readFileSync, writeFileSync, symlinkSync, mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
if (process.env.C1_LIVE === '1') throw new Error('Never mutate a live Entra session')
const frontend = process.cwd()
const repo = resolve(frontend, '..')
const mutations = [
  { id: 'M1', file: 'src/entraAuth.ts', before: "cacheLocation: 'memoryStorage'", after: "cacheLocation: 'localStorage'", grep: 'configured auth runtime is tenant-scoped', proof: /localStorage/ },
  { id: 'M2', file: 'src/entraAuth.ts', before: 'redirectUri: getRedirectUri(window.location.origin)', after: 'redirectUri: window.location.origin', grep: 'M2 browser:', proof: /127\.0\.0\.1/ },
  { id: 'M3', file: 'src/entraAuth.ts', before: 'https://login.microsoftonline.com/$'+'{tenantId}', after: 'https://login.microsoftonline.com/common', grep: 'configured auth runtime is tenant-scoped', proof: /common/ },
  { id: 'M4', file: 'src/entraAuth.ts', before: 'auth.acquireTokenPopup(', after: 'auth.acquireTokenRedirect(', grep: 'M4 browser:', proof: /redirect: 1/ },
  { id: 'M5', file: 'src/entraAuth.ts', before: 'await auth.logoutPopup(', after: 'await Promise.resolve(', grep: 'M5 browser:', proof: /count: 0/ },
  { id: 'M6', file: 'src/App.tsx', before: 'export default function App() {', after: "export default function App() {\n  fetch('https://unauthorized-fal.invalid/');", grep: 'M5 browser:', proof: /unauthorized-fal\.invalid/ },
  { id: 'M7', file: 'src/entraAuth.ts', before: 'export async function signIn(): Promise<void> {', after: "export async function signIn(): Promise<void> {\n  sessionStorage.setItem('token', 'Bearer eyJsynthetic-only');", grep: 'attempted login sends correct tenant', proof: /sessionSafe/ },
]
const nodeModules = join(frontend, 'node_modules')
for (const m of mutations) {
  const temporary = mkdtempSync(join(tmpdir(), 'c1-browser-mutation-'))
  let added = false
  try {
    execFileSync('git', ['worktree', 'add', '--detach', temporary, 'HEAD'], { cwd: repo, stdio: 'pipe' })
    added = true
    const isolatedFrontend = join(temporary, 'frontend')
    symlinkSync(nodeModules, join(isolatedFrontend, 'node_modules'), 'dir')
    const target = join(isolatedFrontend, m.file)
    const before = readFileSync(target, 'utf8')
    if (!before.includes(m.before)) throw new Error(m.id + ' mutation location not found')
    writeFileSync(target, before.replace(m.before, m.after))
    const specFile = join(isolatedFrontend, 'e2e', 'entra-c1.spec.mjs')
    const spec = readFileSync(specFile, 'utf8')
    writeFileSync(specFile, spec.replaceAll('  assertSourceInvariants()\n', '  // source-only checks disabled for browser mutation\n'))
    const result = spawnSync(process.execPath, [
      join(nodeModules, '@playwright', 'test', 'cli.js'),
      'test', '--config=playwright.config.mjs', '--grep', m.grep,
      '--workers=1', '--reporter=list',
    ], {
      cwd: isolatedFrontend,
      env: { ...process.env, C1_LIVE: '0', C1_UNCONFIGURED: '0', C1_MUTATION_MODE: '1', VITE_ENTRA_SPA_CLIENT_ID: '' },
      encoding: 'utf8', timeout: 120000, maxBuffer: 2 * 1024 * 1024,
    })
    const output = (result.stdout || '') + '\n' + (result.stderr || '')
    const realAssertion = /Error: expect\(|expect\(received\)|expect\(/.test(output)
    if (result.status === 0 || !realAssertion || !m.proof.test(output)) {
      throw new Error(m.id + ': no discriminating browser assertion; status=' + result.status +
        ', assertion=' + realAssertion + ', proof=' + m.proof.test(output))
    }
    console.log(m.id + ': BROWSER DETECTED (runtime assertion)')
  } finally {
    if (added) {
      try { execFileSync('git', ['worktree', 'remove', '--force', temporary], { cwd: repo, stdio: 'pipe' }) }
      catch { /* preserve failure above */ }
    }
    rmSync(temporary, { recursive: true, force: true })
  }
}
console.log('C1 BROWSER MUTATIONS: 7/7 detected; no real auth or paid provider.')
