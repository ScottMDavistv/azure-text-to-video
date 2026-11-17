// Browser-only MSAL replacement for deterministic account-switch regression.
// Used exclusively by the e2e Vite alias; not bundled in the production build.
export function isAuthConfigured() { return true }
export async function initializeAuth() { return {} }
export async function getSignedInAccount() { return window.__raceAccount ?? null }
export async function signIn() {
  window.__raceAccount = { homeAccountId: 'account-A', name: 'Account A', username: 'a@example.test' }
}
export async function signOut() { window.__raceAccount = null }
export async function acquireApiToken() {
  const account = await getSignedInAccount()
  if (!account) throw new Error('Authentication session changed')
  return { accessToken: 'offline-mocked-' + account.homeAccountId }
}
