// Regression tests: evidence sanitization, verdict logic, and equivalence of
// the supplemental spec's replicated security helpers with the approved suite.
import test from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, writeFileSync, readFileSync, rmSync, mkdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  assertSanitized, parseMeta, parsePhases, summarizeReport, evaluate,
  renderEvidence, renderReviewPackage, scanArtifacts, OFFLINE_EXPECTED,
} from '../lib/c1-evidence.mjs'

const here = dirname(fileURLToPath(import.meta.url))
const SHA = '6b45d0acad10065cb825c12c4544cabdad3441d7'
const AUTH = '73fe6d2a8aeeef97d34ba23c83fcce5e63f649cc'

const spec = (title, status, extra = {}) => ({
  title,
  tests: [{ annotations: extra.annotations || [], results: status === 'none' ? [] : [{
    status, errors: (extra.errors || []).map(message => ({ message })),
    stdout: (extra.stdout || []).map(text => ({ text })), annotations: [],
  }] }],
})
const report = specs => ({ suites: [{ title: 'entra-c1.spec.mjs', specs, suites: [] }] })
const offlineAllPass = () => report([
  ...OFFLINE_EXPECTED.map(t => spec(t + ' x', 'passed')),
  spec('LIVE: popup login, validated claims, silent acquisition', 'skipped'),
])
const meta = {
  AUTH_SHA: AUTH, PW_SHA: SHA, HARNESS_SHA: 'a'.repeat(40), HARNESS_CLEAN: 'true',
  CHECKOUT_VERIFIED: 'PASS', LOCALHOST: 'PASS', CHECKS: 'harness_clean', MODE: 'supervised',
}
const ran = rc => ({ state: 'ran', start: '2026-11-17T00:00:00Z', end: '2026-11-17T00:01:00Z', rc: String(rc) })
const allPhases = () => ({
  'static-mutation': ran(0), offline: ran(0), unconfigured: ran(0),
  'consent-denial': ran(0), live: ran(0), 'cancelled-logout': ran(0),
})
const allReports = () => ({
  offline: summarizeReport(offlineAllPass()),
  unconfigured: summarizeReport(report([spec('M-1: missing SPA configuration is fail-closed', 'passed')])),
  live: summarizeReport(report([
    spec('LIVE: popup login, validated claims, silent acquisition and confirmed cache-clearing logout', 'passed'),
    spec('LIVE: deliberately cancelled popup returns MSAL user_cancelled, not a generic failure', 'passed'),
  ])),
  'consent-denial': summarizeReport(report([spec('SUPERVISED consent denial: declined consent fails closed', 'passed',
    { stdout: ['C1 RESULT case=consent-denial state=failed code=access_denied aadsts=AADSTS65004'] })])),
  'cancelled-logout': summarizeReport(report([spec('SUPERVISED cancelled logout: local sign-out is fail-closed', 'passed',
    { stdout: ['C1 RESULT case=cancelled-logout state=succeeded code=none aadsts=none',
      'C1 OBSERVATION case=cancelled-logout ui_signed_in=false msal_account_present=false failure_message_shown=false server_signout_confirmed=false'] })])),
})

test('sanitizer rejects credential, identity and URL material', () => {
  const bad = [
    'token eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.payload',
    'Authorization: Bearer abc', 'user alice@contoso.com signed in',
    'https://login.microsoftonline.com/x/oauth2/v2.0/authorize?client_id=1',
    'http://localhost:5173/#code=abc', 'tenant 773e9051-c546-4b75-9432-90f4f8242660',
    'access_token present', 'refresh_token', 'code_verifier', 'Set-Cookie: ESTSAUTH=1',
    'opaque ' + 'Ab1_'.repeat(13),
  ]
  for (const b of bad) assert.throws(() => assertSanitized(b), /Sanitizer rejected/, b)
})

test('sanitizer accepts SHAs, digests and ordinary evidence text', () => {
  assertSanitized('`' + SHA + '` `' + 'f'.repeat(64) + '` user_cancelled AADSTS65004 login.microsoftonline.com')
  assertSanitized('| R1 | Clean exact-head checkout | **PASS** | - |')
})

test('parseMeta keeps only allowlisted keys with strictly formatted values', () => {
  const m = parseMeta('PW_SHA=' + SHA + '\nBROWSER_VERSION=1.2.3; rm -rf /\nEVIL=1\nMODE=supervised\nAUTH_SHA=short')
  assert.deepEqual(m, { PW_SHA: SHA, MODE: 'supervised' })
})

test('parsePhases ignores malformed and unknown phases', () => {
  const p = parsePhases('offline|ran|2026-11-17T00:00:00Z|2026-11-17T00:00:01Z|0\nevil|ran|x|y|0\nlive|skipped|-|-|-')
  assert.deepEqual(Object.keys(p).sort(), ['live', 'offline'])
})

test('status mapping: passed/failed/timedOut/INCOMPLETE skip/mode skip', () => {
  const s = summarizeReport(report([
    spec('A', 'passed'), spec('B', 'failed'), spec('C', 'timedOut'), spec('D', 'interrupted'),
    spec('E', 'skipped', { annotations: [{ type: 'skip', description: 'INCOMPLETE: popup was closed instead of declining consent' }] }),
    spec('F', 'skipped', { annotations: [{ type: 'skip', description: 'Synthetic offline configuration only' }] }),
    spec('G', 'none'),
  ]))
  assert.deepEqual(s.map(t => t.verdict), ['PASS', 'FAIL', 'FAIL', 'FAIL', 'INCOMPLETE', 'SKIPPED', 'SKIPPED'])
})

test('RESULT lines must match the strict grammar; anything else is ignored', () => {
  const [good, bad] = summarizeReport(report([
    spec('X', 'passed', { stdout: ['C1 RESULT case=consent-denial state=failed code=access_denied aadsts=AADSTS65004'] }),
    spec('Y', 'passed', { stdout: ['C1 RESULT case=consent-denial state=failed code=access_denied aadsts=AADSTS65004 upn=a@b.com'] }),
  ]))
  assert.equal(good.code, 'access_denied'); assert.equal(good.aadsts, 'AADSTS65004')
  assert.equal(bad.code, null)
})

test('blocked hostnames and markers are extracted from errors without retaining messages', () => {
  const [t] = summarizeReport(report([spec('LIVE: x', 'failed', {
    errors: ['expected [] got ["Blocked unauthorized HTTP origin evil.example", "Credential-like browser console output", "https://x/?code=1"]'],
  })]))
  assert.deepEqual(t.hosts, ['evil.example'])
  assert.equal(t.egressMarker, true); assert.equal(t.credentialMarker, true)
  assert.ok(!JSON.stringify(t).includes('code=1'))
})

test('offline-only session is INCOMPLETE, never READY', () => {
  const phases = { 'static-mutation': ran(0), offline: ran(0), unconfigured: ran(0),
    'consent-denial': { state: 'skipped' }, live: { state: 'skipped' }, 'cancelled-logout': { state: 'skipped' } }
  const r = evaluate({ meta, phases, reports: { offline: allReports().offline, unconfigured: allReports().unconfigured }, artifactHits: 0 })
  assert.equal(r.overall, 'INCOMPLETE')
  assert.equal(r.rows.find(x => x.id === 'R3').verdict, 'PASS')
  assert.equal(r.credential, 'INCOMPLETE'); assert.equal(r.egress, 'INCOMPLETE')
})

test('complete supervised session with all rows passing is READY FOR INDEPENDENT REVIEW (not certified)', () => {
  const r = evaluate({ meta, phases: allPhases(), reports: allReports(), artifactHits: 0 })
  assert.equal(r.overall, 'READY FOR INDEPENDENT REVIEW')
  assert.equal(r.rows.find(x => x.id === 'R7').code, 'user_cancelled (asserted)')
  assert.equal(r.rows.find(x => x.id === 'R8').code, 'access_denied / AADSTS65004')
  assert.equal(r.allTests.find(t => t.observation).observation.serverSignoutConfirmed, 'false')
  const ev = assertSanitized(renderEvidence({ meta, phases: allPhases(), result: r, generated: '2026-11-17T00:00:00Z' }))
  assert.match(ev, /never issues C1 certification/)
  assertSanitized(renderReviewPackage({ evidence: ev, meta }))
})

test('a missing or skipped offline test fails R3 (no SKIPPED counted as PASS)', () => {
  const reports = allReports()
  reports.offline = summarizeReport(report(OFFLINE_EXPECTED.slice(1).map(t => spec(t, 'passed'))))
  assert.equal(evaluate({ meta, phases: allPhases(), reports, artifactHits: 0 }).rows.find(x => x.id === 'R3').verdict, 'FAIL')
  reports.offline = summarizeReport(report(OFFLINE_EXPECTED.map((t, i) => spec(t, i === 0 ? 'skipped' : 'passed'))))
  assert.equal(evaluate({ meta, phases: allPhases(), reports, artifactHits: 0 }).overall, 'FAIL')
})

test('phase that ran without a report (config refused dirty tree) is FAIL', () => {
  const reports = allReports(); delete reports.live
  const r = evaluate({ meta, phases: allPhases(), reports, artifactHits: 0 })
  assert.equal(r.rows.find(x => x.id === 'R6').verdict, 'FAIL'); assert.equal(r.overall, 'FAIL')
})

test('token-like temporary artifacts force credential FAIL', () => {
  assert.equal(evaluate({ meta, phases: allPhases(), reports: allReports(), artifactHits: 1 }).credential, 'FAIL')
})

test('egress markers in live tests force egress FAIL; the M6 probe\'s expected blocks do not', () => {
  const reports = allReports()
  reports.offline = summarizeReport(report(OFFLINE_EXPECTED.map(t => spec(t, 'passed',
    t.startsWith('M6') ? { errors: [] } : {}))))
  reports.offline.find(t => t.title.startsWith('M6')).egressMarker = true
  assert.equal(evaluate({ meta, phases: allPhases(), reports, artifactHits: 0 }).egress, 'PASS')
  reports.live[0] = { ...reports.live[0], verdict: 'FAIL', egressMarker: true, hosts: ['cdn.example'] }
  const r = evaluate({ meta, phases: allPhases(), reports, artifactHits: 0 })
  assert.equal(r.egress, 'FAIL'); assert.deepEqual(r.hosts, ['cdn.example'])
})

test('dirty checkout or modified harness fails R1', () => {
  for (const m of [{ ...meta, CHECKOUT_VERIFIED: 'FAIL' }, { ...meta, HARNESS_CLEAN: 'false' }]) {
    assert.equal(evaluate({ meta: m, phases: allPhases(), reports: allReports(), artifactHits: 0 }).rows[0].verdict, 'FAIL')
  }
})

test('artifact scanner finds JWTs and does not follow symlinks', () => {
  const d = mkdtempSync(join(tmpdir(), 'c1-scan-'))
  try {
    mkdirSync(join(d, 'a'))
    writeFileSync(join(d, 'a', 'ok.json'), '{"status":"passed"}')
    assert.equal(scanArtifacts(d), 0)
    writeFileSync(join(d, 'a', 'leak.txt'), 'eyJ' + 'a'.repeat(30) + '.' + 'b'.repeat(30))
    assert.equal(scanArtifacts(d), 1)
  } finally { rmSync(d, { recursive: true, force: true }) }
})

// ---- supplemental spec / config integrity -----------------------------------
const supSpec = readFileSync(join(here, '../supplemental/c1-supervised.spec.mjs'), 'utf8')
const supCfg = readFileSync(join(here, '../supplemental/playwright.supplemental.config.mjs'), 'utf8')

test('supplemental config keeps exact-head, fresh-server and no-artifact rules', () => {
  assert.match(supCfg, /reuseExistingServer:\s*false/)
  assert.match(supCfg, /trace:\s*'off',\s*screenshot:\s*'off',\s*video:\s*'off'/)
  assert.match(supCfg, /expectedSha !== sha/)
  assert.match(supCfg, /dirty\) throw/)
  assert.match(supCfg, /ls-files', '-v'/)
  assert.match(supCfg, /C1_LIVE !== '1'/)
  assert.match(supCfg, /outputDir:\s*join\(tmpdir\(\)/)
})

test('supplemental spec prints only sanitized RESULT/OBSERVATION lines', () => {
  const logs = [...supSpec.matchAll(/console\.log\(([^\n]*)/g)].map(m => m[1])
  assert.equal(logs.length, 2)  // RESULT + OBSERVATION builders only
  assert.ok(logs.every(l => /^'C1 (RESULT|OBSERVATION) case=/.test(l)))
  assert.doesNotMatch(supSpec, /accessToken|idToken|storageState|tracing\.start|screenshot\(|context\.cookies\(\)/)
})

function approvedSpec() {
  try {
    const top = execFileSync('git', ['-C', here, 'rev-parse', '--show-toplevel'], { encoding: 'utf8' }).trim()
    return execFileSync('git', ['-C', top, 'show', SHA + ':frontend/e2e/entra-c1.spec.mjs'], { encoding: 'utf8' })
  } catch { return null }
}
const block = (src, start) => {
  const i = src.indexOf(start)
  if (i < 0) return null
  let depth = 0
  for (let j = src.indexOf('{', i); j < src.length; j++) {
    if (src[j] === '{') depth++
    if (src[j] === '}' && --depth === 0) return src.slice(i, j + 1)
  }
  return null
}

test('replicated allowlists and storage checks are identical to the approved suite', { skip: !approvedSpec() && 'approved SHA not in local history' }, () => {
  const approved = approvedSpec()
  for (const start of ['const allowedHosts = new Set([', 'const allowedLocalOrigins', 'const allowedHmrOrigins', 'function allowedRequest(url)', 'async function assertMemoryOnly(page, context)']) {
    const a = start.startsWith('const') ? approved.split('\n').find(l => l.startsWith(start)) : block(approved, start)
    const b = start.startsWith('const') ? supSpec.split('\n').find(l => l.startsWith(start)) : block(supSpec, start)
    if (start === 'const allowedHosts = new Set([') {
      const grab = src => src.slice(src.indexOf(start), src.indexOf('])', src.indexOf(start)))
      assert.equal(grab(supSpec), grab(approved), start)
    } else assert.equal(b, a, start)
  }
})

test('replicated egress guard matches the approved LIVE branch (route aborts every non-allowlisted request)', { skip: !approvedSpec() && 'approved SHA not in local history' }, () => {
  const approved = approvedSpec()
  const norm = s => s.replace(/\s+/g, ' ')
  for (const frag of [
    "page.on('console'", "page.on('pageerror'", "page.on('websocket'", 'context.routeWebSocket(/.*/',
    "problems.push('Authorization header to unauthorized host ' + url.hostname)",
    "await route.abort('blockedbyclient')", 'const credentialPattern = /Bearer\\s+[\\w.-]{24,}|eyJ[A-Za-z0-9_-]{20,}\\.|client_secret\\s*[:=]/i',
  ]) {
    assert.ok(norm(approved).includes(norm(frag)), 'approved has ' + frag)
    assert.ok(norm(supSpec).includes(norm(frag)), 'supplemental has ' + frag)
  }
  // LIVE semantics: approved aborts when (live && !allowedRequest(url)); supplemental is live-only.
  assert.match(supSpec, /if \(!allowedRequest\(url\)\) \{\s*problems\.push\('Blocked unauthorized HTTP origin ' \+ url\.hostname\)\s*await route\.abort\('blockedbyclient'\)/)
})
