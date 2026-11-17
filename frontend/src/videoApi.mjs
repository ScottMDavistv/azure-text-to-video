// Browser-only client contract. Never use a service credential or put tokens in VITE_ settings.
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const STATES = new Set(['created', 'queued', 'processing', 'completed', 'failed'])
const MAX_BYTES = 100 * 1024 * 1024

export class VideoApiError extends Error {
  constructor(message, status = 0) {
    super(message)
    this.name = 'VideoApiError'
    this.status = status
  }
}
export function validateApiBase(value) {
  if (!value || typeof value !== 'string') throw new VideoApiError('Job API endpoint is not configured.')
  let url
  try { url = new URL(value) } catch { throw new VideoApiError('Invalid job API endpoint.') }
  const local = url.hostname === 'localhost' || url.hostname === '127.0.0.1'
  if (url.protocol !== 'https:' && !(local && url.protocol === 'http:'))
    throw new VideoApiError('Job API requires HTTPS, except on localhost.')
  if (url.username || url.password || url.search || url.hash || url.pathname !== '/')
    throw new VideoApiError('Job API base must be an origin without credentials or a path.')
  return url.origin
}
export function validJobId(value) { return typeof value === 'string' && UUID.test(value) }
function checkJob(data, id) {
  if (!data || !validJobId(data.job_id) || !STATES.has(data.status) || (id && data.job_id !== id))
    throw new VideoApiError('Unexpected job response from API.')
  return data
}
function safePath(id, suffix = '') {
  if (!validJobId(id)) throw new VideoApiError('Invalid job ID.')
  return `/jobs/${id}${suffix}`
}
async function request(origin, path, token, fetcher, options = {}) {
  if (typeof token !== 'string' || token.length === 0) throw new VideoApiError('Sign in and obtain API permission first.')
  let response
  try {
    response = await fetcher(origin + path, {
      ...options, redirect: 'error', cache: 'no-store', credentials: 'omit',
      headers: { 'Authorization': `Bearer ${token}`, 'Accept': options.binary ? 'video/mp4' : 'application/json', ...(options.body ? { 'Content-Type': 'application/json' } : {}) },
    })
  } catch {
    throw new VideoApiError('API network request failed. Check connectivity and server access.')
  }
  if (!response.ok) {
    const status = response.status
    const messages = {
      400: 'The request is invalid.', 401: 'API authorization failed. Sign in again or contact an administrator.',
      403: 'You do not have access to this job.', 404: 'Job or video was not found.',
      409: 'The job cannot perform this action in its current state.',
      413: 'The video exceeds the allowed download size.',
      422: 'The prompt or request was rejected.', 429: 'Too many requests. Try again later.',
      503: 'The API, dispatcher, or video delivery is unavailable.',
    }
    throw new VideoApiError(messages[status] || `API request failed (HTTP ${status}).`, status)
  }
  return response
}
export function createVideoApi(base, fetcher = fetch) {
  const origin = validateApiBase(base)
  async function json(path, token, options) {
    const response = await request(origin, path, token, fetcher, options)
    try { return await response.json() } catch { throw new VideoApiError('API returned invalid JSON.') }
  }
  return {
    async submit(prompt, token, signal) {
      if (typeof prompt !== 'string' || !prompt.trim() || prompt.length > 2000)
        throw new VideoApiError('Prompt must contain 1–2,000 characters.')
      const job = checkJob(await json('/jobs', token, { method: 'POST', body: JSON.stringify({ prompt }), signal }))
      if (job.status !== 'created') throw new VideoApiError('Unexpected initial job state.')
      return job
    },
    async queue(jobId, token, signal) {
      const response = await json(safePath(jobId, '/queue'), token, { method: 'POST', signal })
      if (response.job_id !== jobId || response.status !== 'queued') throw new VideoApiError('Unexpected queue response.')
      return response
    },
    async status(jobId, token, signal) {
      return checkJob(await json(safePath(jobId), token, { signal }), jobId)
    },
    async metadata(jobId, token, signal) {
      const data = await json(safePath(jobId, '/video/metadata'), token, { signal })
      if (!data || data.job_id !== jobId || data.status !== 'completed' || data.available !== true ||
          data.content_type !== 'video/mp4' || !Number.isInteger(data.size_bytes) ||
          data.size_bytes < 1 || data.size_bytes > MAX_BYTES ||
          data.download_endpoint !== safePath(jobId, '/video'))
        throw new VideoApiError('API returned invalid video metadata.')
      return data
    },
    async download(jobId, token, signal) {
      const metadata = await this.metadata(jobId, token, signal)
      const response = await request(origin, safePath(jobId, '/video'), token, fetcher, { signal, binary: true })
      const type = response.headers.get('content-type')?.split(';')[0]?.trim().toLowerCase()
      if (type !== 'video/mp4') throw new VideoApiError('API did not return an MP4.')
      const declared = response.headers.get('content-length')
      if (declared !== null && (!/^\\d+$/.test(declared) || Number(declared) !== metadata.size_bytes))
        throw new VideoApiError('Downloaded video length did not match metadata.')
      const blob = await response.blob()
      if (!blob.size || blob.size > MAX_BYTES || blob.size !== metadata.size_bytes)
        throw new VideoApiError('Downloaded video size did not match authorized metadata.')
      return blob
    },
  }
}
