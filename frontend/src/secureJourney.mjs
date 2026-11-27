// Entra-backed video API client. No token persistence or service credentials.
// Caller must acquire a fresh delegated token for each request.
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const STATES = new Set(['created', 'queued', 'processing', 'completed', 'failed', 'cancelled'])

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

export function createVideoApi({ baseUrl = '', tokenProvider, fetcher = fetch, requestTimeoutMs = 30000 }) {
  if (!Number.isInteger(requestTimeoutMs) || requestTimeoutMs < 1 || requestTimeoutMs > 120000) {
    throw new Error('Request deadline must be between 1 and 120000 milliseconds')
  }
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
  async function requestUnbounded(path, { method = 'GET', body, signal, video = false, onDispatch } = {}) {
    const token = await tokenProvider()
    if (typeof token !== 'string' || !token.trim()) {
      throw new VideoApiError(401, 'Microsoft API permission is required.')
    }
    if (signal?.aborted) throw new DOMException('Request aborted', 'AbortError')
    onDispatch?.()
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
      if (claimed === null || !/^[0-9]+$/.test(claimed) || Number(claimed) !== video) {
        throw new VideoApiError(502, 'Video response size conflicts with authorized metadata.')
      }
      // Do not trust Content-Length to bound allocation: enforce the authorized
      // byte limit while reading, before constructing the private Blob URL.
      if (!response.body?.getReader) throw new VideoApiError(502, 'Video body is unavailable.')
      const reader = response.body.getReader()
      const chunks = []
      let size = 0
      const cancel = () => { void reader.cancel().catch(() => {}) }
      signal.addEventListener('abort', cancel, { once: true })
      try {
        while (true) {
          if (signal.aborted) throw signal.reason
          const { done, value } = await reader.read()
          if (done) break
          size += value.byteLength
          if (size > video) throw new VideoApiError(502, 'Video size is invalid.')
          chunks.push(value)
        }
        if (signal.aborted) throw signal.reason
        if (size !== video) throw new VideoApiError(502, 'Video size is invalid.')
        return new Blob(chunks, { type: 'video/mp4' })
      } catch (error) {
        cancel()
        throw error
      } finally {
        signal.removeEventListener('abort', cancel)
        reader.releaseLock()
      }
    }
    return response.json()
  }
  async function request(path, options = {}) {
    const controller = new AbortController()
    const abort = () => controller.abort(options.signal.reason)
    if (options.signal?.aborted) abort()
    else options.signal?.addEventListener('abort', abort, { once: true })
    const timer = setTimeout(() => controller.abort(new VideoApiError(0,
      'The request timed out. Its outcome may be unknown. Check job status before retrying.')),
    requestTimeoutMs)
    let rejectOnAbort
    const cancelled = new Promise((_, reject) => {
      rejectOnAbort = () => reject(controller.signal.reason)
      if (controller.signal.aborted) rejectOnAbort()
      else controller.signal.addEventListener('abort', rejectOnAbort, { once: true })
    })
    try {
      if (controller.signal.aborted) throw controller.signal.reason
      // Cover token acquisition and body parsing, not just fetch. No retry:
      // timing out a mutation does not prove that the server rejected it.
      return await Promise.race([cancelled,
        requestUnbounded(path, { ...options, signal: controller.signal })])
    } finally {
      clearTimeout(timer)
      options.signal?.removeEventListener('abort', abort)
      controller.signal.removeEventListener('abort', rejectOnAbort)
    }
  }
  const path = (projectId, jobId = '') =>
    '/projects/' + validateProjectId(projectId) + '/jobs' +
    (jobId ? '/' + validateProjectId(jobId) : '')

  return {
    async submit(projectId, prompt, signal, operationId) {
      if (typeof prompt !== 'string' || prompt.trim().length < 1 ||
          prompt.trim().length > 2000) throw new VideoApiError(422, 'Enter 1–2,000 characters.')
      const operation = operationId === undefined ? undefined : validateProjectId(operationId)
      // Reuse an operation only through an explicit caller action, never automatically.
      let dispatched = false
      try {
        const job = validateJob(await request(path(projectId), {
          method: 'POST', body: { prompt: prompt.trim(),
            ...(operation === undefined ? {} : { operation_id: operation }) }, signal,
          onDispatch: () => { dispatched = true },
        }))
        if (operation !== undefined && job.prompt !== prompt.trim()) {
          throw new VideoApiError(502, 'Creation response does not match the submitted prompt.')
        }
        return job
      } catch (error) {
        const refused = error instanceof VideoApiError &&
          [400, 401, 403, 404, 413, 422].includes(error.status)
        if (dispatched && !refused) {
          const unknown = new VideoApiError(error instanceof VideoApiError ? error.status : 0,
            'Creation outcome unknown. A job may have been saved, but no confirmed job ID was received. Do not submit again or reload to retry. Ask an authorized operator to reconcile this project before creating a replacement.')
          unknown.outcomeUnknown = true
          throw unknown
        }
        throw error
      }
    },
    async queue(projectId, jobId, signal) {
      const result = await request(path(projectId, jobId) + '/queue', { method: 'POST', signal })
      if (result?.job_id !== jobId || result?.status !== 'queued') {
        throw new VideoApiError(502, 'Unexpected queue response.')
      }
      return result
    },
    async status(projectId, jobId, signal) {
      const job = validateJob(await request(path(projectId, jobId), { signal }))
      if (job.job_id !== validateProjectId(jobId)) {
        throw new VideoApiError(502, 'Job response does not match the requested job.')
      }
      return job
    },
    async metadata(projectId, jobId, signal) {
      const data = await request(path(projectId, jobId) + '/video/metadata', { signal })
      if (data?.job_id !== jobId || data?.status !== 'completed' || data?.available !== true ||
          data?.content_type !== 'video/mp4' ||
          !Number.isSafeInteger(data.size_bytes) || data.size_bytes <= 0 ||
          data.size_bytes > 100 * 1024 * 1024 ||
          data.download_endpoint !== path(projectId, jobId) + '/video') {
        throw new VideoApiError(502, 'Untrusted video metadata.')
      }
      return data
    },
    async download(projectId, jobId, signal) {
      const metadata = await this.metadata(projectId, jobId, signal)
      return request(path(projectId, jobId) + '/video', { video: metadata.size_bytes, signal })
    },
  }
}
