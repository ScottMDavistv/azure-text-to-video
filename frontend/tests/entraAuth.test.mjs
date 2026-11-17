import test from 'node:test'
import assert from 'node:assert/strict'

const mockApp = {
  initialize: async () => {},
  getActiveAccount: () => ({ homeAccountId: 'acct-1', username: 'demo@localhost' }),
  getAllAccounts: () => [{ homeAccountId: 'acct-1', username: 'demo@localhost' }],
  loginPopup: async () => {},
  logoutPopup: async () => {},
  acquireTokenSilent: async () => ({ accessToken: 'token-123' }),
  acquireTokenPopup: async () => ({ accessToken: 'token-456' }),
  handleRedirectPromise: async () => {},
}

const realEnv = globalThis.__ENTRA_ENV__

test('popup login, consent token and signout work in mocked MSAL flow', async () => {
  const mod = await import('../src/entraAuth.ts')
  const { initializeAuth, getSignedInAccount, signIn, acquireApiToken, signOut } = mod

  const PublicClientApplication = (await import('@azure/msal-browser')).PublicClientApplication
  const original = PublicClientApplication
  ;(globalThis).process = { env: { VITE_ENTRA_SPA_CLIENT_ID: '6d5e9e96-8be6-445c-9176-3b67d020612c' } }
  const mockCtor = async () => {
    return mockApp
  }
  Object.defineProperty(globalThis, '__ENTRA_ENV__', { value: { VITE_ENTRA_SPA_CLIENT_ID: '6d5e9e96-8be6-445c-9176-3b67d020612c' }, writable: true })

  try {
    const auth = await initializeAuth()
    assert.equal(auth, mockApp)
    assert.deepEqual(await getSignedInAccount(), { homeAccountId: 'acct-1', username: 'demo@localhost' })
    await signIn()
    const result = await acquireApiToken()
    assert.equal(result.accessToken, 'token-123')
    await signOut()
  } finally {
    Object.defineProperty(globalThis, '__ENTRA_ENV__', { value: realEnv, writable: true })
    ;(globalThis).process = { env: {} }
  }
})

test('negative auth flows reset state and fail closed', async () => {
  const mod = await import('../src/entraAuth.ts')
  const { initializeAuth, acquireApiToken, signIn, signOut } = mod

  const PublicClientApplication = (await import('@azure/msal-browser')).PublicClientApplication
  const original = PublicClientApplication
  const pending = {
    ...mockApp,
    getAllAccounts: () => [],
    getActiveAccount: () => null,
    loginPopup: async () => { throw new Error('popup-denied') },
    logoutPopup: async () => { throw new Error('logout-failed') },
    acquireTokenSilent: async () => { throw new Error('no silent token') },
    acquireTokenPopup: async () => { throw new Error('consent failed') },
  }
  Object.defineProperty(globalThis, '__ENTRA_ENV__', { value: { VITE_ENTRA_SPA_CLIENT_ID: '6d5e9e96-8be6-445c-9176-3b67d020612c' }, writable: true })
  try {
    await assert.rejects(signIn, /popup-denied/)
    await assert.rejects(signOut, /logout-failed/)
    await assert.rejects(acquireApiToken, /no silent token/)
  } finally {
    Object.defineProperty(globalThis, '__ENTRA_ENV__', { value: realEnv, writable: true })
  }
})
