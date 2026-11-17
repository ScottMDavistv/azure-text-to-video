// Entra-backed video API client. No token persistence or service credentials.
// Caller must acquire a fresh delegated token for each request.
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const STATES = new Set(['created', 'queued', 'processing', 'completed', 'failed'])

export class VideoApiError extends Error {
  constructor(status, message) {
    super(message)
    this.name = 'VideoApiError'
    this.status = status
  }
}

export function validateProjectId(value) {
  if (typeof value !== 'string' || !UUID.test(value)) {
    throw new VideoApiError(0, 'A valid project identifier is required.')
  }
  return value.toLowerCase()
}

export function validateJob(data) {
  if (!data || !UUID.test(data.job_id) || !STATES.has(data.status) ||
      typeof data.prompt !== 'string' || typeof data.created_at !== 'string') {
    throw new VideoApiError(502, 'Unexpected API job response.')
  }
  return data
}

export function createVideoApi({ baseUrl = '', tokenProvider, fetcher = fetch }) {
  if (typeof tokenProvider !== 'function') throw new Error('Token provider required')
  // Only same-origin relative paths or an explicitly configured HTTPS API origin.
  let origin = ''
  if (baseUrl) {
    const url = new URL(baseUrl)
    if (url.protocol !== 'https:' && !(url.protocol === 'http:' &&
        (url.hostname === 'localhost' || url.hostname === '127.0.0.1'))) {
      throw new Error('Video API origin must be HTTPS (localhost development excepted)')
    }
    if (url.username || url.password || url.search || url.hash || url.pathname !== '/') {
      throw new Error('Video API origin must not contain credentials, paths or query')
    }
    origin = url.origin
  }
  async function request(path, { method = 'GET', body, signal, video = false } = {}) {
    const token = await tokenProvider()
    if (typeof token !== 'string' || !token.trim()) {
      throw new VideoApiError(401, 'Microsoft API permission is required.')
    }
    const response = await fetcher(origin + path, {
      method, signal, credentials: 'omit', redirect: 'error',
      cache: 'no-store',
      headers: {
        Authorization: 'Bearer ' + token,
        ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    })
    if (!response.ok) {
      // Do not echo server error bodies that might include sensitive records.
      const messages = {
        401: 'Sign in again to access this project.',
        403: 'You do not have permission for this project or job.',
        404: 'Video job or artifact not found.',
        409: 'Job is not ready or its state changed. Refresh status.',
        413: 'The video exceeds the configured download limit.',
        422: 'The prompt was rejected by the server.',
        503: 'The authenticated video feature or service is unavailable.',
      }
      throw new VideoApiError(response.status, messages[response.status] ||
        'Video API request failed.')
    }
    if (video) {
      const type = response.headers.get('content-type')?.split(';')[0].trim().toLowerCase()
      if (type !== 'video/mp4') throw new VideoApiError(502, 'Unexpected video format.')
      const claimed = response.headers.get('content-length')
      // Reject a declared mismatch before allocating any MP4 response body.
      if (claimed !== null && (!/^[0-9]+$/.test(claimed) || Number(claimed) !== video)) {
        throw new VideoApiError(502, 'Video response size conflicts with authorized metadata.')
      }
      const blob = await response.blob()
      if (!blob.size || blob.size > 100 * 1024 * 1024 || blob.size !== video) {
        throw new VideoApiError(502, 'Video size is invalid.')
      }
      return blob
    }
    return response.json()
  }
  const path = (projectId, jobId = '') =>
    '/projects/' + validateProjectId(projectId) + '/jobs' +
    (jobId ? '/' + validateProjectId(jobId) : '')

  return {
    async submit(projectId, prompt) {
      if (typeof prompt !== 'string' || prompt.trim().length < 1 ||
          prompt.trim().length > 2000) throw new VideoApiError(422, 'Enter 1–2,000 characters.')
      return validateJob(await request(path(projectId), {
        method: 'POST', body: { prompt: prompt.trim() },
      }))
    },
    async queue(projectId, jobId) {
      const result = await request(path(projectId, jobId) + '/queue', { method: 'POST' })
      if (result?.job_id !== jobId || result?.status !== 'queued') {
        throw new VideoApiError(502, 'Unexpected queue response.')
      }
      return result
    },
    async status(projectId, jobId, signal) {
      return validateJob(await request(path(projectId, jobId), { signal }))
    },
    async metadata(projectId, jobId) {
      const data = await request(path(projectId, jobId) + '/video/metadata')
      if (data?.job_id !== jobId || data?.status !== 'completed' || data?.available !== true ||
          data?.content_type !== 'video/mp4' ||
          !Number.isSafeInteger(data.size_bytes) || data.size_bytes <= 0 ||
          data.size_bytes > 100 * 1024 * 1024 ||
          data.download_endpoint !== path(projectId, jobId) + '/video') {
        throw new VideoApiError(502, 'Untrusted video metadata.')
      }
      return data
    },
    async download(projectId, jobId) {
      const metadata = await this.metadata(projectId, jobId)
      return request(path(projectId, jobId) + '/video', { video: metadata.size_bytes })
    },
  }
}
