// Development-only Chromium browser race gate. No live OAuth or backend.
import { createServer } from 'vite'
import react from '@vitejs/plugin-react'
import { spawn } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { setTimeout as sleep } from 'node:timers/promises'

process.env.VITE_ENABLE_AUTHENTICATED_JOURNEY = 'true'
process.env.VITE_VIDEO_PROJECT_ID = 'c7e6a845-cb0c-468c-9a18-1e9df0b20545'
process.env.VITE_VIDEO_API_ORIGIN = ''
const fakeAuth = resolve('e2e/fakeAuth.mjs')
const server = await createServer({
  configFile: false,
  root: process.cwd(),
  plugins: [react()],
  resolve: { alias: [{find: /^\.\/entraAuth$/, replacement: fakeAuth}] },
  server: { host: '127.0.0.1', port: 0, strictPort: false, hmr: false },
  logLevel: 'error',
})

// Diagnostics begin only after Vite initialization. Never serialize caught values.
let primaryStage = null
let fixtureState = 'NOT_OBSERVED'
let profileErrorCode = 'UNKNOWN'
function classifyProfileError(error) {
  try {
    const code = Object.getOwnPropertyDescriptor(error, 'code')?.value
    return ['EBUSY', 'EMFILE', 'ENFILE', 'ENOTEMPTY', 'EPERM', 'EACCES', 'EROFS', 'EIO'].includes(code) ? code : 'UNKNOWN'
  } catch { return 'UNKNOWN' }
}
const cleanup = { terminate: 'NOT_COMPLETED', pipes: 'NOT_COMPLETED', close: 'NOT_COMPLETED', server: 'NOT_COMPLETED', profile: 'NOT_COMPLETED' }
const stages = new Set(['SETUP', 'LISTEN', 'LOOPBACK', 'SPAWN', 'EARLY_EXIT', 'PIPE', 'FRAME_LIMIT', 'FRAME_PARSE', 'CDP_COMMAND', 'INTERCEPTION', 'EGRESS', 'NAVIGATION', 'EVALUATION', 'MARKER', 'TIMEOUT', 'FIXTURE_FAIL', 'CLEANUP', 'UNKNOWN'])
function recordFailure(stage) {
  if (primaryStage === null) primaryStage = stages.has(stage) ? stage : 'UNKNOWN'
}
function observed(stage, action) {
  try { return action() } catch (error) { recordFailure(stage); throw error }
}
async function observedAsync(stage, action) {
  try { return await action() } catch (error) { recordFailure(stage); throw error }
}
function cleanupSync(name, action) {
  try { action(); cleanup[name] = 'OK' } catch (error) {
    cleanup[name] = 'FAILED'; recordFailure('CLEANUP'); throw error
  }
}
async function cleanupAsync(name, action) {
  try { await action(); cleanup[name] = 'OK' } catch (error) {
    if (name === 'profile') profileErrorCode = classifyProfileError(error)
    cleanup[name] = 'FAILED'; recordFailure('CLEANUP'); throw error
  }
}

// Chromium's debugger pipe is private to this process, with no listening port.
// Wait for the fixture itself, not an arbitrary snapshot of its DOM.
async function checkFixture(browser, origin) {
  const input = browser.stdio[3]
  const output = browser.stdio[4]
  const requests = new Map()
  let id = 0
  let buffer = ''
  let sessionId
  let failure
  let fail
  const failed = new Promise((_, reject) => { fail = reject })
  let observing = true
  const stop = (stage, message) => {
    if (failure) return
    if (observing) recordFailure(stage)
    failure = new Error(message)
    fail(failure)
    for (const { reject } of requests.values()) reject(failure)
    requests.clear()
  }
  const send = (method, params = {}, session) => new Promise((resolve, reject) => {
    if (failure) { reject(failure); return }
    requests.set(++id, { resolve, reject, method })
    observed('PIPE', () => input.write(JSON.stringify({ id, method, params, sessionId: session }) + '\0'))
  })
  browser.once('error', () => stop('SPAWN', 'Could not start Chromium'))
  browser.once('exit', () => stop('EARLY_EXIT', 'Chromium exited before a fixture result'))
  input.on('error', () => stop('PIPE', 'Chromium debugger pipe failed'))
  output.on('error', () => stop('PIPE', 'Chromium debugger pipe failed'))
  output.setEncoding('utf8')
  output.on('data', chunk => {
    buffer += chunk
    if (buffer.length > 1024 * 1024) { stop('FRAME_LIMIT', 'Chromium debugger message exceeded limit'); return }
    let end
    while (!failure && (end = buffer.indexOf('\0')) !== -1) {
      const frame = buffer.slice(0, end)
      buffer = buffer.slice(end + 1)
      let message
      try { message = JSON.parse(frame) } catch { stop('FRAME_PARSE', 'Invalid Chromium debugger response'); return }
      if (!message || typeof message !== 'object') { stop('FRAME_PARSE', 'Invalid Chromium debugger response'); return }
      if (message.id) {
        const request = requests.get(message.id)
        if (!request) continue
        requests.delete(message.id)
        if (message.error) {
          if (observing) recordFailure(['Fetch.continueRequest', 'Fetch.failRequest'].includes(request.method) ? 'INTERCEPTION' : 'CDP_COMMAND')
          request.reject(new Error('Chromium debugger command failed'))
        }
        else request.resolve(message.result)
      } else if (message.method === 'Fetch.requestPaused' && message.sessionId === sessionId) {
        const { requestId, request } = message.params ?? {}
        let permitted = false
        try { permitted = new URL(request.url).origin === origin } catch { /* Deny invalid URLs. */ }
        const method = permitted ? 'Fetch.continueRequest' : 'Fetch.failRequest'
        const params = permitted ? { requestId } : { requestId, errorReason: 'BlockedByClient' }
        send(method, params, sessionId).catch(() => stop('INTERCEPTION', 'Chromium request interception failed'))
        if (!permitted) stop('EGRESS', 'Browser fixture attempted a non-loopback request')
      }
    }
  })

  const timeout = setTimeout(() => stop('TIMEOUT', 'Timed out waiting for the Chromium fixture result'), 90000)
  try {
    await Promise.race([failed, (async () => {
      const { targetId } = await send('Target.createTarget', { url: 'about:blank' })
      ;({ sessionId } = await send('Target.attachToTarget', { targetId, flatten: true }))
      await send('Fetch.enable', { patterns: [{ urlPattern: '*' }] }, sessionId)
      const navigation = await send('Page.navigate', { url: origin + '/e2e/session-race.html' }, sessionId)
      if (navigation.errorText) { recordFailure('NAVIGATION'); throw new Error('Could not load the Chromium fixture') }
      while (!failure) {
        const evaluation = await send('Runtime.evaluate', {
          expression: `(() => {
            const markers = document.querySelectorAll('#race-result');
            if (!markers.length) return 'PENDING';
            if (markers.length !== 1 || markers[0].tagName !== 'PRE') return 'INVALID';
            const { result } = markers[0].dataset;
            if (!(result === 'PASS' || result === 'FAIL') ||
                !markers[0].textContent.startsWith(result + ': ')) return 'INVALID';
            if (result === 'PASS') return result;
            const allowed = ['C42_MOUNT', 'C42_FOCUS', 'C42_ACCOUNT_SWITCH', 'C42_PROJECT_SWITCH', 'C42_PRIVATE_MEDIA', 'C42_LOGOUT', 'C42_POLL_PAUSE', 'C42_POLL_RESUME', 'C42_CANCELLED', 'C42_CREATE_HOLD', 'C42_CREATE_RECOVERY', 'C42_QUEUE_RECOVERY', 'C42_QUEUE_FAILURE', 'C42_DENIED_RECOVERY', 'C42_FINAL_ACCOUNT'];
            const code = markers[0].dataset.checkpoint;
            return 'FAIL:' + (allowed.includes(code) ? code : 'C42_UNKNOWN');
          })()`,
          returnByValue: true,
        }, sessionId)
        if (evaluation.exceptionDetails) { recordFailure('EVALUATION'); throw new Error('Could not read the Chromium fixture result') }
        const result = evaluation.result?.value
        if (result === 'PASS') { fixtureState = 'PASS'; return }
        const failures = ['C42_MOUNT', 'C42_FOCUS', 'C42_ACCOUNT_SWITCH', 'C42_PROJECT_SWITCH', 'C42_PRIVATE_MEDIA', 'C42_LOGOUT', 'C42_POLL_PAUSE', 'C42_POLL_RESUME', 'C42_CANCELLED', 'C42_CREATE_HOLD', 'C42_CREATE_RECOVERY', 'C42_QUEUE_RECOVERY', 'C42_QUEUE_FAILURE', 'C42_DENIED_RECOVERY', 'C42_FINAL_ACCOUNT', 'C42_UNKNOWN'].map(code => 'FAIL:' + code)
        if (failures.includes(result)) {
          fixtureState = 'FAIL'
          recordFailure('FIXTURE_FAIL')
          failedCheckpoint = result.slice(5)
          throw new Error('Chromium session-race fixture reported failure')
        }
        if (result !== 'PENDING') {
          fixtureState = 'INVALID'; recordFailure('MARKER')
          throw new Error('Invalid Chromium fixture result marker')
        }
        fixtureState = 'PENDING'
        await sleep(100)
      }
    })()])
  } catch (error) {
    recordFailure('UNKNOWN'); throw error
  } finally {
    observing = false
    clearTimeout(timeout)
  }
}

let failedCheckpoint = null
let browser
let browserClosed
async function runBrowserFixture() {
const profile = await observedAsync('SETUP', () => mkdtemp(join(tmpdir(), 'c42-browser-')))
try {
  await observedAsync('LISTEN', () => server.listen())
  const address = observed('LOOPBACK', () => server.httpServer.address())
  if (address?.address !== '127.0.0.1') { recordFailure('LOOPBACK'); throw new Error('Browser fixture must listen on IPv4 loopback') }
  const origin = 'http://127.0.0.1:' + address.port
  const chrome = process.env.CHROME_BIN || 'google-chrome'
  browser = observed('SPAWN', () => spawn(chrome, [
    '--headless=new', '--no-sandbox', '--disable-gpu',
    '--disable-dev-shm-usage', '--no-first-run', '--no-default-browser-check',
    '--disable-background-networking', '--disable-extensions',
    '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1', '--no-proxy-server',
    '--user-data-dir=' + profile, '--remote-debugging-pipe', 'about:blank',
  ], {stdio:['ignore','ignore','ignore','pipe','pipe']}))
  browserClosed = new Promise(resolve => browser.once('close', resolve))
  await checkFixture(browser, origin)
} catch (error) {
  recordFailure('UNKNOWN'); throw error
} finally {
  try {
    if (browser && browser.exitCode === null && browser.signalCode === null) {
      cleanupSync('terminate', () => browser.kill('SIGKILL'))
    } else { cleanup.terminate = 'NOT_REQUIRED' }
    if (browserClosed) {
      // Descendants inheriting a pipe must not prevent teardown.
      cleanupSync('pipes', () => {
        browser.stdio[3]?.destroy()
        browser.stdio[4]?.destroy()
      })
      const deadline = new AbortController()
      try {
        await cleanupAsync('close', () => Promise.race([
          browserClosed,
          sleep(5000, null, { signal: deadline.signal }).then(() => {
            throw new Error('Chromium did not close during cleanup')
          }),
        ]))
      } finally {
        deadline.abort()
      }
    } else { cleanup.pipes = cleanup.close = 'NOT_REQUIRED' }
  } finally {
    try { await cleanupAsync('server', () => server.close()) } finally { await cleanupAsync('profile', () => rm(profile, {recursive:true,force:true,maxRetries:3,retryDelay:100})) }
  }
}
}
try {
  await runBrowserFixture()
  console.log('PASS: headless Chromium account-switch, stale request, revoked URL, logout race')
} catch {
  // Never forward an exception, stack or browser text to the terminal.
  recordFailure('UNKNOWN')
  console.error(failedCheckpoint || 'C42_RUNNER_FAILURE')
  console.error('C42_STAGE_' + primaryStage)
  console.error('C42_FIXTURE_' + fixtureState)
  for (const name of ['terminate', 'pipes', 'close', 'server', 'profile']) {
    console.error('C42_CLEANUP_' + name.toUpperCase() + '_' + cleanup[name])
  }
  if (cleanup.profile === 'FAILED') console.error('C42_PROFILE_ERROR_' + profileErrorCode)
  process.exitCode = 1
}
