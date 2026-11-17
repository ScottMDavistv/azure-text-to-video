import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import assert from 'node:assert/strict'
// PR #46 (6b62c05) split the MSAL flow: entraAuth.ts wires the real MSAL
// classes; entraAuthCore.ts holds config, popup flows and the logout fail-safe.
const read = rel => readFileSync(fileURLToPath(new URL(rel, import.meta.url)), 'utf8')
const wrapper = read('../src/entraAuth.ts')
const core = read('../src/entraAuthCore.ts')
const app = read('../src/App.tsx')
const tenant = '773e9051-c546-4b75-9432-90f4f8242660'

// Static source-level mutation regression: run additionally to the Playwright
// browser suite, NOT in place of real or synthetic browser authentication.
function violations({ w, c, ui }) {
  const a = w + '\n' + c
  const findings = []
  if (!/cacheLocation:\s*['"]memoryStorage['"]/.test(c)) findings.push('persistent MSAL cache')
  if (!/storeAuthStateInCookie:\s*false/.test(c) || /storeAuthStateInCookie:\s*true/.test(a)) findings.push('auth state in cookies')
  if (!/const redirectUri = getRedirectUri\(origin\)/.test(c) || !/^\s*redirectUri,\s*$/m.test(c) ||
      !/postLogoutRedirectUri:\s*redirectUri,/.test(c) ||
      !/getOrigin:\s*\(\)\s*=>\s*window\.location\.origin,/.test(w)) findings.push('redirect allowlist bypass')
  if (/\/(?:common|organizations|consumers)\b/i.test(a) ||
      !/authority:\s*`https:\/\/login\.microsoftonline\.com\/\$\{tenantId\}`/.test(c) ||
      !w.includes("const tenantId = '" + tenant + "'")) findings.push('non-tenant authority')
  if (/acquireTokenRedirect\s*\(|loginRedirect\s*\(|logoutRedirect\s*\(/.test(a) || !/acquireTokenPopup\s*\(/.test(c)) findings.push('redirect token fallback')
  if (!/if \(options\.isInteractionRequired\(error\)\) \{/.test(c) ||
      !/isInteractionRequired:\s*\(error\)\s*=>\s*error instanceof InteractionRequiredAuthError,/.test(w)) findings.push('popup on non-interaction error')
  if (!/await auth\.logoutPopup\(/.test(c)) findings.push('missing logoutPopup')
  if (!/catch \(error\) \{\s*\/\/[^\n]*\n(?:\s*\/\/[^\n]*\n)*\s*auth\.setActiveAccount\(null\)\s*\n\s*try \{\s*\n\s*await auth\.clearCache\(\{ account: account \?\? undefined \}\)/.test(c)) findings.push('failed logout keeps local auth state')
  if (/\bfetch\s*\(\s*['"]https?:\/\//.test(ui)) findings.push('unexpected external UI fetch')
  if (/\b(?:localStorage|sessionStorage)\s*(?:\.|\[)/.test(a + '\n' + ui)) findings.push('direct browser credential storage')
  return findings
}
const base = { w: wrapper, c: core, ui: app }
assert.deepEqual(violations(base), [], 'Baseline must satisfy all static auth constraints')
const edit = (key, before, after) => src => {
  assert.ok(src[key].includes(before), 'mutation location not found: ' + before.slice(0, 60))
  return { ...src, [key]: src[key].replace(before, after) }
}
const changes = [
  ['M1', edit('c', "cacheLocation: 'memoryStorage'", "cacheLocation: 'localStorage'")],
  ['M2', edit('c', 'const redirectUri = getRedirectUri(origin)', 'const redirectUri = origin')],
  ['M3', edit('c', 'https://login.microsoftonline.com/${tenantId}', 'https://login.microsoftonline.com/common')],
  ['M4', edit('c', 'auth.acquireTokenPopup(', 'auth.acquireTokenRedirect(')],
  ['M5', edit('c', 'await auth.logoutPopup(', 'await Promise.resolve(')],
  ['M6', edit('ui', 'export default function App() {', "export default function App() {\n  fetch('https://unauthorized-fal.invalid/');")],
  ['M7', edit('c', '  async function signIn(): Promise<void> {', "  async function signIn(): Promise<void> {\n    sessionStorage.setItem('token', 'Bearer eyJplaceholder')")],
  ['M8', edit('c', 'storeAuthStateInCookie: false', 'storeAuthStateInCookie: true')],
  ['M9', edit('c', 'if (options.isInteractionRequired(error)) {', 'if (true) {')],
  ['M10', edit('w', 'isInteractionRequired: (error) => error instanceof InteractionRequiredAuthError,', 'isInteractionRequired: () => true,')],
  ['M11', edit('c', '        await auth.clearCache({ account: account ?? undefined })', '        void account')],
  ['M12', edit('c', '      auth.setActiveAccount(null)\n      try {', '      try {')],
]
let detected = 0
for (const [id, mutate] of changes) {
  const mutated = mutate(base)
  assert.ok(mutated.w !== wrapper || mutated.c !== core || mutated.ui !== app, id + ': mutation failed to apply')
  const errors = violations(mutated)
  assert.ok(errors.length > 0, id + ': mutated source escaped security assertions')
  detected++
  console.log(id + ': DETECTED (' + errors.join(', ') + ')')
}
assert.equal(detected, changes.length)
console.log('C1 SOURCE MUTATION REGRESSION: ' + detected + '/' + changes.length + ' detected. Browser-level mutation proof is a separate gate.')
