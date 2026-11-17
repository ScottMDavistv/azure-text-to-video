import test from 'node:test'
import assert from 'node:assert/strict'
import { createVideoApi, validateApiBase, VideoApiError } from '../src/videoApi.mjs'
const ID = '71d0d32a-8532-42e5-b710-3e8b71a3126c'
const reply = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'content-type': 'application/json' } })
test('origin policy rejects dangerous or ambiguous endpoint origins', () => {
  for (const value of ['http://api.example.com', 'https://user:pass@api.example.com', 'https://api.example.com/extra', 'https://api.example.com/?token=x', 'javascript:alert(1)', ''])
    assert.throws(() => validateApiBase(value), VideoApiError)
  assert.equal(validateApiBase('http://localhost:8000'), 'http://localhost:8000')
  assert.equal(validateApiBase('https://api.example.com'), 'https://api.example.com')
})
test('submit, queue, status, metadata and blob use scoped bearer header and exact paths', async () => {
  const seen = []
  const fetcher = async (url, options) => {
    seen.push({ url, options })
    const path = new URL(url).pathname
    if (path === '/jobs') return reply({ job_id: ID, status: 'created' }, 201)
    if (path.endsWith('/queue')) return reply({ job_id: ID, status: 'queued' })
    if (path.endsWith('/metadata')) return reply({ job_id: ID, status: 'completed', available: true, content_type: 'video/mp4', size_bytes: 4, download_endpoint: `/jobs/${ID}/video` })
    if (path.endsWith('/video')) return new Response(new Blob(['test'], { type: 'video/mp4' }), { headers: { 'content-type': 'video/mp4' } })
    return reply({ job_id: ID, status: 'completed' })
  }
  const api = createVideoApi('https://api.example.com', fetcher)
  await api.submit('Calm ocean sunrise', 'access-token')
  await api.queue(ID, 'access-token')
  await api.status(ID, 'access-token')
  await api.download(ID, 'access-token')
  assert.equal(seen.length, 5)
  assert.equal(seen[0].options.method, 'POST')
  assert.equal(JSON.parse(seen[0].options.body).prompt, 'Calm ocean sunrise')
  assert.ok(seen.every(c => c.options.headers.Authorization === 'Bearer access-token' && c.options.redirect === 'error' && c.options.credentials === 'omit'))
  assert.equal(seen[4].url, `https://api.example.com/jobs/${ID}/video`)
})
test('invalid state, identity, download metadata or HTTP auth fails closed', async () => {
  const fail = createVideoApi('https://api.example.com', async () => reply({ detail: 'sensitive detail' }, 401))
  await assert.rejects(fail.status(ID, 'token'), /authorization failed/)
  const api = createVideoApi('https://api.example.com', async () => reply({ job_id: 'wrong', status: 'completed' }))
  await assert.rejects(api.status(ID, 'token'), /Unexpected job/)
  await assert.rejects(api.queue('invalid', 'token'), /Invalid job ID/)
  const bad = createVideoApi('https://api.example.com', async () => reply({ job_id: ID, status: 'completed', available: true, content_type: 'video/mp4', size_bytes: 5, download_endpoint: 'https://attacker.test/video' }))
  await assert.rejects(bad.download(ID, 'token'), /invalid video metadata/)
  await assert.rejects(fail.submit('', 'token'), /Prompt/)
  await assert.rejects(fail.status(ID, ''), /Sign in/)
})
