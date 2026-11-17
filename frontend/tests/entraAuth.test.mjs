import test from 'node:test'
import assert from 'node:assert/strict'

const mockAppFactory = () => ({
  initialize: async () => {},
  getActiveAccount: () => ({ homeAccountId: 'acct-1', username: 'demo@localhost' }),
  getAllAccounts: () => [{ homeAccountId: 'acct-1', username: 'demo@localhost' }],
  loginPopup: async () => {},
  logoutPopup: async () => {},
  acquireTokenSilent: async () => ({ accessToken: 'token-123' }),
  acquireTokenPopup: async () => ({ accessToken: 'token-456' }),
  handleRedirectPromise: async () => {},
})

const realEnv = globalThis.__ENTRA_ENV__

function setEnv() {
  Object.defineProperty(globalThis, '__ENTRA_ENV__', {
    value: { VITE_ENTRA_SPA_CLIENT_ID: '6d5e9e96-8be6-445c-9176-3b67d020612c' },
    configurable: true,
    writable: true,
  })
}

const mod = await import('../src/entraAuth.ts')
const { initializeAuth, getSignedInAccount, signIn, acquireApiToken, signOut, setMsalFactory, resetMsalFactory } = mod

test('popup login, consent token and signout work in mocked MSAL flow', async () => {
  setEnv()
  setMsalFactory(() => mockAppFactory())
  try {
    const auth = await initializeAuth()
    assert.ok(auth)
    assert.deepEqual(await getSignedInAccount(), { homeAccountId: 'acct-1', username: 'demo@localhost' })
    await signIn()
    const result = await acquireApiToken()
    assert.equal(result?.accessToken, 'token-123')
    await signOut()
  } finally {
    resetMsalFactory()
    Object.defineProperty(globalThis, '__ENTRA_ENV__', { value: realEnv, configurable: true, writable: true })
  }
})

test('negative auth flows reset state and fail closed', async () => {
  setEnv()
  setMsalFactory(() => ({
    initialize: async () => {},
    getActiveAccount: () => null,
    getAllAccounts: () => [],
    loginPopup: async () => { throw new Error('popup-denied') },
    logoutPopup: async () => { throw new Error('logout-failed') },
    acquireTokenSilent: async () => { throw new Error('no silent token') },
    acquireTokenPopup: async () => { throw new Error('consent failed') },
    handleRedirectPromise: async () => {},
  }))
  try {
    await assert.rejects(signIn, /popup-denied/)
    await assert.rejects(signOut, /logout-failed/)
    await assert.rejects(async () => {
      await acquireApiToken()
    }, /Sign in with Microsoft before requesting API permission|no silent token/)
  } finally {
    resetMsalFactory()
    Object.defineProperty(globalThis, '__ENTRA_ENV__', { value: realEnv, configurable: true, writable: true })
  }
})
