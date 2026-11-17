import { useEffect, useRef, useState } from 'react'
import { acquireApiToken } from './entraAuth'
import { createVideoApi, validateApiBase, type Job } from './videoApi.mjs'

const configuredBase = import.meta.env.VITE_JOB_API_BASE_URL?.trim() ?? ''
let endpointError: string | null = null
try { validateApiBase(configuredBase) } catch { endpointError = 'A valid HTTPS job API origin must be configured.' }
const api = endpointError ? null : createVideoApi(configuredBase)
const terminal = new Set(['completed', 'failed'])

function message(error: unknown): string {
  if (error instanceof Error && error.name === 'AbortError') return ''
  return error instanceof Error ? error.message : 'Request failed.'
}

export default function LiveJourney({ accountName }: { accountName: string | null }) {
  const [prompt, setPrompt] = useState('')
  const [job, setJob] = useState<Job | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState('Authenticated API mode needs a user-authorizing backend; existing service-only endpoints cannot accept Entra tokens.')
  const [videoUrl, setVideoUrl] = useState<string | null>(null)
  const controllers = useRef<Set<AbortController>>(new Set())
  const videoUrlRef = useRef<string | null>(null)
  const activeJob = useRef<string | null>(null)

  function revokeVideo() {
    if (videoUrlRef.current) URL.revokeObjectURL(videoUrlRef.current)
    videoUrlRef.current = null
    setVideoUrl(null)
  }
  useEffect(() => () => {
    for (const controller of controllers.current) controller.abort()
    if (videoUrlRef.current) URL.revokeObjectURL(videoUrlRef.current)
  }, [])
  async function authorized<T>(operation: (token: string, signal: AbortSignal) => Promise<T>): Promise<T> {
    const controller = new AbortController()
    controllers.current.add(controller)
    try {
      const result = await acquireApiToken()
      if (!result) throw new Error('Complete Microsoft sign-in, then retry.')
      // Access token is intentionally kept in a local stack variable, never in React state/logs.
      if (controller.signal.aborted) throw new DOMException('Canceled', 'AbortError')
      return await operation(result.accessToken, controller.signal)
    } finally { controllers.current.delete(controller) }
  }

  async function create(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!api || !accountName || busy) return
    setBusy(true); setError(null); setNotice('Submitting authenticated job...')
    revokeVideo()
    activeJob.current = null
    setJob(null)
    try {
      const created = await authorized((token, signal) => api.submit(prompt.trim(), token, signal))
      activeJob.current = created.job_id
      setJob(created)
      try {
        const queued = await authorized((token, signal) => api.queue(created.job_id, token, signal))
        setJob(previous => previous?.job_id === created.job_id ? { ...previous, status: queued.status as Job['status'] } : previous)
        setNotice('Job submitted and queued. Polling the server for state changes.')
      } catch (e) {
        setError(message(e))
        setNotice('Job creation may have succeeded. Use Refresh status before resubmitting.')
      }
    } catch (e) { setError(message(e)); setNotice('Job submission did not complete.') }
    finally { setBusy(false) }
  }

  async function refresh(id: string, silent = false) {
    if (!api || !accountName || activeJob.current !== id) return
    try {
      const updated = await authorized((token, signal) => api.status(id, token, signal))
      if (activeJob.current !== id) return
      setJob(updated)
      if (updated.status === 'completed') setNotice('Server reports complete. Retrieve the private MP4 to preview/download.')
      if (updated.status === 'failed') setNotice('Rendering failed. No video is available.')
      if (!silent) setError(null)
    } catch (e) { if (activeJob.current === id) setError(message(e)) }
  }

  useEffect(() => {
    if (!job || terminal.has(job.status) || !api || !accountName) return
    const id = job.job_id
    const timer = window.setInterval(() => { void refresh(id, true) }, 5000)
    return () => window.clearInterval(timer)
    // Intentional: schedule by stable job ID/status; avoid making interval depend on each job response.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.job_id, job?.status, accountName])

  async function retrieve() {
    if (!api || !job || job.status !== 'completed' || busy) return
    setBusy(true); setError(null)
    try {
      const blob = await authorized((token, signal) => api.download(job.job_id, token, signal))
      revokeVideo()
      const url = URL.createObjectURL(blob)
      videoUrlRef.current = url
      setVideoUrl(url)
      setNotice('Private MP4 downloaded into this browser session. The URL is revoked when leaving this view.')
    } catch (e) { setError(message(e)) }
    finally { setBusy(false) }
  }

  return <section className="panel full" aria-labelledby="live-title">
    <div className="panel-heading"><h2 id="live-title">Authenticated job workflow</h2><span className="status-pill">Backend contract integration</span></div>
    <p className="muted">Uses delegated Entra API access only. No service-token override, hidden credential, or public blob URL is supported.</p>
    {endpointError ? <p role="status" className="field-error">{endpointError}</p> : null}
    {!accountName ? <p role="status">Sign in with Microsoft to use the authenticated workflow.</p> : null}
    <form onSubmit={create}>
      <label htmlFor="live-prompt" className="eyebrow">Authenticated video prompt</label>
      <textarea id="live-prompt" maxLength={2000} value={prompt} onChange={e => setPrompt(e.target.value)} placeholder="Enter a one-to-2,000-character video prompt" />
      <button className="primary" type="submit" disabled={!api || !accountName || busy || prompt.trim().length < 1}>Submit and queue job</button>
    </form>
    <p role="status">{notice}</p>
    {error ? <p role="alert" className="field-error">{error}</p> : null}
    {job ? <div>
      <p><strong>Job:</strong> {job.job_id}</p>
      <p><strong>Actual API state:</strong> {job.status}. The current API does not return a percent complete.</p>
      <button className="secondary" onClick={() => void refresh(job.job_id)} disabled={busy}>Refresh status</button>{' '}
      <button className="primary" disabled={job.status !== 'completed' || busy} onClick={() => void retrieve()}>Retrieve authorized MP4</button>
    </div> : null}
    {videoUrl && job?.status === 'completed' ? <div className="preview">
      <video controls preload="metadata" src={videoUrl} style={{ maxWidth: '100%', width: 720 }} aria-label="Completed video preview" />
      <p><a href={videoUrl} download={`${job.job_id}.mp4`} className="secondary">Download MP4</a></p>
    </div> : <div className="preview"><p>Playback and download are unavailable until an authenticated completed job yields a verified MP4.</p></div>}
  </section>
}
