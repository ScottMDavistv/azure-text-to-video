import { acquireApiToken, getSignedInAccount } from '/src/entraAuth.ts'
const control = '/projects/ticket90-local-check'
const box = document.createElement('section')
box.style.cssText = 'position:relative;background:#123;color:white;padding:16px;font:16px system-ui;z-index:1000'
box.innerHTML = '<strong>Ticket #90 local acceptance</strong> <label>One-time setup code <input id="t90-code" type="password" autocomplete="off"></label> <button id="t90-enroll">Register this test session</button> <button id="t90-check">Check video access</button> <span id="t90-result" role="status"></span><div id="t90-extra"></div>'
document.body.prepend(box)
const el = id => box.querySelector('#t90-' + id)
let running = false
let played = false
document.addEventListener('timeupdate', e => {
  if (e.target instanceof HTMLVideoElement && e.target.currentTime > 0) played = true
}, true)
const safeStatus = status => [0, 200, 201, 204, 400, 401, 403, 404, 409, 422, 429, 500, 502, 503, 504].includes(status)
  ? String(status) : 'other'
function showStop(target, phase, status) {
  target.textContent = 'STOP. Do not retry or grant permissions. ' +
    'TKT90 stopped-at=' + phase + ' http=' + status
}
function action(button, initialPhase, task) {
  el(button).onclick = async () => {
    if (running) return
    running = true
    el('result').textContent = 'Working…'
    let phase = initialPhase
    let status = 'not-observed'
    const at = next => { phase = next; status = 'not-observed' }
    const observed = response => { status = safeStatus(response.status); return response }
    try { await task(at, observed) }
    catch { showStop(el('result'), phase, status) }
    finally { running = false }
  }
}
async function bearer() {
  const token = (await acquireApiToken())?.accessToken
  if (!token) throw Error('token-unavailable')
  return { Authorization: 'Bearer ' + token }
}
async function local(path, options = {}) {
  return fetch(path, { credentials: 'omit', redirect: 'error', cache: 'no-store', ...options })
}
action('enroll', 'register-find-account', async (at, observed) => {
  if (!(await getSignedInAccount())) throw Error('not-signed-in')
  const code = el('code').value.trim()
  el('code').value = ''
  at('register-get-api-token')
  const auth = await bearer()
  at('register-local-api')
  const r = observed(await local(control + '/enroll', {
    method: 'POST',
    headers: { ...auth, 'Content-Type': 'application/json' },
    body: JSON.stringify({ code }),
  }))
  if (!r.ok) throw Error('enrollment-failed')
  at('register-read-response')
  observed(r)
  const data = await r.json()
  el('result').textContent = data.role === 'member'
    ? 'MEMBER READY. Paste the project ID below into Authorized project ID.'
    : 'NONMEMBER READY. Do not submit a job from this window.'
  el('extra').textContent = data.role === 'member' ? 'Test project ID: ' + data.project : ''
})
action('check', 'check-find-account', async (at, observed) => {
  const signedIn = Boolean(await getSignedInAccount())
  at('check-get-api-token')
  const headers = signedIn ? await bearer() : {}
  at('check-direct-api')
  const r = observed(await local(control + '/check', { method: 'POST', headers }))
  if (!r.ok) throw Error('direct-check-failed')
  at('check-read-response')
  observed(r)
  const data = await r.json()
  const results = []
  for (const suffix of ['', '/video/metadata', '/video']) {
    at(suffix === '' ? 'check-job-status' : suffix === '/video/metadata' ? 'check-video-metadata' : 'check-video-bytes')
    const response = observed(await local(data.path + suffix, { headers }))
    if (response.status !== data.expected) throw Error('unexpected-status')
    if (data.expected !== 200) {
      const body = await response.json()
      if (body.detail !== data.detail) throw Error('unexpected-denial')
    } else if (suffix === '/video') {
      if (response.headers.get('cache-control') !== 'private, no-store')
        throw Error('not-private')
      await response.body.cancel()
    }
    results.push(response.status)
  }
  at('check-player-isolation')
  if (data.expected !== 200 && document.querySelector('video, a[download]'))
    throw Error('unexpected-player')
  at('check-record-result')
  const report = observed(await local(control + '/report', {
    method: 'POST', headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify({ results }),
  }))
  if (!report.ok) throw Error('report-rejected')
  el('result').textContent = data.role + ': browser and direct API ' + data.expected + ' on all three endpoints.'
  if (data.role === 'member') {
    if (!played) {
      el('extra').textContent = 'Play the video briefly before recording download proof.'
    }
    const holder = el('extra')
    const label = document.createElement('label')
    label.textContent = ' After using Download authorized MP4, select that local file to check it: '
    const file = document.createElement('input')
    file.type = 'file'; file.accept = 'video/mp4,.mp4'
    file.onchange = async () => {
      let phase = 'media-local-playback-and-file'
      let status = 'not-observed'
      try {
        const f = file.files[0]
        if (!f || f.size > 100_000_000 || !played) throw Error('play-or-download-incomplete')
        const bytes = new Uint8Array(await crypto.subtle.digest('SHA-256', await f.arrayBuffer()))
        const sha = Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('')
        phase = 'media-get-api-token'
        const auth = await bearer()
        phase = 'media-verify-download'
        const answer = await local(control + '/media-proof', {
          method: 'POST', headers: { ...auth, 'Content-Type': 'application/json' },
          body: JSON.stringify({ sha, played }),
        })
        status = safeStatus(answer.status)
        if (!answer.ok) throw Error('media-proof-failed')
        holder.textContent = 'Playback observed. Download matches the private stored MP4.'
      } catch { showStop(holder, phase, status) }
    }
    label.append(file)
    holder.append(label)
  }
})
