import {
  PublicClientApplication,
  InteractionRequiredAuthError,
  type AccountInfo,
  type AuthenticationResult,
} from '@azure/msal-browser'
import { createEntraAuth, getRedirectUri, type MsalClient } from './entraAuthCore.ts'

const tenantId = '773e9051-c546-4b75-9432-90f4f8242660'
const apiClientId = 'a5a09b74-aead-455f-9de7-9b844c8a8465'

export { getRedirectUri }

export const apiScopes = [`api://${apiClientId}/access_as_user`]

const auth = createEntraAuth({
  clientId: import.meta.env.VITE_ENTRA_SPA_CLIENT_ID?.trim() ?? '',
  tenantId,
  apiScopes,
  getOrigin: () => window.location.origin,
  createClient: (config) => new PublicClientApplication(config),
  isInteractionRequired: (error) => error instanceof InteractionRequiredAuthError,
})

export function isAuthConfigured(): boolean {
  return auth.isAuthConfigured()
}

export async function initializeAuth(): Promise<MsalClient> {
  return auth.initializeAuth()
}

export async function getSignedInAccount(): Promise<AccountInfo | null> {
  return auth.getSignedInAccount()
}

export async function signIn(): Promise<void> {
  return auth.signIn()
}

export async function signOut(): Promise<void> {
  return auth.signOut()
}

export async function acquireApiToken(): Promise<AuthenticationResult | null> {
  return auth.acquireApiToken()
}
