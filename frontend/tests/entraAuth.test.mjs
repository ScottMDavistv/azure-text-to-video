// L-6 (Issue #62): mocked MSAL popup sign-in, consent/token, sign-out,
// error cleanup and memory-only cache. No network, no live Entra.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import {
  LOCALHOST_REDIRECT, buildMsalConfig, createEntraAuth, getRedirectUri,
} from '../src/entraAuthCore.ts'

const CLIENT_ID = '00000000-0000-4000-8000-000000000001'
const TENANT_ID = '773e9051-c546-4b75-9432-90f4f8242660'
const SCOPES = ['api://a5a09b74-aead-455f-9de7-9b844c8a8465/access_as_user']
const ACCOUNT = Object.freeze({ homeAccountId: 'acct-1', username: 'dev@localhost', name: 'Dev' })

class InteractionRequired extends Error {}

/** Minimal MSAL stand-in; any unmocked method (e.g. *Redirect) is absent. */
function mockMsal(overrides = {}) {
  const calls = []
  let active = null
  let accounts = overrides.accounts ?? []
  const record = (name, value) => { calls.push([name, value]); return value }
  const client = {
    calls,
    initialize: async () => { record('initialize'); if (overrides.initialize) await overrides.initialize() },
    getActiveAccount: () => active,
    getAllAccounts: () => accounts,
    setActiveAccount: (account) => { record('setActiveAccount', account); active = account },
    loginPopup: async (request) => {
      record('loginPopup', request)
      if (overrides.loginPopup) return overrides.loginPopup(request)
      accounts = [ACCOUNT]
      return { account: ACCOUNT, accessToken: 'login-token' }
    },
    logoutPopup: async (request) => {
      record('logoutPopup', request)
      if (overrides.logoutPopup) return overrides.logoutPopup(request)
      accounts = []
    },
    clearCache: async (request) => {
      record('clearCache', request)
      if (overrides.clearCache) return overrides.clearCache(request)
      accounts = []
    },
    acquireTokenSilent: async (request) => {
      record('acquireTokenSilent', request)
      if (overrides.acquireTokenSilent) return overrides.acquireTokenSilent(request)
      return { accessToken: 'silent-token', account: request.account }
    },
    acquireTokenPopup: async (request) => {
      record('acquireTokenPopup', request)
      if (overrides.acquireTokenPopup) return overrides.acquireTokenPopup(request)
      return { accessToken: 'consented-token', account: request.account }
    },
  }
  return client
}

function setup({ clientId = CLIENT_ID, origin = LOCALHOST_REDIRECT, clients = [mockMsal()] } = {}) {
  const configs = []
  const queue = [...clients]
  const auth = createEntraAuth({
    clientId,
    tenantId: TENANT_ID,
    apiScopes: SCOPES,
    getOrigin: () => origin,
    createClient: (config) => {
      configs.push(config)
      const next = queue.shift()
      if (!next) throw new Error('unexpected extra MSAL client')
      return next
    },
    isInteractionRequired: (error) => error instanceof InteractionRequired,
  })
  return { auth, configs }
}

const names = (client) => client.calls.map(([name]) => name)

test('config is popup-ready, localhost-only and memory-only', () => {
  for (const origin of [LOCALHOST_REDIRECT, 'http://127.0.0.1:5173']) {
    const config = buildMsalConfig(CLIENT_ID, TENANT_ID, origin)
    assert.deepEqual(config.cache, { cacheLocation: 'memoryStorage', storeAuthStateInCookie: false })
    assert.equal(config.auth.redirectUri, LOCALHOST_REDIRECT)
    assert.equal(config.auth.postLogoutRedirectUri, LOCALHOST_REDIRECT)
    assert.equal(config.auth.authority, `https://login.microsoftonline.com/${TENANT_ID}`)
    assert.equal(config.auth.navigateToLoginRequestUrl, false)
  }
})

test('unregistered origins are rejected before any MSAL client exists', async () => {
  for (const origin of [
    'https://localhost:5173', 'http://localhost:5174', 'http://evil.example',
    'https://turbo-halibut-g4pq966pxp5vf9wxw-5173.app.github.dev', 'null', '',
  ]) {
    assert.throws(() => getRedirectUri(origin), /Unregistered frontend origin/)
    const { auth, configs } = setup({ origin })
    await assert.rejects(auth.initializeAuth(), /Unregistered frontend origin/)
    await assert.rejects(auth.signIn(), /Unregistered frontend origin/)
    assert.equal(configs.length, 0)
  }
})

test('missing or malformed client ID fails closed without MSAL', async () => {
  for (const clientId of ['', '   ', 'not-a-guid', `${CLIENT_ID}x`, `{${CLIENT_ID}}`]) {
    const { auth, configs } = setup({ clientId })
    assert.equal(auth.isAuthConfigured(), false)
    await assert.rejects(auth.initializeAuth(), /not configured/)
    await assert.rejects(auth.acquireApiToken(), /not configured/)
    assert.equal(configs.length, 0)
  }
})

test('the created client receives the memory-only config', async () => {
  const client = mockMsal()
  const { auth, configs } = setup({ clients: [client] })
  assert.equal(await auth.initializeAuth(), client)
  assert.equal(await auth.initializeAuth(), client)  // single shared instance
  assert.equal(configs.length, 1)
  assert.equal(configs[0].cache.cacheLocation, 'memoryStorage')
  assert.equal(configs[0].auth.clientId, CLIENT_ID)
  assert.deepEqual(names(client), ['initialize'])
})

test('popup login marks the returned account active', async () => {
  const client = mockMsal()
  const { auth } = setup({ clients: [client] })
  assert.equal(await auth.getSignedInAccount(), null)
  await auth.signIn()
  assert.deepEqual(client.calls.find(([n]) => n === 'loginPopup')[1], { scopes: SCOPES })
  assert.equal(await auth.getSignedInAccount(), ACCOUNT)
  assert.ok(!names(client).some((n) => /Redirect/.test(n)))
})

test('popup login failure leaves no account and allows a clean retry', async () => {
  let attempts = 0
  const client = mockMsal({
    loginPopup: async () => {
      attempts += 1
      if (attempts === 1) throw Object.assign(new Error('user_cancelled'), { errorCode: 'user_cancelled' })
      return { account: ACCOUNT }
    },
  })
  const { auth } = setup({ clients: [client] })
  await assert.rejects(auth.signIn(), /user_cancelled/)
  assert.equal(await auth.getSignedInAccount(), null)
  assert.ok(!names(client).includes('setActiveAccount'))
  await auth.signIn()
  assert.equal(await auth.getSignedInAccount(), ACCOUNT)
})

test('popup login result without an account does not mark anyone active', async () => {
  const client = mockMsal({ loginPopup: async () => ({ account: null }) })
  const { auth } = setup({ clients: [client] })
  await auth.signIn()
  assert.equal(await auth.getSignedInAccount(), null)
  await assert.rejects(auth.acquireApiToken(), /Sign in with Microsoft/)
  assert.ok(!names(client).includes('acquireTokenSilent'))
})

test('failed initialization is not cached; the next attempt retries', async () => {
  const broken = mockMsal({ initialize: async () => { throw new Error('init failed') } })
  const healthy = mockMsal()
  const { auth, configs } = setup({ clients: [broken, healthy] })
  await assert.rejects(auth.initializeAuth(), /init failed/)
  // The rejected promise was not cached: the next call builds a new client.
  assert.equal(await auth.initializeAuth(), healthy)
  await auth.signIn()
  assert.equal(await auth.getSignedInAccount(), ACCOUNT)
  assert.equal(configs.length, 2)
})

test('client construction failure is surfaced and retried', async () => {
  let built = 0
  const healthy = mockMsal()
  const auth = createEntraAuth({
    clientId: CLIENT_ID, tenantId: TENANT_ID, apiScopes: SCOPES,
    getOrigin: () => LOCALHOST_REDIRECT,
    createClient: () => { built += 1; if (built === 1) throw new Error('ctor failed'); return healthy },
    isInteractionRequired: () => false,
  })
  await assert.rejects(auth.initializeAuth(), /ctor failed/)
  assert.equal(await auth.initializeAuth(), healthy)
})

test('silent token acquisition never opens a popup', async () => {
  const client = mockMsal({ accounts: [ACCOUNT] })
  const { auth } = setup({ clients: [client] })
  const result = await auth.acquireApiToken()
  assert.equal(result.accessToken, 'silent-token')
  assert.deepEqual(client.calls.find(([n]) => n === 'acquireTokenSilent')[1], { account: ACCOUNT, scopes: SCOPES })
  assert.ok(!names(client).includes('acquireTokenPopup'))
})

test('interaction-required error drives popup consent and returns its token', async () => {
  const client = mockMsal({
    accounts: [ACCOUNT],
    acquireTokenSilent: async () => { throw new InteractionRequired('consent_required') },
  })
  const { auth } = setup({ clients: [client] })
  const result = await auth.acquireApiToken()
  assert.equal(result.accessToken, 'consented-token')
  assert.deepEqual(client.calls.find(([n]) => n === 'acquireTokenPopup')[1], { account: ACCOUNT, scopes: SCOPES })
  assert.ok(!names(client).some((n) => /Redirect/.test(n)))
})

test('declined or blocked consent popup propagates and does not retry', async () => {
  const client = mockMsal({
    accounts: [ACCOUNT],
    acquireTokenSilent: async () => { throw new InteractionRequired('interaction_required') },
    acquireTokenPopup: async () => { throw new Error('popup_window_error') },
  })
  const { auth } = setup({ clients: [client] })
  await assert.rejects(auth.acquireApiToken(), /popup_window_error/)
  assert.equal(names(client).filter((n) => n === 'acquireTokenPopup').length, 1)
})

test('non-interaction silent errors are rethrown without a popup', async () => {
  const client = mockMsal({
    accounts: [ACCOUNT],
    acquireTokenSilent: async () => { throw new Error('network_error') },
  })
  const { auth } = setup({ clients: [client] })
  await assert.rejects(auth.acquireApiToken(), /network_error/)
  assert.ok(!names(client).includes('acquireTokenPopup'))
})

test('token request without a signed-in account fails before MSAL token calls', async () => {
  const client = mockMsal()
  const { auth } = setup({ clients: [client] })
  await assert.rejects(auth.acquireApiToken(), /Sign in with Microsoft/)
  assert.ok(!names(client).some((n) => n.startsWith('acquireToken')))
})

test('popup logout targets the account and clears the active account', async () => {
  const client = mockMsal()
  const { auth } = setup({ clients: [client] })
  await auth.signIn()
  await auth.signOut()
  assert.deepEqual(client.calls.find(([n]) => n === 'logoutPopup')[1], { account: ACCOUNT })
  assert.equal(await auth.getSignedInAccount(), null)
  assert.ok(!names(client).includes('clearCache'))
})

test('failed logout popup still clears local account and in-memory cache', async () => {
  const client = mockMsal({ logoutPopup: async () => { throw new Error('popup_blocked') } })
  const { auth } = setup({ clients: [client] })
  await auth.signIn()
  await assert.rejects(auth.signOut(), /popup_blocked/)
  assert.deepEqual(client.calls.find(([n]) => n === 'clearCache')[1], { account: ACCOUNT })
  assert.equal(await auth.getSignedInAccount(), null)
  await assert.rejects(auth.acquireApiToken(), /Sign in with Microsoft/)
})

test('cache clearing failure during logout keeps the original error', async () => {
  const client = mockMsal({
    logoutPopup: async () => { throw new Error('logout_failed') },
    clearCache: async () => { throw new Error('clear_failed') },
  })
  const { auth } = setup({ clients: [client] })
  await auth.signIn()
  await assert.rejects(auth.signOut(), /logout_failed/)
  assert.equal(client.getActiveAccount(), null)
})

test('the real wiring is popup-only and never names persistent browser storage', async () => {
  for (const file of ['../src/entraAuth.ts', '../src/entraAuthCore.ts']) {
    const source = await readFile(new URL(file, import.meta.url), 'utf8')
    assert.doesNotMatch(source, /(login|logout|acquireToken)Redirect|handleRedirectPromise/)
    assert.doesNotMatch(source, /cacheLocation:\s*'(sessionStorage|localStorage)'/)
    assert.doesNotMatch(source, /\b(window\.)?(sessionStorage|localStorage)\s*\./)
    assert.doesNotMatch(source, /app\.github\.dev/)
  }
})
