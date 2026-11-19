// C1 L-2: independent-of-HTTP-routing Chromium UDP/STUN positive and negative controls.
// This test must never claim success just because the unprotected browser sent zero packets.
import assert from 'node:assert/strict'
import { chromium } from '@playwright/test'
import { createSocket } from 'node:dgram'
import { createServer } from 'node:http'
import { networkInterfaces } from 'node:os'
import { once } from 'node:events'
import { WEBRTC_UDP_POLICY_ARG } from './c1-webrtc-policy.mjs'

if (process.env.C1_LIVE === '1') throw new Error('L-2 probe refuses live Entra mode')
if (process.env.C1_MUTATION_MODE === '1') throw new Error('L-2 probe refuses mutation mode')
const iface = Object.values(networkInterfaces()).flat().find(x => x && x.family === 'IPv4' && !x.internal)?.address
if (!iface) throw new Error('L-2 NOT VERIFIED: no non-loopback IPv4 interface for positive-control STUN probe')
const timeout = Number(process.env.C1_UDP_PROBE_TIMEOUT_MS || 4500)
if (!Number.isInteger(timeout) || timeout < 2500 || timeout > 10000) throw new Error('L-2 invalid bounded probe timeout')
const sock = createSocket('udp4')
const http = createServer((_req, res) => {
  res.writeHead(200, { 'content-type': 'text/html', 'cache-control': 'no-store' })
  res.end('<!doctype html><title>C1 STUN probe</title>')
})
const udpPackets = []
sock.on('message', (data, remote) => {
  // RFC 8489: Binding Request (0x0001), valid STUN magic cookie (0x2112a442).
  if (data.length >= 20 && data.readUInt16BE(0) === 1 && data.readUInt32BE(4) === 0x2112a442) {
    udpPackets.push({ from: remote.address, bytes: data.length })
  }
})
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))

async function checkLaunchArguments(browser, expected) {
  const cdp = await browser.newBrowserCDPSession()
  try {
    const commandLine = await cdp.send('Browser.getBrowserCommandLine')
    const flags = commandLine.arguments.filter(a => a.startsWith('--force-webrtc-ip-handling-policy='))
    assert.deepEqual(flags, expected ? [WEBRTC_UDP_POLICY_ARG] : [], 'actual Chromium command line does not have the expected WebRTC policy')
  } finally { await cdp.detach() }
}

async function gather(page, stunPort) {
  return await page.evaluate(async ({ host, port, timeoutMs }) => {
    const pc = new RTCPeerConnection({ iceServers: [{ urls: 'stun:' + host + ':' + port }], iceTransportPolicy: 'all' })
    try {
      pc.createDataChannel('offline-c1-stun-probe')
      await pc.setLocalDescription(await pc.createOffer())
      return await Promise.race([
        new Promise(resolve => {
          if (pc.iceGatheringState === 'complete') resolve('complete')
          else pc.addEventListener('icegatheringstatechange', () => {
            if (pc.iceGatheringState === 'complete') resolve('complete')
          })
        }),
        new Promise(resolve => setTimeout(() => resolve('timeout'), timeoutMs)),
      ])
    } finally { pc.close() }
  }, { host: iface, port: stunPort, timeoutMs: timeout })
}

async function exerciseBrowser(guarded, stunPort, webPort) {
  const browser = await chromium.launch({ args: guarded ? [WEBRTC_UDP_POLICY_ARG] : [] })
  try {
    await checkLaunchArguments(browser, guarded)
    // Browser policy must apply to newly created, independent contexts as well.
    for (let i = 0; i < 2; i++) {
      const context = await browser.newContext()
      try {
        const page = await context.newPage()
        await page.goto('http://127.0.0.1:' + webPort + '/', { waitUntil: 'domcontentloaded' })
        const state = await gather(page, stunPort)
        assert.ok(['complete', 'timeout'].includes(state), 'unexpected ICE state')
      } finally { await context.close() }
    }
    await delay(300)
    return browser.version()
  } finally { await browser.close() }
}

let success = false
try {
  await new Promise((resolve, reject) => sock.bind(0, '0.0.0.0', err => err ? reject(err) : resolve()))
  http.listen(0, '127.0.0.1')
  await once(http, 'listening')
  const stunPort = sock.address().port
  const webPort = http.address().port
  const version = await exerciseBrowser(false, stunPort, webPort)
  console.log('C1 L-2 unrestricted Chromium ' + version + ': STUN binding requests=' + udpPackets.length)
  assert.ok(udpPackets.length > 0, 'L-2 NOT VERIFIED: unrestricted Chromium did not deliver STUN to positive-control sink')
  udpPackets.length = 0
  await exerciseBrowser(true, stunPort, webPort)
  console.log('C1 L-2 protected Chromium: STUN binding requests=' + udpPackets.length)
  assert.equal(udpPackets.length, 0, 'L-2 FAIL: guarded Chromium delivered STUN UDP')
  success = true
  console.log('C1 L-2 PASS: unrestricted STUN delivered; protected contexts delivered zero packets')
} finally {
  await new Promise(resolve => http.close(resolve))
  sock.close()
}
if (!success) throw new Error('C1 L-2 NOT VERIFIED')
