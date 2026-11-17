import {
  PublicClientApplication,
  InteractionRequiredAuthError,
  type AccountInfo,
  type AuthenticationResult,
} from '@azure/msal-browser'

const tenantId = '773e9051-c546-4b75-9432-90f4f8242660'
const apiClientId = 'a5a09b74-aead-455f-9de7-9b844c8a8465'
const localhostRedirect = 'http://localhost:5173'
const codespacesRedirect =
  'https://turbo-halibut-g4pq966pxp5vf9wxw-5173.app.github.dev'

function getRuntimeEnv(): Record<string, string | undefined> {
  const injected = (globalThis as typeof globalThis & { __ENTRA_ENV__?: Record<string, string | undefined> }).__ENTRA_ENV__
  if (injected) return injected
  if (typeof process !== 'undefined' && process.env) {
    return process.env as Record<string, string | undefined>
  }
  if (typeof import.meta !== 'undefined' && import.meta && import.meta.env) {
    return import.meta.env as Record<string, string | undefined>
  }
  return {}
}

/** Only use origins explicitly registered with the Entra SPA. */
export function getRedirectUri(origin: string): string {
  if (origin === localhostRedirect || origin === 'http://127.0.0.1:5173') {
    return localhostRedirect
  }
  if (origin === codespacesRedirect) return codespacesRedirect
  throw new Error('Unregistered frontend origin; check the Entra SPA redirect URI')
}

export const apiScopes = [`api://${apiClientId}/access_as_user`]

export function getFrontendClientId(): string {
  return (getRuntimeEnv().VITE_ENTRA_SPA_CLIENT_ID ?? '').trim()
}

export function isAuthConfigured(): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(getFrontendClientId())
}

let initialization: Promise<PublicClientApplication> | null = null
let authFactory:
  | ((config: ConstructorParameters<typeof PublicClientApplication>[0]) => PublicClientApplication)
  | null = null

export function setMsalFactory(
  factory: (config: ConstructorParameters<typeof PublicClientApplication>[0]) => PublicClientApplication,
): void {
  authFactory = factory
  initialization = null
}

export function resetInitialization(): void {
  initialization = null
}

export function resetMsalFactory(): void {
  authFactory = null
  initialization = null
}

function currentOrigin(): string {
  if (typeof window !== 'undefined' && window.location) {
    return window.location.origin
  }
  return localhostRedirect
}

export async function initializeAuth(): Promise<PublicClientApplication> {
  if (!isAuthConfigured()) {
    throw new Error('The local Entra SPA client ID is not configured')
  }
  if (!initialization) {
    const clientConfig = {
      auth: {
        clientId: getFrontendClientId(),
        authority: `https://login.microsoftonline.com/${tenantId}`,
        redirectUri: getRedirectUri(currentOrigin()),
        postLogoutRedirectUri: getRedirectUri(currentOrigin()),
        navigateToLoginRequestUrl: false,
      },
      cache: { cacheLocation: 'memoryStorage', storeAuthStateInCookie: false },
    }
    const client = (authFactory ?? ((config) => new PublicClientApplication(config)))(clientConfig)
    initialization = (async () => {
      await client.initialize()
      return client
    })()
  }
  return initialization
}

export async function getSignedInAccount(): Promise<AccountInfo | null> {
  const auth = await initializeAuth()
  return auth.getActiveAccount() ?? auth.getAllAccounts()[0] ?? null
}

export async function signIn(): Promise<void> {
  const auth = await initializeAuth()
  try {
    await auth.loginPopup({ scopes: apiScopes, prompt: 'select_account' })
  } catch (error) {
    resetInitialization()
    throw error
  }
}

export async function signOut(): Promise<void> {
  const auth = await initializeAuth()
  const account = await getSignedInAccount()
  try {
    await auth.logoutPopup({ account: account ?? undefined })
  } catch (error) {
    resetInitialization()
    throw error
  }
}

export async function acquireApiToken(): Promise<AuthenticationResult | null> {
  const auth = await initializeAuth()
  const account = await getSignedInAccount()
  if (!account) throw new Error('Sign in with Microsoft before requesting API permission')
  try {
    return await auth.acquireTokenSilent({ account, scopes: apiScopes })
  } catch (error) {
    if (error instanceof InteractionRequiredAuthError) {
      try {
        return await auth.acquireTokenPopup({ account, scopes: apiScopes, prompt: 'consent' })
      } catch (popupError) {
        resetInitialization()
        throw popupError
      }
    }
    resetInitialization()
    throw error
  }
}
