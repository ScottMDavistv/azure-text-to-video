import { test, expect } from '@playwright/test'
import { createServer } from 'node:http'
import { requireFreshPermission, requireMicrosoftPopupOrigin } from './c1-operation-evidence.mjs'
import { loadSources, sourceViolations } from './c1-source-policy.mjs'
import {
  live, sha, checkoutDirty, allowedHmrOrigins, allowedRequest,
  observeContext, assertBuild, assertMemoryOnly,
} from './c1-guards.mjs'

const unconfigured = process.env.C1_UNCONFIGURED === '1'
const tenant = '773e9051-c546-4b75-9432-90f4f8242660'
const audience = 'a5a09b74-aead-455f-9de7-9b844c8a8465'
const scope = 'api://' + audience + '/access_as_user'


test('C1 exact-host regression: Microsoft login host is HTTPS-only; spoofed hosts stay denied', () => {
  // These are URL policy assertions only; no requests are sent to Microsoft.
  for (const allowed of [
    'https://login.microsoftonline.com/773e9051-c546-4b75-9432-90f4f8242660/',
    'https://login.microsoft.com/773e9051-c546-4b75-9432-90f4f8242660/',
  ]) {
    expect(allowedRequest(new URL(allowed)), allowed).toBe(true)
  }
  for (const forbidden of [
    'http://login.microsoft.com/',
    'https://login.microsoft.com.evil.example/',
    'https://notlogin.microsoft.com/',
    'https://login.microsoft.com.attacker.invalid/',
    'https://unapproved.example/',
    'https://localhost:5174/',
    'http://127.0.0.1:5174/',
    'ws://login.microsoft.com/',
    'wss://login.microsoft.com/',
    'https://login.microsoft.com:8443/',
    'https://login.microsoftonline.com:444/',
  ]) {
    expect(allowedRequest(new URL(forbidden)), forbidden).toBe(false)
  }
  expect(allowedHmrOrigins.has('wss://login.microsoft.com')).toBe(false)
  expect(allowedHmrOrigins.has('ws://unapproved.example')).toBe(false)
})


// Evidence contains only a commit identifier, browser version, timestamps and
// test status. Do not print request URLs, JWTs, cookies or browser storage.
let evidenceBrowser = 'unknown'
test.beforeAll(async ({ browser }) => {
  evidenceBrowser = browser.version()
  console.log('C1 BEGIN sha=' + sha + ' dirty=' + checkoutDirty +
    ' browser=' + evidenceBrowser + ' utc=' + new Date().toISOString())
})
test.afterEach(async ({}, info) => {
  console.log('C1 TEST outcome=' + info.status + ' sha=' + sha +
    ' browser=' + evidenceBrowser + ' utc=' + new Date().toISOString() +
    ' title=' + info.title.replace(/[^a-z0-9 -]/gi, '_'))
})
test.afterAll(async () => {
  console.log('C1 END sha=' + sha + ' browser=' + evidenceBrowser +
    ' utc=' + new Date().toISOString())
})

function assertSourceInvariants() {
  // Static structural checks (not runtime evidence) over entraAuth.ts,
  // entraAuthCore.ts and App.tsx; fails closed if auth moves to an
  // uninspected file. See c1-source-policy.mjs.
  expect(sourceViolations(loadSources())).toEqual([])
}

test('M-1/M-4/M-5: configured auth runtime is tenant-scoped, memory-only, correctly redirected, no unauthorized egress', async ({ page, context }) => {
  test.skip(live || unconfigured, 'Synthetic offline configuration only')
  const audit = await observeContext(context)
  assertSourceInvariants()
  await page.goto('/')
  await assertBuild(page)
  const config = await page.evaluate(async () => {
    const { initializeAuth, getRedirectUri, apiScopes } = await import('/src/entraAuth.ts')
    const auth = await initializeAuth()
    const c = auth.getConfiguration()
    let unexpectedOriginRejected = false
    try { getRedirectUri('https://untrusted.example') } catch { unexpectedOriginRejected = true }
    return {
      authority: c.auth.authority, redirect: c.auth.redirectUri,
      cache: c.cache.cacheLocation, cookie: c.cache.storeAuthStateInCookie,
      scopes: apiScopes, unexpectedOriginRejected,
    }
  })
  expect(config.authority).toBe('https://login.microsoftonline.com/' + tenant)
  expect(config.redirect).toBe('http://localhost:5173')
  expect(config.cache).toBe('memoryStorage')
  expect(config.cookie).toBe(false)
  expect(config.scopes).toEqual([scope])
  expect(config.unexpectedOriginRejected).toBe(true)
  await assertMemoryOnly(page, context)
  await page.waitForTimeout(250) // permit post-mount egress mutations to surface
  expect(audit.problems).toEqual([])
})

test('M-1/M-2: attempted login sends correct tenant, PKCE, registered URI, client and scopes; blocked login fails closed', async ({ page, context }) => {
  test.skip(live || unconfigured, 'Synthetic offline configuration only')
  const audit = await observeContext(context)
  const authorizations = []
  context.on('request', request => {
    if (request.url().includes('/oauth2/v2.0/authorize')) {
      // Capture URL in-memory ONLY; do not attach the sensitive URL to reports.
      const url = new URL(request.url())
      authorizations.push({
        host: url.hostname, pathname: url.pathname, tenant: url.pathname.split('/')[1],
        redirect: url.searchParams.get('redirect_uri'),
        client: url.searchParams.get('client_id'),
        scopes: url.searchParams.get('scope')?.split(' ') ?? [],
        responseType: url.searchParams.get('response_type'),
        mode: url.searchParams.get('response_mode'),
        challengeMethod: url.searchParams.get('code_challenge_method'),
        challengePresent: Boolean(url.searchParams.get('code_challenge')),
      })
    }
  })
  await page.goto('/')
  await assertBuild(page)
  await page.evaluate(async () => {
    const { initializeAuth } = await import('/src/entraAuth.ts')
    const auth = await initializeAuth()
    const original = auth.loginPopup.bind(auth)
    window.__c1OfflineCancelCode = 'not_observed'
    auth.loginPopup = async (...args) => {
      try { return await original(...args) }
      catch (error) {
        window.__c1OfflineCancelCode = String(error?.errorCode || 'missing_error_code')
        throw error
      }
    }
  })
  await page.getByRole('button', { name: 'Sign in with Microsoft' }).click()
  await expect.poll(() => authorizations.length, { timeout: 15000 }).toBeGreaterThan(0)
  const request = authorizations[0]
  expect(request.host).toBe('login.microsoftonline.com')
  expect(request.tenant).toBe(tenant)
  expect(request.redirect).toBe('http://localhost:5173')
  expect(request.client).toBe('00000000-0000-4000-8000-000000000000')
  expect(request.scopes).toContain(scope)
  expect(request.responseType).toContain('code')
  expect(request.challengeMethod).toBe('S256')
  expect(request.challengePresent).toBe(true)
  expect(['fragment', 'query']).toContain(request.mode)
  // Close the fake popup, without contacting Microsoft.
  for (const popup of context.pages().filter(p => p !== page)) await popup.close().catch(() => {})
  await expect.poll(() => page.evaluate(() => window.__c1OfflineCancelCode), { timeout: 15000 }).toBe('user_cancelled')
  await expect(page.getByRole('button', { name: 'Sign in with Microsoft' })).toBeEnabled({ timeout: 15000 })
  await expect(page.getByText(/^Signed in:/)).toHaveCount(0)
  await assertMemoryOnly(page, context)
  expect(audit.problems).toEqual([])
})


// Browser-level mutation checks do not rely on the static source regexes.
test('M2 browser: loopback-origin redirect remains canonical localhost', async ({ page, context }) => {
  test.skip(live || unconfigured, 'Offline synthetic SPA only')
  const audit = await observeContext(context)
  await page.goto('http://127.0.0.1:5173')
  await assertBuild(page)
  const actual = await page.evaluate(async () => {
    const { initializeAuth } = await import('/src/entraAuth.ts')
    return (await initializeAuth()).getConfiguration().auth.redirectUri
  })
  expect(actual).toBe('http://localhost:5173')
  expect(audit.problems).toEqual([])
})

test('M4 browser: interaction-required token recovery invokes popup, never redirect', async ({ page, context }) => {
  test.skip(live || unconfigured, 'Offline synthetic SPA only')
  const audit = await observeContext(context)
  await page.goto('/')
  const calls = await page.evaluate(async () => {
    const { initializeAuth, acquireApiToken } = await import('/src/entraAuth.ts')
    // Resolve the exact optimized MSAL module URL imported by Vite's
    // transformed entraAuth.ts; a bare package import in page.evaluate
    // cannot resolve and a second module instance breaks instanceof.
    const transformed = await (await fetch('/src/entraAuth.ts')).text()
    const msalUrl = transformed.match(/from\s*["']([^"']*azure_msal-browser[^"']*)["']/)?.[1]
    if (!msalUrl || !msalUrl.startsWith('/node_modules/.vite/deps/')) {
      throw new Error('Cannot resolve the MSAL module instance used by entraAuth')
    }
    const { InteractionRequiredAuthError } = await import(/* @vite-ignore */ msalUrl)
    const auth = await initializeAuth()
    const account = { homeAccountId: 'synthetic-account', username: 'synthetic@example.invalid' }
    const original = {
      getActiveAccount: auth.getActiveAccount, acquireTokenSilent: auth.acquireTokenSilent,
      acquireTokenPopup: auth.acquireTokenPopup, acquireTokenRedirect: auth.acquireTokenRedirect,
      loginRedirect: auth.loginRedirect,
    }
    const counts = { popup: 0, redirect: 0, result: false, sameClass: false }
    try {
      const injected = new InteractionRequiredAuthError('interaction_required')
      counts.sameClass = injected instanceof InteractionRequiredAuthError
      auth.getActiveAccount = () => account
      auth.acquireTokenSilent = async () => { throw injected }
      auth.acquireTokenPopup = async () => { counts.popup++; return { accessToken: 'synthetic-non-token' } }
      auth.acquireTokenRedirect = async () => { counts.redirect++ }
      auth.loginRedirect = async () => { counts.redirect++ }
      const result = await acquireApiToken()
      counts.result = result?.accessToken === 'synthetic-non-token'
      return counts
    } finally {
      for (const [key, method] of Object.entries(original)) auth[key] = method
    }
  })
  expect(calls).toEqual({ popup: 1, redirect: 0, result: true, sameClass: true })
  expect(audit.problems).toEqual([])
})

test('M5 browser: signOut invokes MSAL logoutPopup for the selected account', async ({ page, context }) => {
  test.skip(live || unconfigured, 'Offline synthetic SPA only')
  const audit = await observeContext(context)
  await page.goto('/')
  const calls = await page.evaluate(async () => {
    const { initializeAuth, signOut } = await import('/src/entraAuth.ts')
    const auth = await initializeAuth()
    const account = { homeAccountId: 'synthetic-account', username: 'synthetic@example.invalid' }
    const original = { getActiveAccount: auth.getActiveAccount, logoutPopup: auth.logoutPopup }
    let count = 0
    let correctAccount = false
    try {
      auth.getActiveAccount = () => account
      auth.logoutPopup = async request => {
        count++
        correctAccount = request?.account?.homeAccountId === account.homeAccountId
      }
      await signOut()
      return { count, correctAccount }
    } finally {
      for (const [key, method] of Object.entries(original)) auth[key] = method
    }
  })
  expect(calls).toEqual({ count: 1, correctAccount: true })
  expect(audit.problems).toEqual([])
})

// Local zero-delivery sink: counts every HTTP request and WebSocket upgrade.
async function startSink() {
  const hits = []
  const server = createServer((request, response) => { hits.push('http ' + request.method); response.end('c1-sink') })
  server.on('upgrade', (_request, socket) => { hits.push('ws upgrade'); socket.destroy() })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  return { port: server.address().port, hits, close: () => new Promise(resolve => server.close(resolve)) }
}

test('M6-B/M6-C browser: egress firewall blocks wrong-port HTTP and WebSockets with zero delivery (positive-control sink)', async ({ page, context, browser }) => {
  test.skip(live || unconfigured, 'Offline synthetic egress probe only')
  const sink = await startSink()
  try {
    // Positive control: an unguarded browser context CAN reach the sink, so a
    // zero count below proves blocking rather than an unreachable target.
    const control = await browser.newContext()
    const controlPage = await control.newPage()
    await controlPage.goto('http://127.0.0.1:' + sink.port + '/c1-positive-control')
    await control.close()
    expect(sink.hits.length).toBeGreaterThan(0)
    sink.hits.length = 0

    const audit = await observeContext(context)
    await page.goto('/')
    await page.evaluate(port => {
      void fetch('http://localhost:' + port + '/api/jobs', { method: 'POST', body: 'c1' }).catch(() => {})
      void fetch('http://localhost:8000/api/jobs', { method: 'POST' }).catch(() => {})
      const socket = new WebSocket('ws://127.0.0.1:' + port + '/ws')
      socket.onerror = () => {}
    }, sink.port)
    await expect.poll(() => audit.problems.some(p => p === 'Blocked unauthorized HTTP origin http://localhost:' + sink.port)).toBe(true)
    await expect.poll(() => audit.problems.some(p => p === 'Blocked unauthorized HTTP origin http://localhost:8000')).toBe(true)
    await expect.poll(() => audit.problems.some(p => p === 'Blocked unauthorized WebSocket origin ws://127.0.0.1:' + sink.port)).toBe(true)
    await page.waitForTimeout(500)
    expect(sink.hits).toEqual([])
  } finally {
    await sink.close()
  }
})

test('M-1: missing SPA configuration is fail-closed', async ({ page, context }) => {
  test.skip(!unconfigured || live, 'Run separately with C1_UNCONFIGURED=1')
  const audit = await observeContext(context)
  await page.goto('/')
  await assertBuild(page)
  await expect(page.getByRole('button', { name: 'Sign in with Microsoft' })).toBeDisabled()
  await expect(page.getByRole('status').first()).toContainText('registration is required')
  expect(audit.problems).toEqual([])
})

test('Static source invariants (not runtime evidence)', async () => {
  assertSourceInvariants()
})

async function assertLocalMsalSignedOut(page) {
  const state = await page.evaluate(async () => {
    const { initializeAuth, getSignedInAccount, acquireApiToken } = await import('/src/entraAuth.ts')
    const client = await initializeAuth()
    let noAccountTokenRejection = false
    try {
      await acquireApiToken()
    } catch (error) {
      noAccountTokenRejection = error?.message === 'Sign in with Microsoft before requesting API permission'
    }
    return {
      activeEmpty: client.getActiveAccount() === null,
      allEmpty: client.getAllAccounts().length === 0,
      appAccountEmpty: (await getSignedInAccount()) === null,
      noAccountTokenRejection,
    }
  })
  expect(state).toEqual({ activeEmpty: true, allEmpty: true, appAccountEmpty: true, noAccountTokenRejection: true })
}

test('LIVE: popup login, validated claims, silent acquisition and verified local MSAL cache clearing', async ({ page, context, browser }) => {
  test.skip(!live, 'C1 live requires explicit authorized supervised account and SPA')
  const audit = await observeContext(context)
  await page.goto('/')
  await assertBuild(page)
  const popups = []
  context.on('page', popup => { if (popup !== page) popups.push(popup) })
  await page.getByRole('button', { name: 'Sign in with Microsoft' }).click()
  await page.pause() // operator completes sign-in/consent, then resumes
  await expect(page.getByText(/^Signed in:/)).toBeVisible()
  const result = await page.evaluate(async () => {
    const { initializeAuth, getSignedInAccount, acquireApiToken } = await import('/src/entraAuth.ts')
    const auth = await initializeAuth()
    const account = await getSignedInAccount()
    if (!account?.homeAccountId || auth.getAllAccounts().length === 0) return { ok: false }
    const token = await acquireApiToken()
    if (!token?.accessToken?.includes('.')) return { ok: false }
    const payload = token.accessToken.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')
    const claims = JSON.parse(atob(payload.padEnd(Math.ceil(payload.length / 4) * 4, '=')))
    const expectedTenant = '773e9051-c546-4b75-9432-90f4f8242660'
    const expectedAudience = 'a5a09b74-aead-455f-9de7-9b844c8a8465'
    const client = auth.getConfiguration().auth.clientId
    const authResult = {
      ok: [expectedAudience, 'api://' + expectedAudience].includes(claims.aud) &&
        claims.tid === expectedTenant &&
        String(claims.scp || '').split(' ').includes('access_as_user') &&
        (claims.azp ?? claims.appid) === client &&
        Number(claims.exp) > Math.floor(Date.now() / 1000) &&
        [1, 2].includes(Number(claims.ver)) &&
        (claims.iss === 'https://sts.windows.net/' + expectedTenant + '/' ||
         claims.iss === 'https://login.microsoftonline.com/' + expectedTenant + '/v2.0'),
    }
    // Probe silent MSAL directly and export no access tokens or claims.
    const silent = await auth.acquireTokenSilent({ account, scopes: ['api://' + expectedAudience + '/access_as_user'] })
    authResult.silent = Boolean(silent.accessToken)
    return authResult
  })
  expect(result).toEqual({ ok: true, silent: true })
  // A popup on repeat acquisition means silent behavior is broken.
  const beforeRepeat = popups.length
  await requireFreshPermission(
    page.getByRole('button', { name: 'Check API permission' }),
    page.getByRole('status').first(),
    expect,
  )
  expect(popups.length).toBe(beforeRepeat)
  await assertMemoryOnly(page, context)
  await page.getByRole('button', { name: 'Sign out' }).click()
  await page.pause() // operator completes Microsoft logout popup, then resumes
  await expect(page.getByRole('status').first()).toHaveText('Signed out locally. Microsoft SSO session may remain active.')
  await expect(page.getByRole('button', { name: 'Sign in with Microsoft' })).toBeEnabled()
  await assertLocalMsalSignedOut(page)
  await page.reload()
  await expect(page.getByRole('button', { name: 'Sign in with Microsoft' })).toBeEnabled()
  await expect(page.getByText(/^Signed in:/)).toHaveCount(0)
  await assertLocalMsalSignedOut(page)
  await assertMemoryOnly(page, context)
  expect(audit.problems).toEqual([])
  console.log('C1 LIVE supervised assertion complete: local_msal_logout=PASS microsoft_provider_signout=INCOMPLETE sha=' + sha + ' browser=' + browser.version() + ' utc=' + new Date().toISOString())
})

test('LIVE: deliberately cancelled popup returns MSAL user_cancelled, not a generic failure', async ({ page, context }) => {
  test.skip(!live, 'C1 live cancellation is supervised')
  const audit = await observeContext(context)
  await page.goto('/')
  await assertBuild(page)
  // Instrument the real initialized MSAL instance; the LOGIN itself is
  // triggered by a real user-gesture Playwright click, not page.evaluate.
  await page.evaluate(async () => {
    const { initializeAuth } = await import('/src/entraAuth.ts')
    const auth = await initializeAuth()
    const original = auth.loginPopup.bind(auth)
    window.__c1CancelCode = 'not_observed'
    auth.loginPopup = async (...args) => {
      try { return await original(...args) }
      catch (error) {
        window.__c1CancelCode = String(error?.errorCode || 'missing_error_code')
        throw error
      }
    }
  })
  const [popup] = await Promise.all([
    page.waitForEvent('popup'),
    page.getByRole('button', { name: 'Sign in with Microsoft' }).click(),
  ])
  await requireMicrosoftPopupOrigin(popup)
  await page.pause() // operator deliberately closes/cancels actual Microsoft popup
  await expect.poll(() => page.evaluate(() => window.__c1CancelCode)).toBe('user_cancelled')
  await expect.poll(() => popup.isClosed()).toBe(true)
  await expect(page.getByRole('status').first()).toContainText('Microsoft sign-in failed')
  await expect(page.getByRole('button', { name: 'Sign in with Microsoft' })).toBeEnabled()
  await expect(page.getByText(/^Signed in:/)).toHaveCount(0)
  await assertLocalMsalSignedOut(page)
  await assertMemoryOnly(page, context)
  expect(audit.problems).toEqual([])
})
