import { useEffect, useMemo, useState } from 'react'
import { acquireApiToken, getSignedInAccount, isAuthConfigured, signIn } from './entraAuth'
import { SCENES, TOTAL_SECONDS, validatePrompt } from './mockJourney.mjs'
import { createVideoApi, validateProjectId, type VideoJob } from './secureJourney.mjs'

const apiOrigin = import.meta.env.VITE_VIDEO_API_ORIGIN?.trim() || ''
const initialProject = import.meta.env.VITE_VIDEO_PROJECT_ID?.trim() || ''

function displayError(error: unknown): string {
  if (error instanceof Error && (
    error.name === 'VideoApiError' || error.message === 'API permission required')) {
    return error.message
  }
  return 'The authenticated request could not complete. Check connectivity and permissions.'
}

export default function SecureStudio() {
  const [account, setAccount] = useState<string | null>(null)
  const [projectId, setProjectId] = useState(initialProject)
  const [prompt, setPrompt] = useState('')
  const [job, setJob] = useState<VideoJob | null>(null)
  const [selectedScene, setSelectedScene] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [videoUrl, setVideoUrl] = useState<string | null>(null)

  const api = useMemo(() => createVideoApi({
    baseUrl: apiOrigin,
    tokenProvider: async () => {
      const result = await acquireApiToken()
      if (!result?.accessToken) throw new Error('API permission required')
      return result.accessToken
    },
  }), [])
  useEffect(() => {
    let active = true
    getSignedInAccount().then(user => {
      if (active) setAccount(user?.name ?? user?.username ?? null)
    }).catch(() => { if (active) setError('Microsoft sign-in has not been initialized.') })
    return () => { active = false }
  }, [])
  useEffect(() => () => { if (videoUrl) URL.revokeObjectURL(videoUrl) }, [videoUrl])

  useEffect(() => {
    if (!job || job.status === 'completed' || job.status === 'failed') return
    let alive = true
    const abort = new AbortController()
    const timer = window.setInterval(async () => {
      try {
        const next = await api.status(projectId, job.job_id, abort.signal)
        if (alive) setJob(next)
      } catch (cause) {
        if (alive && !abort.signal.aborted) setError(displayError(cause))
      }
    }, 3000)
    return () => { alive = false; abort.abort(); window.clearInterval(timer) }
  }, [api, job, projectId])

  async function create(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const promptError = validatePrompt(prompt)
    if (promptError) { setError(promptError); return }
    try {
      validateProjectId(projectId)
      if (!account) { setError('Sign in with Microsoft first.'); return }
      setBusy(true)
      setError(null)
      setJob(null)
      setVideoUrl(null)
      const created = await api.submit(projectId, prompt)
      setJob(created)
      try {
        await api.queue(projectId, created.job_id)
        setJob(await api.status(projectId, created.job_id))
      } catch (cause) {
        // Creation was durable; preserve the real job ID for recovery.
        setError(displayError(cause) + ' Job creation was retained; queue it after the service is available.')
      }
    } catch (cause) {
      setError(displayError(cause))
    } finally {
      setBusy(false)
    }
  }
  async function queueAgain() {
    if (!job || job.status !== 'created') return
    try {
      setBusy(true)
      setError(null)
      await api.queue(projectId, job.job_id)
      setJob(await api.status(projectId, job.job_id))
    } catch (cause) {
      setError(displayError(cause))
    } finally { setBusy(false) }
  }
  async function retrieveVideo() {
    if (!job || job.status !== 'completed') return
    try {
      setBusy(true)
      setError(null)
      const blob = await api.download(projectId, job.job_id)
      setVideoUrl(URL.createObjectURL(blob))
    } catch (cause) {
      setError(displayError(cause))
    } finally { setBusy(false) }
  }
  return (
    <div className="dashboard">
      <section className="panel" aria-labelledby="secure-prompt-heading">
        <h2 id="secure-prompt-heading">Authenticated video request</h2>
        <p className="muted">Microsoft Entra identity and project membership are verified by the backend. Paid rendering remains disabled.</p>
        <p className="muted">{account ? `Signed in: ${account}` : 'Sign in with Microsoft before submitting.'}</p>
        {!account ? <button className="secondary" type="button" disabled={!isAuthConfigured() || busy} onClick={() => signIn().catch(() => setError('Microsoft sign-in failed.'))}>Sign in with Microsoft</button> : null}
        <form onSubmit={create}>
          <label htmlFor="project-id" className="eyebrow">Authorized project ID</label>
          <input className="project-input" id="project-id" value={projectId} onChange={e => { setProjectId(e.target.value); setJob(null); setVideoUrl(null) }} placeholder="Project UUID" />
          <label htmlFor="secure-video-prompt" className="eyebrow">Video prompt</label>
          <textarea id="secure-video-prompt" maxLength={2000} value={prompt} onChange={e => setPrompt(e.target.value)} placeholder="Describe your video..." />
          <div className="actions"><span className="muted">{prompt.length}/2,000</span><button className="primary" type="submit" disabled={!account || busy}>{busy ? 'Working…' : 'Submit authenticated job'}</button></div>
        </form>
        {error ? <p role="alert" className="field-error">{error}</p> : null}
      </section>
      <section className="panel" aria-labelledby="secure-job-heading">
        <h2 id="secure-job-heading">Live job status</h2>
        {job ? <>
          <p><strong>{job.job_id}</strong></p>
          <p role="status">Status: <strong>{job.status}</strong></p>
          <p className="muted">Status is polled from the backend. No percentage is inferred.</p>
          {job.status === 'created' ? <button className="secondary" type="button" disabled={busy} onClick={queueAgain}>Retry synthetic queue</button> : null}
          {job.status === 'failed' ? <p role="alert">The job failed. Review server-side evidence before retrying.</p> : null}
        </> : <div className="empty">No authorized job in this session.</div>}
        <div className="stats"><div className="stat"><span className="muted">Synthetic target</span><strong>05:00</strong></div><div className="stat"><span className="muted">Paid requests</span><strong>Disabled</strong></div></div>
      </section>
      <section className="panel full" aria-labelledby="secure-scenes-heading">
        <div className="panel-heading"><h2 id="secure-scenes-heading">Storyboard reference</h2><span className="muted">{SCENES.length} scenes · {TOTAL_SECONDS}s</span></div>
        <div className="scene-grid">
          {SCENES.map((scene, index) => <button className="scene" type="button" key={scene.title} aria-pressed={selectedScene === index} onClick={() => setSelectedScene(index)}>
            <div className="scene-visual">{String(index + 1).padStart(2, '0')}</div><strong>{scene.title}</strong><p>{scene.duration}s · reference only</p>
          </button>)}
        </div>
        <p className="muted">Selected: {SCENES[selectedScene].title} — {SCENES[selectedScene].description}. Scenes are examples, not a server-approved plan.</p>
      </section>
      <section className="panel full" aria-labelledby="secure-video-heading">
        <h2 id="secure-video-heading">Authorized preview and download</h2>
        <div className="preview">
          {job?.status === 'completed' ?
            <button type="button" className="primary" disabled={busy} onClick={retrieveVideo}>{videoUrl ? 'Refresh authorized video' : 'Retrieve private MP4'}</button> :
            <p>Available only when a backend job is completed and the private artifact is authorized.</p>}
          {videoUrl && job ? <>
            <video controls src={videoUrl} style={{ width: '100%', maxHeight: 450, marginTop: 20 }} />
            <p><a href={videoUrl} download={job.job_id + '.mp4'}>Download authorized MP4</a></p>
          </> : null}
        </div>
      </section>
    </div>
  )
}
