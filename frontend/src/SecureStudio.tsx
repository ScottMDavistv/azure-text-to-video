import { useEffect, useMemo, useRef, useState } from 'react'
import { acquireApiToken, getSignedInAccount } from './entraAuth'
import { SCENES, TOTAL_SECONDS, validatePrompt } from './mockJourney.mjs'
import { createVideoApi, validateProjectId, type VideoJob } from './secureJourney.mjs'

import { shouldPauseStatusPolling } from './statusPolling.mjs'

const apiOrigin = import.meta.env.VITE_VIDEO_API_ORIGIN?.trim() || ''
const initialProject = import.meta.env.VITE_VIDEO_PROJECT_ID?.trim() || ''
const pollInterval = 3000
const statusDeadline = 10000
type PendingCreation = Readonly<{ project: string; account: string; prompt: string; operationId: string; retried: boolean }>

function displayError(error: unknown): string {
  if (error instanceof Error && (
    error.name === 'VideoApiError' || error.message === 'API permission required')) {
    return error.message
  }
  return 'The authenticated request could not complete. Check connectivity and permissions.'
}

export default function SecureStudio({ accountIdentity }: { accountIdentity: string | null }) {
  const [projectId, setProjectId] = useState(initialProject)
  const [uncertainProjects, setUncertainProjects] = useState<ReadonlySet<string>>(() => new Set())
  const [availableRecoveries, setAvailableRecoveries] = useState<ReadonlySet<string>>(() => new Set())
  const recoveryKey = (account: string, project: string) => JSON.stringify([account, project])
  const pendingCreations = useRef(new Map<string, PendingCreation>())
  const rememberCreation = (pending: PendingCreation) => {
    pendingCreations.current.set(pending.project, pending)
    setAvailableRecoveries(previous => new Set([...previous, recoveryKey(pending.account, pending.project)]))
  }
  const forgetCreation = (pending: PendingCreation) => {
    pendingCreations.current.delete(pending.project)
    setAvailableRecoveries(previous => {
      const next = new Set(previous)
      next.delete(recoveryKey(pending.account, pending.project))
      return next
    })
  }
  const creationUncertain = uncertainProjects.has(projectId.toLowerCase())
  const [prompt, setPrompt] = useState('')
  const [job, setJob] = useState<VideoJob | null>(null)
  const [selectedScene, setSelectedScene] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [pollEpoch, setPollEpoch] = useState(0)
  const [pollPaused, setPollPaused] = useState(false)
  const [pollError, setPollError] = useState<string | null>(null)
  const [queueError, setQueueError] = useState<string | null>(null)
  const [videoUrl, setVideoUrl] = useState<string | null>(null)
  const errorSummary = useRef<HTMLParagraphElement | null>(null)
  const generation = useRef(0)
  const inFlight = useRef<AbortController | null>(null)
  const terminalPoll = useRef<string | null>(null)
  const invalidate = () => {
    generation.current += 1
    inFlight.current?.abort()
    inFlight.current = null
  }
  const begin = () => {
    inFlight.current?.abort()
    const controller = new AbortController()
    inFlight.current = controller
    const version = ++generation.current
    return { controller, current: () => generation.current === version && !controller.signal.aborted }
  }

  useEffect(() => { if (error) errorSummary.current?.focus() }, [error])

  const api = useMemo(() => createVideoApi({
    baseUrl: apiOrigin,
    tokenProvider: async () => {
      const before = await getSignedInAccount()
      if (!accountIdentity || before?.homeAccountId !== accountIdentity) {
        throw new Error('Authentication session changed')
      }
      const result = await acquireApiToken()
      const after = await getSignedInAccount()
      if (after?.homeAccountId !== accountIdentity || !result?.accessToken) {
        throw new Error('Authentication session changed')
      }
      return result.accessToken
    },
    fetcher: async (url, options) => {
      const response = await fetch(url, options)
      const current = await getSignedInAccount()
      if (!accountIdentity || current?.homeAccountId !== accountIdentity) {
        throw new Error('Authentication session changed')
      }
      return response
    },
  }), [accountIdentity])
  useEffect(() => () => { if (videoUrl) URL.revokeObjectURL(videoUrl) }, [videoUrl])
  useEffect(() => () => { generation.current += 1; inFlight.current?.abort() }, [])

  const activeJobId = job && job.status !== 'completed' && job.status !== 'failed' && job.status !== 'cancelled' ? job.job_id : null
  useEffect(() => {
    if (!accountIdentity || !activeJobId || busy) return
    const sessionKey = JSON.stringify([accountIdentity, projectId, activeJobId])
    if (terminalPoll.current === sessionKey) return
    const version = generation.current
    const abort = new AbortController()
    const current = () => !abort.signal.aborted && generation.current === version
    let timer: number
    const started = Date.now()
    let consecutiveErrors = 0
    setPollPaused(false)
    const pause = () => {
      terminalPoll.current = sessionKey
      setPollPaused(true)
      setPollError('Automatic status checks paused. The job may still be running. Resume checks when ready; do not submit a replacement job.')
    }
    const poll = async () => {
      if (shouldPauseStatusPolling(Date.now() - started, consecutiveErrors)) { pause(); return }
      const deadline = new AbortController()
      const signal = AbortSignal.any([abort.signal, deadline.signal])
      const timeout = window.setTimeout(() => {
        deadline.abort(new DOMException('Status request timed out', 'TimeoutError'))
      }, statusDeadline)
      // The deadline also covers token acquisition and response parsing.
      // Those promises may not observe the fetch signal themselves.
      let onAbort: () => void = () => {}
      const cancelled = new Promise<never>((_, reject) => {
        onAbort = () => reject(signal.reason)
        signal.addEventListener('abort', onAbort, { once: true })
      })
      try {
        const next = await Promise.race([cancelled, (async () => {
          const result = await api.status(projectId, activeJobId, signal)
          return (await getSignedInAccount())?.homeAccountId === accountIdentity ? result : null
        })()])
        if (!current() || !next) return
        consecutiveErrors = 0
        setPollError(null)
        setJob(next)
        if (next.status === 'completed' || next.status === 'failed' || next.status === 'cancelled') return
      } catch (cause) {
        if (!current()) return
        consecutiveErrors += 1
        setPollError(deadline.signal.aborted ?
          'The status check timed out. The studio will try again.' : displayError(cause))
        if (cause instanceof Error && 'status' in cause &&
            [401, 403, 404].includes(Number(cause.status))) {
          terminalPoll.current = sessionKey
          return
        }
      } finally {
        window.clearTimeout(timeout)
        signal.removeEventListener('abort', onAbort)
      }
      if (shouldPauseStatusPolling(Date.now() - started, consecutiveErrors)) { pause(); return }
      timer = window.setTimeout(poll, pollInterval)
    }
    timer = window.setTimeout(poll, 0)
    return () => { abort.abort(); window.clearTimeout(timer) }
  }, [api, activeJobId, projectId, accountIdentity, busy, pollEpoch])

  async function queueJob(project: string, jobId: string, task: ReturnType<typeof begin>) {
    try {
      await api.queue(project, jobId, task.controller.signal)
      if (task.current()) {
        setQueueError(null)
        setJob(previous => previous?.job_id === jobId ? { ...previous, status: 'queued' } : previous)
      }
    } catch (cause) {
      if (task.current()) {
        setQueueError(displayError(cause) + ' The job was saved. Check its status before retrying the queue.')
      }
    }
  }

  async function create(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (inFlight.current || creationUncertain || pendingCreations.current.has(projectId.toLowerCase())) return
    const promptError = validatePrompt(prompt)
    if (promptError) { setError(promptError); return }
    try {
      validateProjectId(projectId)
      if (!accountIdentity) { setError('Sign in with Microsoft first.'); return }
    } catch (cause) { setError(displayError(cause)); return }
    const project = validateProjectId(projectId)
    const pending: PendingCreation = Object.freeze({ project, account: accountIdentity!,
      prompt: prompt.trim(), operationId: crypto.randomUUID(), retried: false })
    rememberCreation(pending)
    const task = begin()
    setBusy(true)
    setError(null)
    setPollError(null)
    setQueueError(null)
    setVideoUrl(null)
    try {
      const created = await api.submit(project, pending.prompt, task.controller.signal, pending.operationId)
      if ((await getSignedInAccount())?.homeAccountId !== accountIdentity || !task.current()) {
        setUncertainProjects(previous => new Set([...previous, project]))
        return
      }
      forgetCreation(pending)
      setJob(created)
      if (created.status === 'created') await queueJob(project, created.job_id, task)
    } catch (cause) {
      if (cause instanceof Error && 'outcomeUnknown' in cause && cause.outcomeUnknown === true) {
        // Keep the original project's hold even if the owner changed projects
        // while the dispatched request was being aborted.
        setUncertainProjects(previous => new Set([...previous, project.toLowerCase()]))
      } else {
        forgetCreation(pending)
      }
      if (task.current()) setError(displayError(cause))
    } finally {
      if (task.current()) { setBusy(false); inFlight.current = null }
    }
  }
  async function recoverCreation() {
    const pending = pendingCreations.current.get(projectId.toLowerCase())
    if (inFlight.current || !creationUncertain || !pending || pending.retried ||
        pending.account !== accountIdentity) return
    // Spend the one explicit recovery attempt before awaiting anything.
    pendingCreations.current.set(pending.project, Object.freeze({ ...pending, retried: true }))
    setAvailableRecoveries(previous => {
      const next = new Set(previous)
      next.delete(recoveryKey(pending.account, pending.project))
      return next
    })
    const task = begin()
    setBusy(true)
    setError(null)
    try {
      if ((await getSignedInAccount())?.homeAccountId !== pending.account) {
        throw new Error('Authentication session changed')
      }
      const recovered = await api.submit(pending.project, pending.prompt,
        task.controller.signal, pending.operationId)
      if (!task.current() || (await getSignedInAccount())?.homeAccountId !== pending.account) return
      forgetCreation(pending)
      setUncertainProjects(previous => {
        const next = new Set(previous)
        next.delete(pending.project)
        return next
      })
      setJob(recovered)
      setQueueError(null)
      setPollError(null)
      // Recovery never queues automatically. A recovered created job may be
      // queued separately through the existing, known-ID queue action.
    } catch (cause) {
      if (task.current()) setError(displayError(cause))
    } finally {
      if (task.current()) { setBusy(false); inFlight.current = null }
    }
  }
  const canRetryQueue = !creationUncertain && (job?.status === 'created' || (job?.status === 'queued' && queueError !== null))
  async function queueAgain() {
    if (inFlight.current || !job || !canRetryQueue) return
    const project = projectId
    const jobId = job.job_id
    const task = begin()
    setBusy(true)
    setError(null)
    setPollError(null)
    try {
      await queueJob(project, jobId, task)
    } finally {
      if (task.current()) { setBusy(false); inFlight.current = null }
    }
  }
  async function retrieveVideo() {
    if (inFlight.current || !job || job.status !== 'completed') return
    const project = projectId
    const jobId = job.job_id
    const task = begin()
    setBusy(true)
    setError(null)
    setVideoUrl(null)
    try {
      const blob = await api.download(project, jobId, task.controller.signal)
      if ((await getSignedInAccount())?.homeAccountId === accountIdentity && task.current()) setVideoUrl(URL.createObjectURL(blob))
    } catch (cause) {
      if (task.current()) setError(displayError(cause))
    } finally {
      if (task.current()) { setBusy(false); inFlight.current = null }
    }
  }
  return (
    <div className="dashboard">
      <section className="panel" aria-labelledby="secure-prompt-heading">
        <h2 id="secure-prompt-heading">Authenticated video request</h2>
        <p className="muted">Microsoft Entra identity and project membership are verified by the backend. Paid rendering remains disabled.</p>
        <p className="muted">{accountIdentity ? 'Microsoft session selected; project access is checked by the backend.' : 'Sign in using the account controls above before submitting.'}</p>
        <form onSubmit={create} aria-busy={busy} aria-describedby={error ? "secure-request-error" : undefined}>
          <label htmlFor="project-id" className="eyebrow">Authorized project ID</label>
          <input className="project-input" id="project-id" value={projectId} onChange={e => { invalidate(); setProjectId(e.target.value); setJob(null); setVideoUrl(null); setBusy(false); setError(null); setPollError(null); setQueueError(null) }} placeholder="Project UUID" />
          <label htmlFor="secure-video-prompt" className="eyebrow">Video prompt</label>
          <textarea id="secure-video-prompt" aria-describedby="secure-prompt-limit" maxLength={2000} value={prompt} onChange={e => setPrompt(e.target.value)} placeholder="Describe your video..." />
          <div className="actions"><span id="secure-prompt-limit" className="muted">{prompt.length}/2,000 characters</span><button className="primary" type="submit" disabled={!accountIdentity || busy || creationUncertain}>{busy ? 'Working…' : creationUncertain ? 'Creation outcome unknown' : 'Submit authenticated job'}</button></div>
        </form>
        {creationUncertain ? <p role="alert">Creation is paused for this project in this session. No confirmed ID was returned for the uncertain request. You may explicitly retry the original creation once using its unchanged operation ID and submitted prompt. Recovery only restores the job reference; it does not queue automatically. If recovery fails, ask an authorized operator to reconcile it before creating a replacement. Changing projects or reloading does not resolve the server outcome.</p> : null}
        {creationUncertain ? <button type="button" className="secondary" disabled={busy ||
          !accountIdentity || !availableRecoveries.has(recoveryKey(accountIdentity, projectId.toLowerCase()))}
          onClick={recoverCreation}>Retry original creation once</button> : null}
        {error ? <p id="secure-request-error" ref={errorSummary} tabIndex={-1} role="alert" className="field-error">{error}</p> : null}
      </section>
      <section className="panel" aria-labelledby="secure-job-heading">
        <h2 id="secure-job-heading">Last confirmed job status</h2>
        {creationUncertain ? <p className="muted">Any job shown below is the last confirmed job, not the uncertain creation request.</p> : null}
        {job ? <>
          <p><strong>{job.job_id}</strong></p>
          <p role="status">Status: <strong>{job.status}</strong></p>
          <p className="muted">Status is polled from the backend. No percentage is inferred.</p>
          {pollError ? <p role="alert" className="field-error">{pollError}</p> : null}
          {pollPaused && activeJobId ? <button className="secondary" type="button" disabled={busy} onClick={() => {
            terminalPoll.current = null
            setPollPaused(false)
            setPollError(null)
            setPollEpoch(value => value + 1)
          }}>Resume status checks</button> : null}
          {canRetryQueue && queueError ? <p role="alert" className="field-error">{queueError}</p> : null}
          {canRetryQueue ? <button className="secondary" type="button" disabled={busy} onClick={queueAgain}>Retry synthetic queue</button> : null}
          {job.status === 'cancelled' ? <p role="status">The job was cancelled. No video is available for this job.</p> : null}
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
          {videoUrl && job?.status === 'completed' ? <>
            <video controls src={videoUrl} style={{ width: '100%', maxHeight: 450, marginTop: 20 }} />
            <p><a href={videoUrl} download={job.job_id + '.mp4'}>Download authorized MP4</a></p>
          </> : null}
        </div>
      </section>
    </div>
  )
}
