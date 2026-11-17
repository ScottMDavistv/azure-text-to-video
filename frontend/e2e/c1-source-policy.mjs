// Static C1 source policy shared by the Playwright spec and the source-level
// mutation regression. This is NOT runtime evidence: browser assertions in
// entra-c1.spec.mjs and run-browser-mutations.mjs are the C1 proof.
//
// PR #46 (6b62c05) splits authentication into:
//   src/entraAuth.ts      thin wrapper: real PublicClientApplication and the
//                         InteractionRequiredAuthError class, injected into the core
//   src/entraAuthCore.ts  MSAL configuration and popup login/token/logout flow
// Both files are inspected. coverageViolations() fails when MSAL usage or auth
// flow calls appear in any other src file, so the policy cannot silently pass
// while the real implementation has moved somewhere it no longer inspects.
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const srcRoot = fileURLToPath(new URL('../src/', import.meta.url))
export const AUTH_WRAPPER = 'entraAuth.ts'
export const AUTH_CORE = 'entraAuthCore.ts'
export const APP = 'App.tsx'

function listSources(dir = srcRoot) {
  const files = []
  for (const name of readdirSync(dir)) {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) files.push(...listSources(path))
    else if (/\.(?:ts|tsx|js|jsx|mjs)$/.test(name)) files.push(relative(srcRoot, path).split('\\').join('/'))
  }
  return files.sort()
}

export function loadSources() {
  const files = {}
  for (const file of listSources()) files[file] = readFileSync(join(srcRoot, file), 'utf8')
  for (const required of [AUTH_WRAPPER, AUTH_CORE, APP]) {
    if (typeof files[required] !== 'string' || files[required].trim().length === 0) {
      throw new Error('C1 source policy cannot inspect required file src/' + required)
    }
  }
  return files
}

// One import statement only: the body may not contain another `import` keyword.
const msalImports = /\bimport\s+(type\s+)?(?:(?!\bimport\b)[^;])*?from\s*['"]@azure\/msal-browser['"]/g
const msalValueImport = /^\s*import\s+(?!type\b)(?:(?!\bimport\b)[^;])*?from\s*['"]@azure\/msal-browser['"]/m
const authFlowCall = /\b(?:loginPopup|logoutPopup|acquireTokenPopup|acquireTokenSilent|acquireTokenRedirect|loginRedirect|logoutRedirect|handleRedirectPromise)\s*\(/
const redirectApi = /\b(?:acquireTokenRedirect|loginRedirect|logoutRedirect|handleRedirectPromise)\b/
const browserStorage = /\b(?:localStorage|sessionStorage)\s*(?:\.|\[)/

// Fails closed when the inspected files are no longer where authentication lives.
export function coverageViolations(files) {
  const findings = []
  for (const required of [AUTH_WRAPPER, AUTH_CORE, APP]) {
    if (typeof files[required] !== 'string' || !files[required].trim()) return ['required auth source missing: src/' + required]
  }
  const wrapper = files[AUTH_WRAPPER], core = files[AUTH_CORE], app = files[APP]
  for (const [file, text] of Object.entries(files)) {
    if (/\.test\./.test(file) || file === 'c1BuildInfo.ts') continue
    if (file !== AUTH_WRAPPER && file !== AUTH_CORE) {
      for (const m of text.matchAll(msalImports)) if (!m[1]) findings.push('uninspected MSAL runtime import in src/' + file)
    }
    if (file !== AUTH_CORE && authFlowCall.test(text)) findings.push('uninspected auth flow call in src/' + file)
    if (/\bnew\s+PublicClientApplication\b/.test(text) && file !== AUTH_WRAPPER) findings.push('uninspected PublicClientApplication in src/' + file)
  }
  if (!msalValueImport.test(wrapper)) findings.push('entraAuth.ts no longer supplies the real MSAL runtime')
  if (msalValueImport.test(core)) findings.push('entraAuthCore.ts imports MSAL values directly (expected type-only)')
  if (!/import\s*\{[^}]*\bcreateEntraAuth\b[^}]*\}\s*from\s*['"]\.\/entraAuthCore(?:\.ts)?['"]/.test(wrapper)) findings.push('entraAuth.ts does not delegate to entraAuthCore')
  if (!/createClient:\s*\(\s*config\s*\)\s*=>\s*new\s+PublicClientApplication\s*\(\s*config\s*\)/.test(wrapper)) findings.push('MSAL client not constructed from the core configuration')
  if (!/isInteractionRequired:\s*\(\s*error\s*\)\s*=>\s*error\s+instanceof\s+InteractionRequiredAuthError\b/.test(wrapper)) findings.push('interaction-required detection not bound to MSAL InteractionRequiredAuthError')
  if ((wrapper.match(/\bnew\s+PublicClientApplication\b/g) ?? []).length !== 1) findings.push('unexpected PublicClientApplication construction count')
  if (!/options\.createClient\(\s*buildMsalConfig\(/.test(core)) findings.push('core does not build the client from buildMsalConfig')
  if (!/getOrigin:\s*\(\)\s*=>\s*window\.location\.origin/.test(wrapper)) findings.push('origin source changed')
  if (!/from\s*['"]\.\/entraAuth(?:\.ts)?['"]/.test(app) || /entraAuthCore|@azure\/msal-browser/.test(app)) findings.push('App.tsx bypasses the entraAuth wrapper')
  return findings
}

// Same seven security properties as the browser mutations M1-M7.
export function securityViolations(files) {
  const findings = []
  if (coverageViolations(files)[0]?.startsWith('required auth source missing')) return ['security policy not evaluated']
  const wrapper = files[AUTH_WRAPPER], core = files[AUTH_CORE], app = files[APP]
  const auth = wrapper + '\n' + core
  if (!/cache:\s*\{\s*cacheLocation:\s*['"]memoryStorage['"]\s*,\s*storeAuthStateInCookie:\s*false\s*\}/.test(core)) findings.push('M1 persistent MSAL cache')
  if (!/const\s+redirectUri\s*=\s*getRedirectUri\(\s*origin\s*\)/.test(core) || !/\bredirectUri,\s*\n\s*postLogoutRedirectUri:\s*redirectUri\b/.test(core) ||
      !/if\s*\(\s*origin\s*===\s*LOCALHOST_REDIRECT\s*\|\|\s*origin\s*===\s*LOOPBACK_ALIAS\s*\)\s*\{\s*return\s+LOCALHOST_REDIRECT/.test(core) ||
      !/LOCALHOST_REDIRECT\s*=\s*'http:\/\/localhost:5173'/.test(core) || !/LOOPBACK_ALIAS\s*=\s*'http:\/\/127\.0\.0\.1:5173'/.test(core)) findings.push('M2 redirect allowlist bypass')
  if (/\/(?:common|organizations|consumers)\b/i.test(auth) || !/authority:\s*`https:\/\/login\.microsoftonline\.com\/\$\{tenantId\}`/.test(core) ||
      !/const\s+tenantId\s*=\s*'773e9051-c546-4b75-9432-90f4f8242660'/.test(wrapper)) findings.push('M3 non-tenant authority')
  if (redirectApi.test(auth) || !/return\s+await\s+auth\.acquireTokenPopup\(/.test(core) || !/if\s*\(\s*options\.isInteractionRequired\(\s*error\s*\)\s*\)/.test(core)) findings.push('M4 redirect token fallback')
  if (!/await\s+auth\.logoutPopup\(\s*\{\s*account:/.test(core) || !/await\s+auth\.loginPopup\(/.test(core)) findings.push('M5 popup logout/login missing')
  if (/\bfetch\s*\(\s*['"`]https?:\/\//.test(app) || /\bnew\s+WebSocket\s*\(/.test(app + auth)) findings.push('M6 unexpected external UI egress')
  if (browserStorage.test(auth + '\n' + app)) findings.push('M7 direct browser credential storage')
  return findings
}

export function sourceViolations(files = loadSources()) {
  return [...coverageViolations(files), ...securityViolations(files)]
}
