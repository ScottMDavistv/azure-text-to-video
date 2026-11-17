import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import assert from 'node:assert/strict'
const auth = readFileSync(fileURLToPath(new URL('../src/entraAuth.ts', import.meta.url)), 'utf8')
const app = readFileSync(fileURLToPath(new URL('../src/App.tsx', import.meta.url)), 'utf8')

// Static source-level mutation regression: run additionally to the Playwright
// browser suite, NOT in place of real or synthetic browser authentication.
function violations(a, ui) {
  const findings = []
  if (!/cacheLocation:\s*['"]memoryStorage['"]/.test(a)) findings.push('persistent MSAL cache')
  if (!/redirectUri:\s*getRedirectUri\s*\(\s*window\.location\.origin\s*\)/.test(a)) findings.push('redirect allowlist bypass')
  if (/\/(?:common|organizations)\b/i.test(a) || !/login\.microsoftonline\.com\/\$\{tenantId\}/.test(a)) findings.push('non-tenant authority')
  if (/acquireTokenRedirect\s*\(/.test(a) || !/acquireTokenPopup\s*\(/.test(a)) findings.push('redirect token fallback')
  if (!/logoutPopup\s*\(/.test(a)) findings.push('missing logoutPopup')
  if (/\bfetch\s*\(\s*['"]https?:\/\//.test(ui)) findings.push('unexpected external UI fetch')
  if (/\b(?:localStorage|sessionStorage)\s*(?:\.|\[)/.test(a + '\n' + ui)) findings.push('direct browser credential storage')
  return findings
}
assert.deepEqual(violations(auth, app), [], 'Baseline must satisfy all static auth constraints')
const changes = [
  ['M1', a => a.replace("cacheLocation: 'memoryStorage'", "cacheLocation: 'localStorage'"), null],
  ['M2', a => a.replace('redirectUri: getRedirectUri(window.location.origin)', 'redirectUri: window.location.origin'), null],
  ['M3', a => a.replace('https://login.microsoftonline.com/\${tenantId}', 'https://login.microsoftonline.com/common'), null],
  ['M4', a => a.replace('auth.acquireTokenPopup(', 'auth.acquireTokenRedirect('), null],
  ['M5', a => a.replace('await auth.logoutPopup(', 'await Promise.resolve('), null],
  ['M6', null, ui => ui.replace('export default function App() {', "export default function App() {\n  fetch('https://unauthorized-fal.invalid/');")],
  ['M7', a => a.replace('export async function signIn(): Promise<void> {', "export async function signIn(): Promise<void> {\n  sessionStorage.setItem('token', 'Bearer eyJplaceholder');"), null],
]
let detected = 0
for (const [id, mutateAuth, mutateUI] of changes) {
  const aa = mutateAuth ? mutateAuth(auth) : auth
  const uu = mutateUI ? mutateUI(app) : app
  assert.ok(aa !== auth || uu !== app, id + ': mutation failed to apply')
  const errors = violations(aa, uu)
  assert.ok(errors.length > 0, id + ': mutated source escaped security assertions')
  detected++
  console.log(id + ': DETECTED (' + errors.join(', ') + ')')
}
console.log('C1 SOURCE MUTATION REGRESSION: ' + detected + '/7 detected. Browser-level mutation proof is a separate gate.')
