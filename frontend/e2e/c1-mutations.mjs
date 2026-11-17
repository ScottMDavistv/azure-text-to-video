// Single source of truth for the seven C1 security mutations, used by both the
// static source regression (c1-mutation-check.mjs) and the real browser
// mutation runner (run-browser-mutations.mjs). Anchors target the PR #46
// 6b62c05 implementation: MSAL configuration and popup flows live in
// src/entraAuthCore.ts; src/entraAuth.ts only injects the real MSAL runtime.
//
// `proof` must match the *received* value of the discriminating browser
// assertion, so unrelated failures (syntax errors, boot crashes, source
// regexes) are never counted as browser detections.
// `__C1_SINK_PORT__` is replaced by the runner with a local zero-delivery sink port.
export const SINK_PLACEHOLDER = '__C1_SINK_PORT__'

export const mutations = [
  {
    id: 'M1', property: 'memory-only MSAL cache', file: 'src/entraAuthCore.ts',
    before: "cacheLocation: 'memoryStorage'", after: "cacheLocation: 'localStorage'",
    grep: 'configured auth runtime is tenant-scoped', proof: /Received: "localStorage"/,
  },
  {
    id: 'M2', property: 'registered-origin redirect allowlist', file: 'src/entraAuthCore.ts',
    before: 'const redirectUri = getRedirectUri(origin)', after: 'const redirectUri = origin',
    grep: 'M2 browser:', proof: /Received: "http:\/\/127\.0\.0\.1:5173"/,
  },
  {
    id: 'M3', property: 'tenant-specific authority', file: 'src/entraAuthCore.ts',
    before: 'https://login.microsoftonline.com/${tenantId}', after: 'https://login.microsoftonline.com/common',
    grep: 'configured auth runtime is tenant-scoped', proof: /Received: "https:\/\/login\.microsoftonline\.com\/common"/,
  },
  {
    id: 'M4', property: 'popup-only token recovery', file: 'src/entraAuthCore.ts',
    before: 'return await auth.acquireTokenPopup(', after: 'return await (auth as any).acquireTokenRedirect(',
    grep: 'M4 browser:', proof: /\+\s+"redirect": 1,/,
  },
  {
    id: 'M5', property: 'popup logout executes', file: 'src/entraAuthCore.ts',
    before: 'await auth.logoutPopup(', after: 'await Promise.resolve(',
    grep: 'M5 browser:', proof: /\+\s+"count": 0,/,
  },
  {
    id: 'M6', property: 'no unauthorized UI egress (HTTP and WebSocket, zero delivery)', file: 'src/App.tsx',
    before: 'export default function App() {',
    after: "export default function App() {\n  void fetch('https://unauthorized-fal.invalid/').catch(() => {})\n" +
      "  void fetch('http://127.0.0.1:" + SINK_PLACEHOLDER + "/c1-m6-http', { method: 'POST', body: 'c1' }).catch(() => {})\n" +
      "  try { new WebSocket('ws://127.0.0.1:" + SINK_PLACEHOLDER + "/c1-m6-ws').onerror = () => {} } catch { /* blocked */ }",
    grep: 'M5 browser:', proof: /Blocked unauthorized HTTP origin https:\/\/unauthorized-fal\.invalid/,
    alsoProve: [/Blocked unauthorized HTTP origin http:\/\/127\.0\.0\.1:\d+/, /Blocked unauthorized WebSocket origin ws:\/\/127\.0\.0\.1:\d+/],
    zeroDelivery: true,
  },
  {
    id: 'M7', property: 'no credential in browser storage', file: 'src/entraAuthCore.ts',
    before: '  async function signIn(): Promise<void> {',
    after: "  async function signIn(): Promise<void> {\n    sessionStorage.setItem('token', 'Bearer eyJsynthetic-only')",
    grep: 'attempted login sends correct tenant', proof: /\+\s+"sessionSafe": false,/,
  },
]

export function applyMutation(text, mutation, sinkPort = '9') {
  const count = text.split(mutation.before).length - 1
  if (count !== 1) throw new Error(mutation.id + ': expected exactly one mutation anchor in ' + mutation.file + ', found ' + count)
  const mutated = text.replace(mutation.before, () => mutation.after.replaceAll(SINK_PLACEHOLDER, String(sinkPort)))
  if (mutated === text) throw new Error(mutation.id + ': mutation did not change ' + mutation.file)
  return mutated
}
