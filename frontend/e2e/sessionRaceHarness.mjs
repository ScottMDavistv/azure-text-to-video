// Executes inside a real headless Chromium page against the React UI.
// All authentication and API responses are in-memory mocks; no Azure or Entra.
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))
const assert = (condition, message) => { if (!condition) throw new Error(message) }
async function until(test, label, timeout = 6000) {
  const start = Date.now()
  while (Date.now() - start < timeout) {
    if (test()) return
    await sleep(40)
  }
  throw new Error('Timeout: ' + label)
}
function click(label) {
  const button = [...document.querySelectorAll('button')]
    .find(b => b.textContent?.includes(label) && !b.disabled)
  assert(button, 'Button not found: ' + label)
  button.click()
}
function changeText(selector, value) {
  const input = document.querySelector(selector)
  assert(input, 'Missing input ' + selector)
  const proto = input instanceof HTMLTextAreaElement
    ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype
  Object.getOwnPropertyDescriptor(proto, 'value').set.call(input, value)
  input.dispatchEvent(new Event('input', {bubbles:true}))
}
function result(text, ok) {
  const pre = document.createElement('pre')
  pre.id = 'race-result'
  pre.dataset.result = ok ? 'PASS' : 'FAIL'
  pre.textContent = (ok ? 'PASS: ' : 'FAIL: ') + text
  document.body.append(pre)
}
async function run() {
  await until(() => document.querySelector('.auth-tools'), 'App mounted')
  click('Sign in with Microsoft')
  await until(() => document.body.textContent.includes('Signed in: Account A'), 'Account A signed in')
  await until(() => document.querySelector('#secure-video-prompt'), 'SecureStudio mounted')
  // Keyboard users receive a focusable error summary and associated prompt limit.
  document.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
  await until(() => document.activeElement?.id === 'secure-request-error', 'validation error focus')
  assert(document.querySelector('#secure-video-prompt').getAttribute('aria-describedby') === 'secure-prompt-limit', 'Prompt limit association absent')
  changeText('#secure-video-prompt', 'offline browser test')
  // Two submit events before React updates disabled state must yield one mutation.
  const form = document.querySelector('form')
  form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
  form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
  await until(() => window.__racePending.length === 1, 'pending A submission')
  // Switch accounts before the A response completes. Its controller must abort,
  // and a late response must not resurrect the old job.
  window.__raceAccount = {homeAccountId:'account-B', name:'Account B', username:'b@example.test'}
  await until(() => document.body.textContent.includes('Signed in: Account B'), 'Account B switch')
  await until(() => window.__racePending[0].onAbort && !document.querySelector('video'), 'A request aborted')
  assert(document.body.textContent.includes('No authorized job in this session.'), 'A job leaked into B session')
  assert(!document.querySelector('video'), 'A video remained after switching to B')

  // An in-flight project-A submission must not survive a project-ID edit.
  changeText('#secure-video-prompt', 'offline browser test')
  click('Submit authenticated job')
  await until(() => window.__racePending.length >= 2, 'pending project submission')
  changeText('#project-id', 'b7c6a845-cb0c-468c-9a18-1e9df0b20545')
  await until(() => document.querySelector('#project-id')?.value.startsWith('b7c6'), 'project changed')
  assert(document.body.textContent.includes('No authorized job in this session.'), 'Previous project job leaked')
  changeText('#project-id', 'c7e6a845-cb0c-468c-9a18-1e9df0b20545')

  window.__raceMode = 'immediate'
  changeText('#secure-video-prompt', 'offline browser test')
  click('Submit authenticated job')
  await until(() => document.body.textContent.includes('Status: completed'), 'B completed job')
  click('Retrieve private MP4')
  await until(() => document.querySelector('video[src^="blob:"]'), 'B private MP4 URL')
  const video = document.querySelector('video')
  const oldUrl = video.src
  window.__raceAccount = {homeAccountId:'account-C', name:'Account C', username:'c@example.test'}
  await until(() => document.body.textContent.includes('Signed in: Account C'), 'Account C switch')
  await until(() => !document.querySelector('video'), 'B video removed')
  assert(window.__raceRevoked.includes(oldUrl), 'Old Blob URL not revoked on account change')
  assert(document.body.textContent.includes('No authorized job in this session.'), 'B job leaked into C session')

  window.__raceMode = 'held'
  changeText('#secure-video-prompt', 'offline browser test')
  click('Submit authenticated job')
  await until(() => window.__racePending.length >= 3, 'pending C submission')
  click('Sign out')
  await until(() => !document.body.textContent.includes('Signed in: Account C'), 'logout clears C')
  assert(!document.querySelector('video'), 'Video remained after logout')
  assert(document.body.textContent.includes('No authorized job in this session.'), 'Job remained after logout')
  assert(window.__raceRequests.every(r => r.auth?.startsWith('Bearer offline-mocked-')),
    'Unexpected API credential or missing bearer token')
  // Three failed reads pause automatic work, retaining the job. Resuming only reads.
  window.__raceMode = 'poll-failure'
  click('Sign in with Microsoft')
  await until(() => document.querySelector('#secure-video-prompt') && document.body.textContent.includes('Signed in:'), 'new fixture session')
  changeText('#secure-video-prompt', 'offline bounded polling test')
  click('Submit authenticated job')
  await until(() => document.body.textContent.includes('Automatic status checks paused'), 'bounded poll failure', 10000)
  const pausedRequests = window.__raceRequests.length
  const mutations = window.__raceRequests.filter(r => r.method === 'POST').length
  await sleep(3200)
  assert(window.__raceRequests.length === pausedRequests, 'Polling continued after pause')
  assert(document.body.textContent.includes('Status: created'), 'Failed observation invented job failure')
  window.__raceMode = 'immediate'
  click('Resume status checks')
  await until(() => document.body.textContent.includes('Status: completed'), 'explicit status resume')
  assert(window.__raceRequests.filter(r => r.method === 'POST').length === mutations, 'Status resume retried a mutation')
  result('Bounded polling/resume; Account A->B late submit, project switch, B->C MP4 URL revocation, C logout in-flight', true)
}
run().catch(error => result(error?.stack || String(error), false))
