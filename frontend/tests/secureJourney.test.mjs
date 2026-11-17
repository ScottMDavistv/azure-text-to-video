import test from 'node:test'
import assert from 'node:assert/strict'
import { createVideoApi, validateProjectId, validateJob, VideoApiError } from '../src/secureJourney.mjs'

const PROJECT = 'c7e6a845-cb0c-468c-9a18-1e9df0b20545'
const JOB = '14588b13-6c3b-4dd0-8e8b-0d22d3e26f2c'
const sample = { job_id: JOB, prompt: 'Morning light', status: 'created', created_at: '2026-11-16T00:00:00Z' }

function createFetch(steps, calls) {
  return async (url, init) => {
    calls.push({ url, init })
    const next = steps.shift()
    return {
      ok: next.ok !== false,
      status: next.status ?? 200,
      headers: { get: () => next.contentType ?? 'video/mp4' },
      json: async () => next.json,
      blob: async () => next.blob,
    }
  }
}

test('rejects invalid project/job identifiers and untrusted responses', () => {
  assert.equal(validateProjectId(PROJECT), PROJECT)
  for (const value of ('../jobs', 'abc', '', 'javascript:alert(1)')) {
    assert.throws(() => validateProjectId(value), VideoApiError)
  }
  assert.throws(() => validateJob({ status: 'completed', job_id: '../foo' }), VideoApiError)
})

test('rejects insecure API origins and embedded credentials', () => {
  const tokenProvider = async () => 'private-token'
  for (const origin of ['http://evil.example', 'https://user:pass@api.example',
    'https://api.example/redirect', 'javascript:alert(1)']) {
    assert.throws(() => createVideoApi({ baseUrl: origin, tokenProvider }))
  }
})

test('submits, queues and polls only with scoped bearer token', async () => {
  const calls = []
  const steps = [
    { json: sample },
    { json: { job_id: JOB, status: 'queued', dispatched: true } },
    { json: { ...sample, status: 'processing' } },
  ]
  const api = createVideoApi({ tokenProvider: async () => 'scoped-access', fetcher: createFetch(steps, calls) })
  assert.equal((await api.submit(PROJECT, '  Morning light ')).job_id, JOB)
  assert.equal((await api.queue(PROJECT, JOB)).status, 'queued')
  assert.equal((await api.status(PROJECT, JOB)).status, 'processing')
  assert.ok(calls.every(x => x.init.headers.Authorization === 'Bearer scoped-access'))
  assert.ok(calls.every(x => x.init.redirect === 'error' && x.init.credentials === 'omit'))
  assert.equal(calls[0].init.body, JSON.stringify({ prompt: 'Morning light' }))
  assert.ok(calls.every(x => x.url.startsWith('/projects/' + PROJECT + '/jobs')))
})

test('never calls network without an Entra API token', async () => {
  let invoked = false
  const api = createVideoApi({
    tokenProvider: async () => '',
    fetcher: async () => { invoked = true; throw new Error('network should not be called') },
  })
  await assert.rejects(api.submit(PROJECT, 'Morning light'), { status: 401 })
  assert.equal(invoked, false)
})

test('blocks unavailable backend rather than pretending completion', async () => {
  const api = createVideoApi({
    tokenProvider: async () => 'signed-token',
    fetcher: createFetch([{ ok: false, status: 503 }], []),
  })
  await assert.rejects(api.submit(PROJECT, 'Morning light'), { status: 503 })
})

test('requires metadata endpoint be same authorized project/job', async () => {
  const calls = []
  const metadata = { job_id: JOB, available: true, status: 'completed', content_type: 'video/mp4',
    size_bytes: 1200, download_endpoint: '/projects/' + PROJECT + '/jobs/' + JOB + '/video' }
  const api = createVideoApi({ tokenProvider: async () => 'signed-token',
    fetcher: createFetch([{ json: metadata }], calls) })
  assert.equal((await api.metadata(PROJECT, JOB)).available, true)
  const wrong = createVideoApi({ tokenProvider: async () => 'signed-token',
    fetcher: createFetch([{ json: { ...metadata, download_endpoint: 'https://evil.example/mp4' } }], []) })
  await assert.rejects(wrong.metadata(PROJECT, JOB), { status: 502 })
})

test('video retrieved with authorization is verified MP4 with bounded size', async () => {
  const metadata = { job_id: JOB, available: true, status: 'completed', content_type: 'video/mp4', size_bytes: 3,
    download_endpoint: '/projects/' + PROJECT + '/jobs/' + JOB + '/video' }
  const calls = []
  const api = createVideoApi({ tokenProvider: async () => 'signed-token',
    fetcher: createFetch([{ json: metadata },
      { contentType: 'video/mp4', blob: new Blob(['mp4'], { type: 'video/mp4' }) }], calls) })
  const blob = await api.download(PROJECT, JOB)
  assert.equal(blob.size, 3)
  assert.equal(calls[1].init.headers.Authorization, 'Bearer signed-token')
  assert.equal(calls[1].init.redirect, 'error')
})

test('rejects HTML video response and excessive blob', async () => {
  const metadata = { job_id: JOB, available: true, status: 'completed', content_type: 'video/mp4', size_bytes: 3,
    download_endpoint: '/projects/' + PROJECT + '/jobs/' + JOB + '/video' }
  const api = createVideoApi({ tokenProvider: async () => 'signed-token',
    fetcher: createFetch([{ json: metadata },
      { contentType: 'text/html', blob: new Blob(['bad']) }], []) })
  await assert.rejects(api.download(PROJECT, JOB), { status: 502 })
})

test('project MP4 blocks corrupted or mismatched content length', async () => {
  const metadata = { job_id: JOB, status: 'completed', available: true,
    content_type: 'video/mp4', size_bytes: 3,
    download_endpoint: '/projects/' + PROJECT + '/jobs/' + JOB + '/video' }
  for (const bad of [
    { size: 4, claimed: '3' },
    { size: 3, claimed: '4' },
    { size: 3, claimed: 'garbage' },
  ]) {
    const fetcher = async (_url, _options) => {
      if (!fetcher.second) { fetcher.second = true; return {
        ok: true, json: async () => metadata,
      } }
      return { ok: true, headers: { get: name => name === 'content-type'
        ? 'video/mp4' : name === 'content-length' ? bad.claimed : null },
        blob: async () => new Blob(['x'.repeat(bad.size)], { type: 'video/mp4' }) }
    }
    const api = createVideoApi({ tokenProvider: async () => 'signed', fetcher })
    await assert.rejects(api.download(PROJECT, JOB), { status: 502 })
  }
})
test('rejects video metadata when job is not completed', async () => {
  const metadata = { job_id: JOB, status: 'processing', available: true,
    content_type: 'video/mp4', size_bytes: 3,
    download_endpoint: '/projects/' + PROJECT + '/jobs/' + JOB + '/video' }
  const api = createVideoApi({ tokenProvider: async () => 'signed',
    fetcher: createFetch([{json: metadata}], []) })
  await assert.rejects(api.metadata(PROJECT, JOB), { status: 502 })
})
