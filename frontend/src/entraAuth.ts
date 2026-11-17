import {
  PublicClientApplication,
  InteractionRequiredAuthError,
  type AccountInfo,
  type AuthenticationResult,
} from '@azure/msal-browser'

const tenantId = '773e9051-c546-4b75-9432-90f4f8242660'
const apiClientId = 'a5a09b74-aead-455f-9de7-9b844c8a8465'
const localhostRedirect = 'http://localhost:5173'

/** Only use origins explicitly registered with the Entra SPA. */
export function getRedirectUri(origin: string): string {
  if (origin === localhostRedirect || origin === 'http://127.0.0.1:5173') {
    return localhostRedirect
  }
  throw new Error('Unregistered frontend origin; check the Entra SPA redirect URI')
}

export const apiScopes = [`api://${apiClientId}/access_as_user`]

const frontendClientId = import.meta.env.VITE_ENTRA_SPA_CLIENT_ID?.trim() ?? ''

export function isAuthConfigured(): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(frontendClientId)
}

let initialization: Promise<PublicClientApplication> | null = null

export async function initializeAuth(): Promise<PublicClientApplication> {
  if (!isAuthConfigured()) {
    throw new Error('The local Entra SPA client ID is not configured')
  }
  if (!initialization) {
    const client = new PublicClientApplication({
      auth: {
        clientId: frontendClientId,
        authority: `https://login.microsoftonline.com/${tenantId}`,
        redirectUri: getRedirectUri(window.location.origin),
        postLogoutRedirectUri: getRedirectUri(window.location.origin),
        navigateToLoginRequestUrl: false,
      },
      cache: { cacheLocation: 'memoryStorage', storeAuthStateInCookie: false },
    })
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
  const result = await auth.loginPopup({ scopes: apiScopes })
  if (result.account) auth.setActiveAccount(result.account)
}

export async function signOut(): Promise<void> {
  const auth = await initializeAuth()
  const account = await getSignedInAccount()
  await auth.logoutPopup({ account: account ?? undefined })
}

export async function acquireApiToken(): Promise<AuthenticationResult | null> {
  const auth = await initializeAuth()
  const account = await getSignedInAccount()
  if (!account) throw new Error('Sign in with Microsoft before requesting API permission')
  try {
    return await auth.acquireTokenSilent({ account, scopes: apiScopes })
  } catch (error) {
    if (error instanceof InteractionRequiredAuthError) {
      return await auth.acquireTokenPopup({ account, scopes: apiScopes })
    }
    throw error
  }
}
