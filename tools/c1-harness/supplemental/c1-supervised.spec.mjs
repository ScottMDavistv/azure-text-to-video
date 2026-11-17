// C1 harness supplemental SUPERVISED tests: consent denial and cancelled logout.
// These are ADDITIONS for C1 matrix rows the approved suite documents as
// "still separately test". The approved frontend/e2e/entra-c1.spec.mjs is not
// modified. Helpers below replicate the approved suite's LIVE-mode egress,
// storage and exact-head checks (they cannot be imported without registering
// the approved tests a second time) and must be reviewed for equivalence.
//
// Evidence: only `C1 RESULT` / `C1 OBSERVATION` lines with allowlisted,
// pre-sanitized tokens are printed. Never print URLs, claims, tokens, UPNs,
// error messages or storage contents.
import { test, expect } from '@playwright/test'
import { execFileSync } from 'node:child_process'

const which = process.env.C1_SUPERVISED_CASE
const frontend = process.env.C1_WORKTREE_FRONTEND
const sha = execFileSync('git', ['-C', frontend, 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim()

// --- replicated from approved entra-c1.spec.mjs (LIVE branch) ---------------
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

async function observeContext(context) {
  const problems = []
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
    if (!allowedRequest(url)) {
      problems.push('Blocked unauthorized HTTP origin ' + url.hostname)
      await route.abort('blockedbyclient')
    } else {
      await route.continue()
    }
  })
  return { problems }
}

async function assertBuild(page) {
  const served = await page.evaluate(async () => {
    const { c1BuildSha, c1BuildDirty } = await import('/src/c1BuildInfo.ts')
    return { sha: c1BuildSha, dirty: c1BuildDirty }
  })
  expect(served).toEqual({ sha, dirty: false })
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
// --- end replicated helpers ---------------------------------------------------

/** Wrap a real MSAL instance method; export ONLY sanitized outcome tokens. */
async function instrument(page, method, slot) {
  await page.evaluate(async ({ method, slot }) => {
    const { initializeAuth } = await import('/src/entraAuth.ts')
    const auth = await initializeAuth()
    const original = auth[method].bind(auth)
    window[slot] = { state: 'not_observed', code: 'none', aadsts: 'none' }
    auth[method] = async (...args) => {
      try {
        const result = await original(...args)
        window[slot] = { state: 'succeeded', code: 'none', aadsts: 'none' }
        return result
      } catch (error) {
        const raw = String(error?.errorCode || '')
        const code = /^[a-z0-9_]{1,64}$/.test(raw) ? raw : (raw ? 'unrecognized' : 'missing_error_code')
        const match = (String(error?.errorMessage || '') + ' ' + String(error?.subError || '')).match(/AADSTS\d{5,6}/)
        window[slot] = { state: 'failed', code, aadsts: match ? match[0] : 'none' }
        throw error
      }
    }
  }, { method, slot })
}

function clean(outcome) {
  const ok = (v, re) => (typeof v === 'string' && re.test(v) ? v : 'unrecognized')
  return {
    state: ok(outcome?.state, /^(not_observed|succeeded|failed)$/),
    code: ok(outcome?.code, /^[a-z0-9_]{1,64}$/),
    aadsts: ok(outcome?.aadsts, /^(none|AADSTS\d{5,6})$/),
  }
}

function report(name, outcome) {
  console.log('C1 RESULT case=' + name + ' state=' + outcome.state + ' code=' + outcome.code + ' aadsts=' + outcome.aadsts)
}

async function msalState(page) {
  return page.evaluate(async () => {
    const { initializeAuth } = await import('/src/entraAuth.ts')
    const auth = await initializeAuth()
    return { accounts: auth.getAllAccounts().length, active: Boolean(auth.getActiveAccount()) }
  })
}

test('SUPERVISED consent denial: declined consent fails closed with sanitized MSAL code', async ({ page, context }) => {
  test.skip(which !== 'consent-denial', 'Selected by C1_SUPERVISED_CASE')
  const audit = await observeContext(context)
  await page.goto('/')
  await assertBuild(page)
  await instrument(page, 'loginPopup', '__c1ConsentOutcome')
  await page.getByRole('button', { name: 'Sign in with Microsoft' }).click()
  await page.pause() // operator: sign in, then choose Cancel on the CONSENT prompt; resume
  const outcome = clean(await page.evaluate(() => window.__c1ConsentOutcome))
  report('consent-denial', outcome)
  test.skip(outcome.state === 'succeeded', 'INCOMPLETE: sign-in succeeded; no consent prompt was declined')
  test.skip(outcome.state === 'not_observed', 'INCOMPLETE: no sign-in outcome observed before resume')
  test.skip(outcome.code === 'user_cancelled', 'INCOMPLETE: popup was closed instead of declining consent')
  expect(outcome.code).toBe('access_denied')
  expect(['AADSTS65004', 'none']).toContain(outcome.aadsts)
  await expect(page.getByText(/^Signed in:/)).toHaveCount(0)
  await expect(page.getByRole('status').first()).toContainText('sign-in failed')
  expect(await msalState(page)).toEqual({ accounts: 0, active: false })
  await assertMemoryOnly(page, context)
  expect(audit.problems).toEqual([])
})

// MSAL 4.x logoutPopup clears the LOCAL cache first and swallows popup-monitor
// errors ("server logout is best effort"), so a cancelled logout normally
// RESOLVES rather than rejecting with user_cancelled. Cancellation is therefore
// detected from whether the sign-out popup ever returned to the app origin.
test('SUPERVISED cancelled logout: local sign-out is fail-closed and consistent when the Microsoft popup is cancelled', async ({ page, context }) => {
  test.skip(which !== 'cancelled-logout', 'Selected by C1_SUPERVISED_CASE')
  const audit = await observeContext(context)
  await page.goto('/')
  await assertBuild(page)
  await page.getByRole('button', { name: 'Sign in with Microsoft' }).click()
  await page.pause() // operator: complete normal sign-in (and consent if asked); resume
  const signedIn = await page.getByText(/^Signed in:/).count()
  test.skip(signedIn === 0, 'INCOMPLETE: sign-in prerequisite was not completed')
  const popup = { opened: false, returnedToApp: false }
  context.on('page', p => {
    if (p === page) return
    popup.opened = true
    p.on('framenavigated', frame => {
      try {
        if (frame === p.mainFrame() && allowedLocalOrigins.has(new URL(frame.url()).origin)) popup.returnedToApp = true
      } catch { /* about:blank */ }
    })
  })
  await instrument(page, 'logoutPopup', '__c1LogoutOutcome')
  await page.getByRole('button', { name: 'Sign out' }).click()
  await page.pause() // operator: CLOSE the Microsoft sign-out popup without completing it; resume
  const outcome = clean(await page.evaluate(() => window.__c1LogoutOutcome))
  report('cancelled-logout', outcome)
  test.skip(!popup.opened, 'INCOMPLETE: no Microsoft sign-out popup was observed')
  test.skip(popup.returnedToApp, 'INCOMPLETE: sign-out completed at Microsoft; it was not cancelled')
  test.skip(outcome.state === 'not_observed', 'INCOMPLETE: no sign-out outcome observed before resume')
  // Resolved (MSAL best-effort server logout) or user_cancelled are the only acceptable outcomes.
  expect(outcome.state === 'succeeded' || outcome.code === 'user_cancelled').toBe(true)
  await expect(page.getByRole('button', { name: /^(Sign out|Sign in with Microsoft)$/ })).toBeEnabled({ timeout: 15000 })
  const status = await page.getByRole('status').first().innerText()
  const ui = { signedIn: (await page.getByText(/^Signed in:/).count()) > 0, claimsSignedOut: /^Signed out\./.test(status) }
  const state = await page.evaluate(async () => {
    const { initializeAuth, acquireApiToken } = await import('/src/entraAuth.ts')
    const auth = await initializeAuth()
    let tokenRejected = false
    try { await acquireApiToken() } catch { tokenRejected = true }
    return { accounts: auth.getAllAccounts().length, active: Boolean(auth.getActiveAccount()), tokenRejected }
  })
  console.log('C1 OBSERVATION case=cancelled-logout ui_signed_in=' + ui.signedIn +
    ' msal_account_present=' + (state.accounts > 0) + ' failure_message_shown=' + /sign-out failed/.test(status) +
    ' server_signout_confirmed=false')
  // The UI must agree with MSAL: never "Signed out." while an account remains,
  // never "Signed in" after the local cache was cleared.
  expect(ui.claimsSignedOut && state.accounts > 0).toBe(false)
  expect(ui.signedIn).toBe(state.accounts > 0)
  if (state.accounts === 0) expect(state).toEqual({ accounts: 0, active: false, tokenRejected: true })
  else expect(status).toContain('sign-out failed')
  await assertMemoryOnly(page, context)
  await page.reload()
  await assertBuild(page)
  await expect(page.getByText(/^Signed in:/)).toHaveCount(0)
  const after = await page.evaluate(async () => {
    const { initializeAuth } = await import('/src/entraAuth.ts')
    const auth = await initializeAuth()
    return { accounts: auth.getAllAccounts().length, active: Boolean(auth.getActiveAccount()) }
  })
  expect(after).toEqual({ accounts: 0, active: false })
  await assertMemoryOnly(page, context)
  expect(audit.problems).toEqual([])
})
