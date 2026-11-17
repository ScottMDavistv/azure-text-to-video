// Shared C1 browser security guards (PR #74 review Q5). Imported by
// entra-c1.spec.mjs and intended for the C1 harness supplemental spec, so the
// egress firewall, allowlists, build binding and storage policy are defined
// and reviewed once. Importing this module registers no tests.
import { expect } from '@playwright/test'
import { execFileSync } from 'node:child_process'

export const live = process.env.C1_LIVE === '1'
export const sha = execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim()
export const checkoutDirty = Boolean(execFileSync('git', ['status', '--porcelain', '--untracked-files=all'], { encoding: 'utf8' }).trim())

export const allowedHosts = new Set([
  'localhost', '127.0.0.1', 'login.microsoftonline.com', 'login.microsoft.com',
  'login.live.com', 'aadcdn.msftauth.net', 'aadcdn.msauth.net',
  'logincdn.msftauth.net', 'secure.aadcdn.microsoftonline-p.com',
])

export const allowedLocalOrigins = new Set(['http://localhost:5173', 'http://127.0.0.1:5173'])
export const allowedHmrOrigins = new Set(['ws://localhost:5173', 'ws://127.0.0.1:5173'])
export function allowedRequest(url) {
  // Remote hosts: exact hostname, HTTPS, default port only (no :8443 etc.).
  return allowedLocalOrigins.has(url.origin) || (url.protocol === 'https:' && url.port === '' && allowedHosts.has(url.hostname) && !['localhost', '127.0.0.1'].includes(url.hostname))
}
// Diagnostics record scheme://host[:port] only; never path, query or fragment.
export function originLabel(url) {
  return url.protocol + '//' + url.host
}

export async function observeContext(context) {
  const problems = []
  const requestHosts = new Set()
  const credentialPattern = /Bearer\s+[\w.-]{24,}|eyJ[A-Za-z0-9_-]{20,}\.|client_secret\s*[:=]/i
  const listen = page => {
    page.on('console', message => {
      if (credentialPattern.test(message.text())) problems.push('Credential-like browser console output')
    })
    page.on('pageerror', error => {
      if (credentialPattern.test(error.message)) problems.push('Credential-like browser page error')
    })
    page.on('websocket', socket => {
      const url = new URL(socket.url())
      if (!allowedHmrOrigins.has(url.origin)) problems.push('Unauthorized WebSocket origin ' + originLabel(url))
    })
  }
  for (const page of context.pages()) listen(page)
  context.on('page', listen)
  context.on('request', request => {
    const url = new URL(request.url())
    requestHosts.add(url.hostname)
    if (!allowedRequest(url)) problems.push('Unexpected request origin ' + originLabel(url))
    if (!allowedRequest(url) && request.headers().authorization) problems.push('Authorization header to unauthorized origin ' + originLabel(url))
  })
  await context.routeWebSocket(/.*/, async route => {
    const url = new URL(route.url())
    if (allowedHmrOrigins.has(url.origin)) {
      await route.connectToServer()
    } else {
      problems.push('Blocked unauthorized WebSocket origin ' + originLabel(url))
      route.close()
    }
  })
  await context.route('**/*', async route => {
    const url = new URL(route.request().url())
    const local = allowedLocalOrigins.has(url.origin)
    if ((!live && !local) || (live && !allowedRequest(url))) {
      if (!allowedRequest(url)) problems.push('Blocked unauthorized HTTP origin ' + originLabel(url))
      await route.abort('blockedbyclient')
    } else {
      await route.continue()
    }
  })
  return { problems, requestHosts }
}

export async function assertBuild(page) {
  const served = await page.evaluate(async () => {
    const { c1BuildSha, c1BuildDirty } = await import('/src/c1BuildInfo.ts')
    return { sha: c1BuildSha, dirty: c1BuildDirty }
  })
  expect(served.sha).toBe(sha)
  expect(served.dirty).toBe(checkoutDirty)
}

export async function assertMemoryOnly(page, context) {
  const result = await page.evaluate(async () => {
    const local = Object.entries(localStorage)
    const session = Object.entries(sessionStorage)
    const jwt = /eyJ[A-Za-z0-9_-]{20,}\./
    const forbidden = /token|secret|bearer/i
    const allowedSessionKeys = new Set(['msal.interaction.status', 'msal.interaction.inProgress'])
    return {
      localEmpty: local.length === 0,
      sessionSafe: session.every(([key, value]) => allowedSessionKeys.has(key) && value.length < 128 && !forbidden.test(value) && !jwt.test(value)),
      domSafe: !jwt.test(document.body.innerText),
      indexedDbEmpty: (await indexedDB.databases()).length === 0,
    }
  })
  const localCookies = await context.cookies(['http://localhost:5173', 'http://127.0.0.1:5173'])
  expect(localCookies).toEqual([])
  expect(result).toEqual({ localEmpty: true, sessionSafe: true, domSafe: true, indexedDbEmpty: true })
}
