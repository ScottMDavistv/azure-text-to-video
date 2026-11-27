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
  const stop = message => {
    if (failure) return
    failure = new Error(message)
    fail(failure)
    for (const { reject } of requests.values()) reject(failure)
    requests.clear()
  }
  const send = (method, params = {}, session) => new Promise((resolve, reject) => {
    if (failure) { reject(failure); return }
    requests.set(++id, { resolve, reject })
    input.write(JSON.stringify({ id, method, params, sessionId: session }) + '\0')
  })
  browser.once('error', () => stop('Could not start Chromium'))
  browser.once('exit', () => stop('Chromium exited before a fixture result'))
  input.on('error', () => stop('Chromium debugger pipe failed'))
  output.on('error', () => stop('Chromium debugger pipe failed'))
  output.setEncoding('utf8')
  output.on('data', chunk => {
    buffer += chunk
    if (buffer.length > 1024 * 1024) { stop('Chromium debugger message exceeded limit'); return }
    let end
    while (!failure && (end = buffer.indexOf('\0')) !== -1) {
      const frame = buffer.slice(0, end)
      buffer = buffer.slice(end + 1)
      let message
      try { message = JSON.parse(frame) } catch { stop('Invalid Chromium debugger response'); return }
      if (!message || typeof message !== 'object') { stop('Invalid Chromium debugger response'); return }
      if (message.id) {
        const request = requests.get(message.id)
        if (!request) continue
        requests.delete(message.id)
        if (message.error) request.reject(new Error('Chromium debugger command failed'))
        else request.resolve(message.result)
      } else if (message.method === 'Fetch.requestPaused' && message.sessionId === sessionId) {
        const { requestId, request } = message.params ?? {}
        let permitted = false
        try { permitted = new URL(request.url).origin === origin } catch { /* Deny invalid URLs. */ }
        const method = permitted ? 'Fetch.continueRequest' : 'Fetch.failRequest'
        const params = permitted ? { requestId } : { requestId, errorReason: 'BlockedByClient' }
        send(method, params, sessionId).catch(() => stop('Chromium request interception failed'))
        if (!permitted) stop('Browser fixture attempted a non-loopback request')
      }
    }
  })

  const timeout = setTimeout(() => stop('Timed out waiting for the Chromium fixture result'), 90000)
  try {
    await Promise.race([failed, (async () => {
      const { targetId } = await send('Target.createTarget', { url: 'about:blank' })
      ;({ sessionId } = await send('Target.attachToTarget', { targetId, flatten: true }))
      await send('Fetch.enable', { patterns: [{ urlPattern: '*' }] }, sessionId)
      const navigation = await send('Page.navigate', { url: origin + '/e2e/session-race.html' }, sessionId)
      if (navigation.errorText) throw new Error('Could not load the Chromium fixture')
      while (!failure) {
        const evaluation = await send('Runtime.evaluate', {
          expression: `(() => {
            const markers = document.querySelectorAll('#race-result');
            if (!markers.length) return 'PENDING';
            if (markers.length !== 1 || markers[0].tagName !== 'PRE') return 'INVALID';
            const { result } = markers[0].dataset;
            return (result === 'PASS' || result === 'FAIL') &&
              markers[0].textContent.startsWith(result + ': ') ? result : 'INVALID';
          })()`,
          returnByValue: true,
        }, sessionId)
        if (evaluation.exceptionDetails) throw new Error('Could not read the Chromium fixture result')
        const result = evaluation.result?.value
        if (result === 'PASS') return
        if (result === 'FAIL') throw new Error('Chromium session-race fixture reported failure')
        if (result !== 'PENDING') throw new Error('Invalid Chromium fixture result marker')
        await sleep(100)
      }
    })()])
  } finally {
    clearTimeout(timeout)
  }
}

let browser
let browserClosed
const profile = await mkdtemp(join(tmpdir(), 'c42-browser-'))
try {
  await server.listen()
  const address = server.httpServer.address()
  if (address?.address !== '127.0.0.1') throw new Error('Browser fixture must listen on IPv4 loopback')
  const origin = 'http://127.0.0.1:' + address.port
  const chrome = process.env.CHROME_BIN || 'google-chrome'
  browser = spawn(chrome, [
    '--headless=new', '--no-sandbox', '--disable-gpu',
    '--disable-dev-shm-usage', '--no-first-run', '--no-default-browser-check',
    '--disable-background-networking', '--disable-extensions',
    '--host-resolver-rules=MAP * ~NOTFOUND', '--no-proxy-server',
    '--user-data-dir=' + profile, '--remote-debugging-pipe', 'about:blank',
  ], {stdio:['ignore','ignore','ignore','pipe','pipe']})
  browserClosed = new Promise(resolve => browser.once('close', resolve))
  await checkFixture(browser, origin)
} finally {
  try {
    if (browser && browser.exitCode === null && browser.signalCode === null) {
      browser.kill('SIGKILL')
    }
    if (browserClosed) {
      // Descendants inheriting a pipe must not prevent teardown.
      browser.stdio[3]?.destroy()
      browser.stdio[4]?.destroy()
      const deadline = new AbortController()
      try {
        await Promise.race([
          browserClosed,
          sleep(5000, null, { signal: deadline.signal }).then(() => {
            throw new Error('Chromium did not close during cleanup')
          }),
        ])
      } finally {
        deadline.abort()
      }
    }
  } finally {
    try { await server.close() } finally { await rm(profile, {recursive:true,force:true}) }
  }
}
console.log('PASS: headless Chromium account-switch, stale request, revoked URL, logout race')
