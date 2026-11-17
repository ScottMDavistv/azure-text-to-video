import { test, expect } from '@playwright/test'

// This suite exercises real React UI + real entraAuth.ts, with the initialized
// MSAL client stubbed in the browser. No Microsoft calls, tokens, identities,
// paid providers, cookies or persisted authenticated state are used.
// It is NOT a substitute for a live Entra registration/MFA/consent test.
const scope = 'api://a5a09b74-aead-455f-9de7-9b844c8a8465/access_as_user'
test.skip(process.env.C1_LIVE === '1' || process.env.C1_UNCONFIGURED === '1', 'Tier 1 synthetic tests run only in offline mode')

async function offline(page, context) {
  const unexpected = []
  await context.routeWebSocket(/.*/, async route => {
    const url = new URL(route.url())
    if (['ws://localhost:5173', 'ws://127.0.0.1:5173'].includes(url.origin)) {
      await route.connectToServer()
    } else {
      unexpected.push('blocked WebSocket outside local Vite')
      route.close()
    }
  })
  await context.route('**/*', async route => {
    const url = new URL(route.request().url())
    if (['http://localhost:5173', 'http://127.0.0.1:5173'].includes(url.origin)) {
      await route.continue()
    } else {
      unexpected.push('blocked HTTP outside local Vite')
      await route.abort('blockedbyclient')
    }
  })
  await page.goto('/')
  await page.evaluate(async () => {
    const { initializeAuth } = await import('/src/entraAuth.ts')
    await initializeAuth()
  })
  return unexpected
}

async function installSyntheticAccount(page, { deniedLogin = false, cancelledLogin = false, cancelledLogout = false, deniedApi = false } = {}) {
  await page.evaluate(async flags => {
    const { initializeAuth } = await import('/src/entraAuth.ts')
    const auth = await initializeAuth()
    const account = { homeAccountId: 'synthetic-only', username: 'tier1@example.invalid', name: 'Tier 1 Synthetic Account' }
    let active = null
    window.__tier1 = { loginScopes: [], tokenScopes: [], popupScopes: [], logoutCalls: 0, redirectCalls: 0, cleared: [], activeId: () => active?.homeAccountId ?? null }
    auth.loginPopup = async ({ scopes }) => {
      window.__tier1.loginScopes = scopes
      if (flags.deniedLogin || flags.cancelledLogin) throw Object.assign(new Error('Synthetic auth rejected'), { errorCode: flags.cancelledLogin ? 'user_cancelled' : 'access_denied' })
      return { account }
    }
    auth.setActiveAccount = value => { active = value }
    auth.getActiveAccount = () => active
    auth.getAllAccounts = () => active ? [active] : []
    auth.clearCache = async ({ account: selected } = {}) => { window.__tier1.cleared.push(selected?.homeAccountId ?? null) }
    auth.acquireTokenSilent = async ({ scopes }) => {
      window.__tier1.tokenScopes = scopes
      if (flags.deniedApi) throw new Error('Synthetic delegated permission denied')
      return { accessToken: 'synthetic-not-a-token' }
    }
    auth.acquireTokenPopup = async ({ scopes }) => {
      window.__tier1.popupScopes = scopes
      throw Object.assign(new Error('Synthetic consent denied'), { errorCode: 'access_denied' })
    }
    auth.acquireTokenRedirect = async () => { window.__tier1.redirectCalls++ }
    auth.logoutPopup = async ({ account: selected }) => {
      window.__tier1.logoutCalls++
      if (!selected || selected.homeAccountId !== account.homeAccountId) throw new Error('Incorrect account selected')
      if (flags.cancelledLogout) throw Object.assign(new Error('Synthetic user cancelled logout'), { errorCode: 'user_cancelled' })
      active = null
    }
  }, { deniedLogin, cancelledLogin, cancelledLogout, deniedApi })
}

async function assertNoCredentialMaterial(page, context) {
  const clean = await page.evaluate(async () => {
    const jwt = /eyJ[A-Za-z0-9_-]{20,}\./
    return {
      local: Object.keys(localStorage).length === 0,
      session: Object.entries(sessionStorage).every(([key, val]) =>
        key.startsWith('msal.interaction.') && !/token|secret|bearer|eyJ/i.test(val)),
      dom: !jwt.test(document.body.innerText),
      indexedDb: (await indexedDB.databases()).length === 0,
    }
  })
  expect(clean).toEqual({ local: true, session: true, dom: true, indexedDb: true })
  expect(await context.cookies(['http://localhost:5173'])).toEqual([])
}

test('Tier1 synthetic sign-in, delegated scope, silent token and logout UI', async ({ page, context }) => {
  const unexpected = await offline(page, context)
  await installSyntheticAccount(page)
  await page.getByRole('button', { name: 'Sign in with Microsoft' }).click()
  await expect(page.getByText('Signed in: Tier 1 Synthetic Account')).toBeVisible()
  await expect(page.getByRole('status').first()).toContainText('backend authorization is not verified')
  await page.getByRole('button', { name: 'Check API permission' }).click()
  await expect(page.getByRole('status').first()).toContainText('Delegated API permission acquired')
  const scopes = await page.evaluate(() => ({
    login: window.__tier1.loginScopes,
    token: window.__tier1.tokenScopes,
    popup: window.__tier1.popupScopes,
    redirects: window.__tier1.redirectCalls,
  }))
  expect(scopes).toEqual({ login: [scope], token: [scope], popup: [], redirects: 0 })
  await assertNoCredentialMaterial(page, context)
  await page.getByRole('button', { name: 'Sign out' }).click()
  await expect(page.getByRole('button', { name: 'Sign in with Microsoft' })).toBeVisible()
  await expect(page.getByRole('status').first()).toContainText('Signed out')
  expect(await page.evaluate(() => window.__tier1.logoutCalls)).toBe(1)
  await assertNoCredentialMaterial(page, context)
  await page.reload()
  await expect(page.getByRole('button', { name: 'Sign in with Microsoft' })).toBeVisible()
  expect(unexpected).toEqual([])
})

test('Tier1 denied login never reports authenticated state', async ({ page, context }) => {
  const unexpected = await offline(page, context)
  await installSyntheticAccount(page, { deniedLogin: true })
  await page.getByRole('button', { name: 'Sign in with Microsoft' }).click()
  await expect(page.getByRole('status').first()).toContainText('Microsoft sign-in failed')
  await expect(page.getByText(/^Signed in:/)).toHaveCount(0)
  expect(await page.evaluate(() => window.__tier1.activeId())).toBeNull()
  await assertNoCredentialMaterial(page, context)
  expect(unexpected).toEqual([])
})

test('Tier1 delegated API permission failure is never reported as granted', async ({ page, context }) => {
  const unexpected = await offline(page, context)
  await installSyntheticAccount(page, { deniedApi: true })
  await page.getByRole('button', { name: 'Sign in with Microsoft' }).click()
  await expect(page.getByText(/^Signed in:/)).toBeVisible()
  await page.getByRole('button', { name: 'Check API permission' }).click()
  await expect(page.getByRole('status').first()).toContainText('API permission request failed')
  await expect(page.getByRole('status').first()).not.toContainText('Delegated API permission acquired')
  await assertNoCredentialMaterial(page, context)
  expect(unexpected).toEqual([])
})


test('Tier1 cancelled login never creates an account', async ({ page, context }) => {
  const unexpected = await offline(page, context)
  await installSyntheticAccount(page, { cancelledLogin: true })
  await page.getByRole('button', { name: 'Sign in with Microsoft' }).click()
  await expect(page.getByRole('status').first()).toContainText('Microsoft sign-in failed')
  await expect(page.getByText(/^Signed in:/)).toHaveCount(0)
  expect(await page.evaluate(() => window.__tier1.activeId())).toBeNull()
  await assertNoCredentialMaterial(page, context)
  expect(unexpected).toEqual([])
})

test('Tier1 interaction-required fallback and denied popup consent fails closed', async ({ page, context }) => {
  const unexpected = await offline(page, context)
  await installSyntheticAccount(page)
  await page.getByRole('button', { name: 'Sign in with Microsoft' }).click()
  await expect(page.getByText(/^Signed in:/)).toBeVisible()
  const prepared = await page.evaluate(async () => {
    const { initializeAuth } = await import('/src/entraAuth.ts')
    // Use the exact optimized module imported by the running auth implementation.
    const transformed = await (await fetch('/src/entraAuth.ts')).text()
    const msalUrl = transformed.match(/from\s*["']([^"']*azure_msal-browser[^"']*)["']/)?.[1]
    if (!msalUrl || !msalUrl.startsWith('/node_modules/.vite/deps/')) throw new Error('MSAL module identity unavailable')
    const { InteractionRequiredAuthError } = await import(/* @vite-ignore */ msalUrl)
    const auth = await initializeAuth()
    auth.acquireTokenSilent = async () => { throw new InteractionRequiredAuthError('interaction_required') }
    return true
  })
  expect(prepared).toBe(true)
  await page.getByRole('button', { name: 'Check API permission' }).click()
  await expect(page.getByRole('status').first()).toContainText('API permission request failed')
  const observed = await page.evaluate(() => ({ popupScopes: window.__tier1.popupScopes, redirects: window.__tier1.redirectCalls }))
  expect(observed).toEqual({ popupScopes: [scope], redirects: 0 })
  await assertNoCredentialMaterial(page, context)
  expect(unexpected).toEqual([])
})

test('Tier1 cancelled logout reports failure and still clears local session', async ({ page, context }) => {
  const unexpected = await offline(page, context)
  await installSyntheticAccount(page, { cancelledLogout: true })
  await page.getByRole('button', { name: 'Sign in with Microsoft' }).click()
  await expect(page.getByText(/^Signed in:/)).toBeVisible()
  await page.getByRole('button', { name: 'Sign out' }).click()
  await expect(page.getByRole('status').first()).toContainText('Microsoft sign-out failed')
  await expect(page.getByText(/^Signed in:/)).toHaveCount(0)
  expect(await page.evaluate(() => ({ logoutCalls: window.__tier1.logoutCalls, active: window.__tier1.activeId(), cleared: window.__tier1.cleared }))).toEqual({ logoutCalls: 1, active: null, cleared: ['synthetic-only'] })
  await assertNoCredentialMaterial(page, context)
  expect(unexpected).toEqual([])
})
