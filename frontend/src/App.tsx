import { useEffect, useState } from 'react'
import {
  acquireApiToken, getSignedInAccount, initializeAuth,
  isAuthConfigured, signIn, signOut,
} from './entraAuth'
import './App.css'

function App() {
  const configured = isAuthConfigured()
  const [accountName, setAccountName] = useState<string | null>(null)
  const [message, setMessage] = useState(
    configured ? 'Microsoft Entra sign-in is ready for local testing.' :
      'Frontend app registration required. Set VITE_ENTRA_SPA_CLIENT_ID in frontend/.env.local.',
  )
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!configured) return
    let alive = true
    initializeAuth()
      .then(getSignedInAccount)
      .then((account) => {
        if (alive) {
          setAccountName(account?.name ?? account?.username ?? null)
          if (account) setMessage('Signed in. API access and project membership are not yet confirmed.')
        }
      })
      .catch(() => { if (alive) setMessage('Sign-in initialization failed. Check the SPA registration and redirect URI.') })
    return () => { alive = false }
  }, [configured])

  async function requestToken() {
    setBusy(true)
    try {
      const result = await acquireApiToken()
      if (result) {
        // Do not log, display or persist the raw API access token.
        setMessage('API access token acquired. Backend user provisioning is still required.')
      }
    } catch {
      setMessage('API permission request failed. Verify administrator consent and the registered SPA.')
    } finally {
      setBusy(false)
    }
  }

  async function login() {
    setBusy(true)
    try { await signIn() }
    catch { setMessage('Microsoft sign-in failed. Check the Entra SPA registration.'); setBusy(false) }
  }

  async function logout() {
    setBusy(true)
    try { await signOut() }
    catch { setMessage('Microsoft sign-out failed.'); setBusy(false) }
  }

  return (
    <main className="auth-shell">
      <section className="auth-card">
        <p className="eyebrow">VIDEO RENDER TOOL / DEVELOPMENT</p>
        <h1>Microsoft Entra sign-in</h1>
        <p>Development authentication test at <code>{window.location.origin}</code>. Paid rendering is disabled.</p>
        {accountName ? <p className="signed-in">Signed in as {accountName}</p> : null}
        <p role="status" className="auth-status">{message}</p>
        <div className="auth-actions">
          {!accountName ? (
            <button onClick={login} disabled={!configured || busy}>Sign in with Microsoft</button>
          ) : (
            <>
              <button onClick={requestToken} disabled={busy}>Check API permission</button>
              <button className="secondary" onClick={logout} disabled={busy}>Sign out</button>
            </>
          )}
        </div>
        <p className="fine-print">
          This frontend uses a public SPA client with Authorization Code + PKCE.
          It does not grant project roles, perform onboarding, or send requests to paid rendering services.
        </p>
      </section>
    </main>
  )
}

export default App
