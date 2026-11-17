import { test, expect } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const live = process.env.C1_LIVE === '1'
const unconfigured = process.env.C1_UNCONFIGURED === '1'
const sha = execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim()
const tenant = '773e9051-c546-4b75-9432-90f4f8242660'
const audience = 'a5a09b74-aead-455f-9de7-9b844c8a8465'
const scope = 'api://' + audience + '/access_as_user'
const authSource = readFileSync(fileURLToPath(new URL('../src/entraAuth.ts', import.meta.url)), 'utf8')
const appSource = readFileSync(fileURLToPath(new URL('../src/App.tsx', import.meta.url)), 'utf8')
const allowedHosts = new Set([
  'localhost', '127.0.0.1', 'login.microsoftonline.com',
  'login.live.com', 'aadcdn.msftauth.net', 'aadcdn.msauth.net',
  'logincdn.msftauth.net', 'secure.aadcdn.microsoftonline-p.com',
])

const allowedLocalOrigins = new Set(['http://localhost:5173', 'http://127.0.0.1:5173'])
const allowedHmrOrigins = new Set(['ws://localhost:5173', 'ws://127.0.0.1:5173'])
function allowedRequest(url) {
  return allowedLocalOrigins.has(url.origin) || (url.protocol === 'https:' && allowedHosts.has(url.hostname) && !['localhost', '127.0.0.1'].includes(url.hostname))
}

function assertSourceInvariants() {
  // Offline structural checks are intentionally narrow; a real login remains required.
  expect(authSource).toMatch(/cacheLocation:\s*['"]memoryStorage['"]/)
  expect(authSource).toMatch(/storeAuthStateInCookie:\s*false/)
  expect(authSource).toMatch(/loginPopup\s*\(/)
  expect(authSource).toMatch(/logoutPopup\s*\(/)
  expect(authSource).toMatch(/acquireTokenPopup\s*\(/)
  expect(authSource).not.toMatch(/acquireTokenRedirect\s*\(|loginRedirect\s*\(|logoutRedirect\s*\(/)
  expect(authSource).toMatch(/redirectUri:\s*getRedirectUri\s*\(\s*window\.location\.origin\s*\)/)
  expect(authSource).not.toMatch(/\bauthority:\s*[^\n]*\/common\b/i)
  expect(authSource).not.toMatch(/\bauthority:\s*[^\n]*\/organizations\b/i)
  expect(appSource).not.toMatch(/\bfetch\s*\(\s*['"]https?:\/\//)
  // No explicit browser storage reads/writes in auth application code.
  for (const source of [authSource, appSource]) {
    expect(source).not.toMatch(/\b(?:localStorage|sessionStorage)\s*(?:\.|\[)/)
  }
}

async function observeContext(context) {
  const problems = []
  const requestHosts = new Set()
  const credentialPattern = /Bearer\s+[\w.-]{24,}|eyJ[A-Za-z0-9_-]{20,}\.|client_secret\s*[:=]/i
  const listen = page => {
    page.on('console', message => {
      if (credentialPattern.test(message.text())) problems.push('Credential-like browser console output')
    })
    page.on('pageerror', error => {
      if (credentialPattern.test(error.message)) problems.push('Credential-like browser page error')
    })
    page.on('websocket', socket => {
      const url = new URL(socket.url())
      if (!allowedHmrOrigins.has(url.origin)) problems.push('Unauthorized WebSocket host ' + url.hostname)
    })
  }
  for (const page of context.pages()) listen(page)
  context.on('page', listen)
  context.on('request', request => {
    const url = new URL(request.url())
    requestHosts.add(url.hostname)
    if (!allowedRequest(url)) problems.push('Unexpected request host ' + url.hostname)
    if (!allowedRequest(url) && request.headers().authorization) problems.push('Authorization header to unauthorized host ' + url.hostname)
  })
  await context.routeWebSocket(/.*/, async route => {
    const url = new URL(route.url())
    if (allowedHmrOrigins.has(url.origin)) {
      await route.connectToServer()
    } else {
      problems.push('Blocked unauthorized WebSocket host ' + url.hostname)
      route.close()
    }
  })
  await context.route('**/*', async route => {
    const url = new URL(route.request().url())
    const local = allowedLocalOrigins.has(url.origin)
    if ((!live && !local) || (live && !allowedRequest(url))) {
      problems.push('Blocked unauthorized HTTP origin ' + url.hostname)
      await route.abort('blockedbyclient')
    } else {
      await route.continue()
    }
  })
  return { problems, requestHosts }
}

async function assertBuild(page) {
  const served = await page.evaluate(async () => {
    const { c1BuildSha, c1BuildDirty } = await import('/src/c1BuildInfo.ts')
    return { sha: c1BuildSha, dirty: c1BuildDirty }
  })
  expect(served.sha).toBe(sha)
  expect(served.dirty).toBe(process.env.C1_MUTATION_MODE === '1')
}

async function assertMemoryOnly(page, context) {
  const result = await page.evaluate(async () => {
    const local = Object.entries(localStorage)
    const session = Object.entries(sessionStorage)
    const jwt = /eyJ[A-Za-z0-9_-]{20,}\./
    const forbidden = /token|secret|bearer/i
    const allowedSessionKeys = new Set(['msal.interaction.status', 'msal.interaction.inProgress'])
    return {
      localEmpty: local.length === 0,
      sessionSafe: session.every(([key, value]) => allowedSessionKeys.has(key) && value.length < 128 && !forbidden.test(value) && !jwt.test(value)),
      domSafe: !jwt.test(document.body.innerText),
      indexedDbEmpty: (await indexedDB.databases()).length === 0,
    }
  })
  const localCookies = await context.cookies(['http://localhost:5173', 'http://127.0.0.1:5173'])
  expect(localCookies).toEqual([])
  expect(result).toEqual({ localEmpty: true, sessionSafe: true, domSafe: true, indexedDbEmpty: true })
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
  await expect(page.getByRole('button', { name: 'Sign in with Microsoft' })).toBeEnabled({ timeout: 15000 })
  await expect(page.getByText(/^Signed in:/)).toHaveCount(0)
  await assertMemoryOnly(page, context)
  expect(audit.problems).toEqual([])
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

test('LIVE: popup login, validated claims, silent acquisition and confirmed cache-clearing logout', async ({ page, context, browser }) => {
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
  await page.getByRole('button', { name: 'Check API permission' }).click()
  await expect(page.getByRole('status').first()).toContainText('Delegated API permission acquired')
  expect(popups.length).toBe(beforeRepeat)
  await assertMemoryOnly(page, context)
  await page.getByRole('button', { name: 'Sign out' }).click()
  await page.pause() // operator completes Microsoft logout popup, then resumes
  await expect(page.getByRole('button', { name: 'Sign in with Microsoft' })).toBeVisible()
  const signedOut = await page.evaluate(async () => {
    const { initializeAuth, getSignedInAccount, acquireApiToken } = await import('/src/entraAuth.ts')
    const client = await initializeAuth()
    let rejectsToken = false
    try { await acquireApiToken() } catch { rejectsToken = true }
    return { activeEmpty: (await getSignedInAccount()) === null, allEmpty: client.getAllAccounts().length === 0, rejectsToken }
  })
  expect(signedOut).toEqual({ activeEmpty: true, allEmpty: true, rejectsToken: true })
  await page.reload()
  await expect(page.getByText(/^Signed in:/)).toHaveCount(0)
  await assertMemoryOnly(page, context)
  expect(audit.problems).toEqual([])
  console.log('C1 LIVE supervised assertion complete: sha=' + sha + ' browser=' + browser.version() + ' utc=' + new Date().toISOString())
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
  let popupObserved = false
  context.on('page', popup => { if (popup !== page) popupObserved = true })
  await page.getByRole('button', { name: 'Sign in with Microsoft' }).click()
  await page.pause() // operator deliberately closes/cancels actual Microsoft popup
  await expect.poll(() => page.evaluate(() => window.__c1CancelCode)).toBe('user_cancelled')
  expect(popupObserved).toBe(true)
  await expect(page.getByText(/^Signed in:/)).toHaveCount(0)
  await assertMemoryOnly(page, context)
  expect(audit.problems).toEqual([])
})
