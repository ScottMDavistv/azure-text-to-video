/**
 * Dependency-injected MSAL popup flow (L-6, Issue #62).
 *
 * Holds the sign-in, consent/token, sign-out and error-cleanup behaviour so
 * it can be exercised with a mocked MSAL client under `node --test`. Only
 * MSAL *types* are imported here; `entraAuth.ts` supplies the real
 * PublicClientApplication and InteractionRequiredAuthError.
 */
import type {
  AccountInfo,
  AuthenticationResult,
  Configuration,
  IPublicClientApplication,
} from '@azure/msal-browser'

export const LOCALHOST_REDIRECT = 'http://localhost:5173'
const LOOPBACK_ALIAS = 'http://127.0.0.1:5173'
const CLIENT_ID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

/** Only use origins explicitly registered with the Entra SPA. */
export function getRedirectUri(origin: string): string {
  if (origin === LOCALHOST_REDIRECT || origin === LOOPBACK_ALIAS) {
    return LOCALHOST_REDIRECT
  }
  throw new Error('Unregistered frontend origin; check the Entra SPA redirect URI')
}

export function isValidClientId(clientId: string): boolean {
  return CLIENT_ID_PATTERN.test(clientId)
}

export type MsalClient = Pick<
  IPublicClientApplication,
  | 'initialize'
  | 'getActiveAccount'
  | 'getAllAccounts'
  | 'setActiveAccount'
  | 'loginPopup'
  | 'logoutPopup'
  | 'acquireTokenSilent'
  | 'acquireTokenPopup'
  | 'clearCache'
>

export interface EntraAuthOptions {
  clientId: string
  tenantId: string
  apiScopes: string[]
  getOrigin: () => string
  createClient: (config: Configuration) => MsalClient
  isInteractionRequired: (error: unknown) => boolean
}

export function buildMsalConfig(clientId: string, tenantId: string, origin: string): Configuration {
  const redirectUri = getRedirectUri(origin)
  return {
    auth: {
      clientId,
      authority: `https://login.microsoftonline.com/${tenantId}`,
      redirectUri,
      postLogoutRedirectUri: redirectUri,
      navigateToLoginRequestUrl: false,
    },
    // Tokens and accounts stay in memory only; nothing persists in
    // sessionStorage/localStorage or cookies after the tab closes.
    cache: { cacheLocation: 'memoryStorage', storeAuthStateInCookie: false },
  }
}

export function createEntraAuth(options: EntraAuthOptions) {
  const clientId = options.clientId.trim()
  let initialization: Promise<MsalClient> | null = null

  function isAuthConfigured(): boolean {
    return isValidClientId(clientId)
  }

  function initializeAuth(): Promise<MsalClient> {
    if (!isAuthConfigured()) {
      return Promise.reject(new Error('The local Entra SPA client ID is not configured'))
    }
    if (!initialization) {
      let client: MsalClient
      try {
        client = options.createClient(
          buildMsalConfig(clientId, options.tenantId, options.getOrigin()),
        )
      } catch (error) {
        return Promise.reject(error)
      }
      const pending = client.initialize().then(() => client)
      initialization = pending
      // A failed initialization must not be cached; the next attempt retries.
      pending.catch(() => {
        if (initialization === pending) initialization = null
      })
    }
    return initialization
  }

  async function getSignedInAccount(): Promise<AccountInfo | null> {
    const auth = await initializeAuth()
    return auth.getActiveAccount() ?? auth.getAllAccounts()[0] ?? null
  }

  async function signIn(): Promise<void> {
    const auth = await initializeAuth()
    // On popup failure nothing is marked active; the error propagates.
    const result = await auth.loginPopup({ scopes: options.apiScopes })
    if (result.account) auth.setActiveAccount(result.account)
  }

  async function signOut(): Promise<void> {
    const auth = await initializeAuth()
    const account = await getSignedInAccount()
    try {
      await auth.logoutPopup({ account: account ?? undefined })
    } catch (error) {
      // Fail safe: never leave a signed-in account or cached tokens in
      // memory after the user asked to sign out, even if the popup failed.
      auth.setActiveAccount(null)
      try {
        await auth.clearCache({ account: account ?? undefined })
      } catch {
        // Preserve the original sign-out error.
      }
      throw error
    }
    auth.setActiveAccount(null)
  }

  async function acquireApiToken(): Promise<AuthenticationResult> {
    const auth = await initializeAuth()
    const account = await getSignedInAccount()
    if (!account) throw new Error('Sign in with Microsoft before requesting API permission')
    try {
      return await auth.acquireTokenSilent({ account, scopes: options.apiScopes })
    } catch (error) {
      if (options.isInteractionRequired(error)) {
        // Consent or re-authentication: interactive popup only, never redirect.
        return await auth.acquireTokenPopup({ account, scopes: options.apiScopes })
      }
      throw error
    }
  }

  return { isAuthConfigured, initializeAuth, getSignedInAccount, signIn, signOut, acquireApiToken }
}
