import { useEffect, useState } from 'react'
import {
  acquireApiToken, getSignedInAccount, initializeAuth,
  isAuthConfigured, signIn, signOut,
} from './entraAuth'
import {
  SCENES, TOTAL_SECONDS, MOCK_STAGES, createMockJob,
  advanceMockJob, mockProgress, validatePrompt,
  type MockJob,
} from './mockJourney.mjs'
import SecureStudio from './SecureStudio'
import './App.css'

const authenticatedJourneyEnabled = import.meta.env.VITE_ENABLE_AUTHENTICATED_JOURNEY === 'true'

export default function App() {
  const configured = isAuthConfigured()
  const [accountName, setAccountName] = useState<string | null>(null)
  const [accountIdentity, setAccountIdentity] = useState<string | null>(null)
  const [authMessage, setAuthMessage] = useState(
    configured ? 'Entra local sign-in is available; backend authorization is not verified.' :
      'Entra frontend registration is required for sign-in. Demo mode is offline.',
  )
  const [busy, setBusy] = useState(false)
  const [prompt, setPrompt] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [job, setJob] = useState<MockJob | null>(null)
  const [sequence, setSequence] = useState(0)
  const [view, setView] = useState<'studio' | 'projects'>('studio')
  const [realMode, setRealMode] = useState(authenticatedJourneyEnabled)
  const [selectedScene, setSelectedScene] = useState(0)

  useEffect(() => {
    if (!configured) return
    let alive = true
    let checking = false
    async function syncIdentity() {
      if (checking) return
      checking = true
      try {
        const account = await getSignedInAccount()
        if (!alive) return
        setAccountIdentity(account?.homeAccountId ?? null)
        setAccountName(account?.name ?? account?.username ?? null)
      } catch {
        if (alive) {
          setAccountIdentity(null)
          setAccountName(null)
        }
      } finally { checking = false }
    }
    void initializeAuth().then(syncIdentity).catch(() => {
      if (alive) setAuthMessage('Sign-in initialization failed. Verify the SPA registration and redirect URI.')
    })
    // Detect MSAL account changes made outside the local header controls.
    const timer = window.setInterval(() => { void syncIdentity() }, 500)
    window.addEventListener('focus', syncIdentity)
    return () => {
      alive = false
      window.clearInterval(timer)
      window.removeEventListener('focus', syncIdentity)
    }
  }, [configured])

  async function requestToken() {
    setBusy(true)
    try {
      const result = await acquireApiToken()
      if (result) setAuthMessage('Delegated API permission acquired. User provisioning is still required.')
    } catch {
      setAuthMessage('API permission request failed. Check consent and registration.')
    } finally { setBusy(false) }
  }
  async function login() {
    setBusy(true)
    try {
      await signIn()
      const account = await getSignedInAccount()
      setAccountIdentity(account?.homeAccountId ?? null)
      setAccountName(account?.name ?? account?.username ?? null)
    } catch {
      setAuthMessage('Microsoft sign-in failed. Check the Entra registration.')
    } finally { setBusy(false) }
  }
  async function logout() {
    // Unmount SecureStudio and revoke any owned Blob URL before MSAL popup.
    setAccountIdentity(null)
    setAccountName(null)
    setBusy(true)
    try { await signOut() }
    catch { setAuthMessage('Microsoft sign-out failed. Session content cleared.') }
    finally { setBusy(false) }
  }
  function submitDemo(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const validation = validatePrompt(prompt)
    setError(validation)
    if (validation) return
    const next = sequence + 1
    setSequence(next)
    setJob(createMockJob(prompt, next))
    setSelectedScene(0)
    setView('studio')
  }
  const stageName = job ? MOCK_STAGES[job.stage] : null
  const completed = stageName === 'completed'

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">◈ Video Render Tool</div>
        <div className="nav-label">Workspace</div>
        <nav aria-label="Main navigation">
          <button className={`nav-item ${view === 'studio' ? 'active' : ''}`} onClick={() => setView('studio')}>Studio</button>
          <button className={`nav-item ${view === 'projects' ? 'active' : ''}`} onClick={() => setView('projects')}>Projects</button>
        </nav>
        <p className="sidebar-note">Paid provider disabled. Authenticated project workflow requires separately enabled backend security and synthetic dispatch.</p>
      </aside>
      <main className="workspace">
        <header className="topbar">
          <span className="eyebrow">Development workspace / {realMode ? "Authenticated API mode" : "Offline mock workflow"}</span>
          <span className="status-pill">● Paid rendering disabled</span>
        </header>
        <div className="heading-row">
          <div>
            <p className="eyebrow">VIDEO CREATION</p>
            <h1>{view === 'studio' ? 'Creative studio' : 'Your projects'}</h1>
            <p>{view === 'studio' ? realMode ? 'Submit a project-owned job using Microsoft Entra and retrieve private completed videos.' : 'Plan a five-minute synthetic video using an offline demonstration.' : 'Demo projects live only in this browser session.'}</p>
          </div>
          <div className="auth-tools">
            {accountName ? (
              <>
                <span className="muted">Signed in: {accountName}</span>
                <button className="secondary" disabled={busy} onClick={requestToken}>Check API permission</button>
                <button className="secondary" disabled={busy} onClick={logout}>Sign out</button>
              </>
            ) : <button className="secondary" disabled={!configured || busy} onClick={login}>Sign in with Microsoft</button>}
          </div>
        </div>
        <p className="muted" role="status">{authMessage}</p>
        {authenticatedJourneyEnabled ? <div className="mode-picker"><button className="secondary" type="button" aria-pressed={!realMode} onClick={() => setRealMode(false)}>Offline demo</button><button className="secondary" type="button" aria-pressed={realMode} onClick={() => setRealMode(true)}>Authenticated project</button></div> : null}
        {view === 'studio' && realMode ? <SecureStudio key={accountIdentity ?? 'signed-out'} accountIdentity={accountIdentity} /> : view === 'studio' ? (
          <div className="dashboard">
            <section className="panel" aria-labelledby="prompt-title">
              <h2 id="prompt-title">Create a video concept</h2>
              <p className="muted">Describe your story. This form runs only a deterministic local simulation.</p>
              <form onSubmit={submitDemo}>
                <label htmlFor="video-prompt" className="eyebrow">Video prompt</label>
                <textarea id="video-prompt" maxLength={2000} value={prompt} onChange={e => { setPrompt(e.target.value); setError(null) }} placeholder="Create a five-minute educational video about..." aria-invalid={Boolean(error)} aria-describedby={error ? 'prompt-error' : undefined} />
                {error ? <p className="field-error" id="prompt-error" role="alert">{error}</p> : null}
                <div className="actions">
                  <span className="muted">{prompt.trim().length}/2,000 characters</span>
                  <button className="primary" type="submit">Create mock job</button>
                </div>
              </form>
            </section>
            <section className="panel" aria-labelledby="job-title">
              <h2 id="job-title">Job status</h2>
              {job ? (
                <>
                  <p><strong>{job.id}</strong> · <span className="demo-flag">SIMULATED</span></p>
                  <p role="status">Stage: {stageName} · {mockProgress(job)}%</p>
                  <div className="progress-track" role="progressbar" aria-valuenow={mockProgress(job)} aria-valuemin={0} aria-valuemax={100} aria-label="Simulated rendering progress">
                    <div className="progress-fill" style={{ width: `${mockProgress(job)}%` }} />
                  </div>
                  <p className="muted">{completed ? 'Mock workflow complete; no MP4 was produced.' : 'Advance manually to preview job lifecycle states.'}</p>
                  <button className="primary" onClick={() => setJob(current => current ? advanceMockJob(current) : null)} disabled={completed}>Advance simulation</button>
                </>
              ) : <div className="empty">Create a mock job to see its queued, planning, rendering, assembly, and completion states.</div>}
              <div className="stats">
                <div className="stat"><span className="muted">Target duration</span><strong>05:00</strong></div>
                <div className="stat"><span className="muted">Billable calls</span><strong>0</strong></div>
              </div>
            </section>
            <section className="panel full" aria-labelledby="scenes-title">
              <div className="panel-heading"><h2 id="scenes-title">Scene plan preview</h2><span className="muted">{SCENES.length} scenes · {TOTAL_SECONDS}s total</span></div>
              <div className="scene-grid">
                {SCENES.map((scene, index) => <button type="button" className="scene" key={scene.title} onClick={() => setSelectedScene(index)} aria-pressed={selectedScene === index} style={{ textAlign: 'left', outlineColor: '#78b5ff' }}>
                  <div className="scene-visual" aria-hidden="true">{String(index + 1).padStart(2, '0')}</div>
                  <strong>{scene.title}</strong><p>{scene.duration}s · storyboard placeholder</p>
                </button>)}
              </div>
              <p className="muted"><strong>Selected scene:</strong> {SCENES[selectedScene].title} — {SCENES[selectedScene].description}</p>
            </section>
            <section className="panel full" aria-labelledby="preview-title">
              <h2 id="preview-title">Preview & download</h2>
              <div className="preview" role="note">
                <p>{completed ? 'Simulation finished. No video file exists.' : 'Video preview becomes available after real synthetic rendering is integrated.'}</p>
                <button className="secondary" type="button" disabled>Play video (unavailable)</button>{' '}
                <button className="secondary" type="button" disabled>Download MP4 (unavailable)</button>
              </div>
              <p className="muted">These controls stay disabled until a validated, authorized artifact is returned by the backend. Completion in demo mode must not imply a rendered MP4.</p>
            </section>
          </div>
        ) : (
          <section className="panel" aria-labelledby="projects-title">
            <h2 id="projects-title">Session projects</h2>
            {job ? <p><strong>{job.id}</strong> — {job.prompt} · {stageName} (mock only)</p> : <div className="empty">No session projects yet. Start in Studio to create a mock job.</div>}
            <button className="primary" onClick={() => setView('studio')}>Return to studio</button>
          </section>
        )}
      </main>
    </div>
  )
}
