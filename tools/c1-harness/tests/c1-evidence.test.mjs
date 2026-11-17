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
  filterLines, summaryLines,
} from '../lib/c1-evidence.mjs'

const here = dirname(fileURLToPath(import.meta.url))
// L-5: the approved SHAs come from the harness library (single source of truth),
// never from a separate copy in this test.
const libText = readFileSync(join(here, '../lib/c1-lib.sh'), 'utf8')
const pinned = name => (libText.match(new RegExp('^' + name + "='([0-9a-f]{40})'$", 'm')) || [])[1]
const SHA = pinned('C1_PW_SHA_APPROVED')
const AUTH = pinned('C1_AUTH_SHA_APPROVED')

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
  CHECKOUT_VERIFIED: 'PASS', LOCALHOST: 'PASS', MODE: 'supervised',
  CHECKS: 'harness_clean,auth_ancestor_and_approved_diff,exact_head_clean_worktree,localhost_loopback_only,ports_free,clean_after_dependency_install',
  PLAYWRIGHT_VERSION: '1.65.1', EXPECTED_PLAYWRIGHT_VERSION: '1.65.1',
  BROWSER_VERSION: '159.0.8154.6', EXPECTED_BROWSER_VERSION: '159.0.8154.6',
  BROWSER_HEADED_VERSION: '159.0.8154.6', EXPECTED_HEADED_BROWSER_VERSION: '159.0.8154.6',
  PORT_PRIVATE: 'verified-probe-user-repo', ATTEST_NO_PRIOR_CONSENT: 'yes', ATTEST_CONSENT_PROMPT_CANCELLED: 'yes',
}
const ran = rc => ({ state: 'ran', start: '2026-11-17T00:00:00Z', end: '2026-11-17T00:01:00Z', rc: String(rc) })
const allPhases = () => ({
  'static-mutation': ran(0), 'browser-mutation': ran(0), offline: ran(0), unconfigured: ran(0),
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
  const phases = { 'static-mutation': ran(0), 'browser-mutation': ran(0), offline: ran(0), unconfigured: ran(0),
    'consent-denial': { state: 'skipped' }, live: { state: 'skipped' }, 'cancelled-logout': { state: 'skipped' } }
  const r = evaluate({ meta: { ...meta, MODE: 'offline-only', PORT_PRIVATE: 'not-started' }, phases, reports: { offline: allReports().offline, unconfigured: allReports().unconfigured }, artifactHits: 0 })
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

test('R3 is INCOMPLETE, never PASS, when an expected offline test reports INCOMPLETE', () => {
  const reports = allReports()
  reports.offline = summarizeReport(report(OFFLINE_EXPECTED.map((t, i) =>
    spec(t, i === 0 ? 'skipped' : 'passed', i === 0
      ? { annotations: [{ type: 'skip', description: 'INCOMPLETE: controlled offline cancellation not observed' }] }
      : {}))))
  const r = evaluate({ meta, phases: allPhases(), reports, artifactHits: 0 })
  assert.equal(r.rows.find(x => x.id === 'R3').verdict, 'INCOMPLETE')
  assert.equal(r.overall, 'INCOMPLETE')
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
  // L-5: FAIL (not skip) when the pinned approved suite cannot be read.
  const top = execFileSync('git', ['-C', here, 'rev-parse', '--show-toplevel'], { encoding: 'utf8' }).trim()
  return execFileSync('git', ['-C', top, 'show', SHA + ':frontend/e2e/entra-c1.spec.mjs'], { encoding: 'utf8' })
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

test('L-5: supplemental network guard matches exact pinned PR60 shared guard', () => {
  const top = execFileSync('git', ['-C', here, 'rev-parse', '--show-toplevel'], { encoding: 'utf8' }).trim()
  const approved = execFileSync('git', ['-C', top, 'show', SHA + ':frontend/e2e/c1-guards.mjs'], { encoding: 'utf8' })
    .replace(/^export /gm, '')
  const between = (src, first, last) => src.slice(src.indexOf(first), src.indexOf(last)).trim()
  const start = 'const allowedHosts = new Set(['
  const end = 'async function assertBuild(page)'
  assert.ok(approved.includes(start) && supSpec.includes(start) && approved.includes(end) && supSpec.includes(end))
  assert.equal(between(supSpec, start, end), between(approved, start, end))
  // Exact-host semantics and failing closed on unauthorized LIVE traffic are mandatory.
  assert.match(supSpec, /url\.port === ''/)
  assert.match(supSpec, /live && !allowedRequest\(url\)/)
  assert.match(supSpec, /Blocked unauthorized HTTP origin/)
  assert.match(supSpec, /Blocked unauthorized WebSocket origin/)
  assert.match(supSpec, /const live = true/)
  const mem = 'async function assertMemoryOnly(page, context)'
  assert.equal(supSpec.slice(supSpec.indexOf(mem), supSpec.indexOf('// --- end replicated helpers')).trim(),
    approved.slice(approved.indexOf(mem)).trim())
  assert.match(supSpec, /expect\(served\.sha\)\.toBe\(sha\)/)
  assert.match(supSpec, /expect\(served\.dirty\)\.toBe\(false\)/)
})

// ---- remediation regressions (M-1, M-4, L-2, L-5) ------------------------------
test('L-5: equivalence SHAs are read from c1-lib.sh pins', () => {
  assert.match(SHA, /^[0-9a-f]{40}$/); assert.match(AUTH, /^[0-9a-f]{40}$/)
  assert.doesNotMatch(readFileSync(join(here, 'c1-evidence.test.mjs'), 'utf8'), /const SHA = '[0-9a-f]{40}'/)
})

const ready = over => evaluate({ meta, phases: allPhases(), reports: allReports(), artifactHits: 0, ...over })
const row = (r, id) => r.rows.find(x => x.id === id)

test('M-1a: an unlisted approved test FAILING (offline or live) makes the session FAIL', () => {
  for (const ph of ['offline', 'live']) {
    const reports = allReports()
    reports[ph] = [...reports[ph], ...summarizeReport(report([spec('C1 exact-host regression: Microsoft login host is HTTPS-only', 'failed')]))]
    const r = evaluate({ meta, phases: { ...allPhases(), [ph]: ran(1) }, reports, artifactHits: 0 })
    assert.equal(row(r, 'R12').verdict, 'FAIL', ph); assert.equal(r.overall, 'FAIL', ph)
  }
})

test('M-1b: a ran phase with non-zero exit makes the session FAIL even if listed tests passed', () => {
  const r = evaluate({ meta, phases: { ...allPhases(), unconfigured: ran(2) }, reports: allReports(), artifactHits: 0 })
  assert.equal(row(r, 'R12').verdict, 'FAIL'); assert.equal(r.overall, 'FAIL')
})

test('M-1c: unverified or failed port privacy can never be READY', () => {
  for (const PORT_PRIVATE of ['not-started', 'failed', undefined]) {
    const r = ready({ meta: { ...meta, PORT_PRIVATE } })
    assert.equal(row(r, 'R11').verdict, 'FAIL', String(PORT_PRIVATE)); assert.equal(r.overall, 'FAIL')
  }
  assert.equal(parseMeta('PORT_PRIVATE=operator-confirmed').PORT_PRIVATE, undefined) // legacy value is rejected
  assert.equal(row(ready({}), 'R11').verdict, 'PASS')
})

test('M-1d: browser or Playwright version mismatch FAILS the toolchain row', () => {
  for (const m of [
    { BROWSER_VERSION: '153.0.8010.12' }, { BROWSER_HEADED_VERSION: '153.0.8010.12' },
    { PLAYWRIGHT_VERSION: '1.64.0' }, { EXPECTED_BROWSER_VERSION: undefined }, { BROWSER_HEADED_VERSION: 'unknown' },
  ]) {
    const r = ready({ meta: { ...meta, ...m } })
    assert.equal(row(r, 'R10').verdict, 'FAIL', JSON.stringify(m)); assert.equal(r.overall, 'FAIL')
  }
})

test('M-1: a complete, consistent supervised session is READY (and only then)', () => {
  assert.equal(ready({}).overall, 'READY FOR INDEPENDENT REVIEW')
})

test('M-4: R8 needs access_denied + AADSTS65004 + both attestations; otherwise INCOMPLETE, never PASS', () => {
  const withResult = line => { const r = allReports(); r['consent-denial'] = summarizeReport(report([spec('SUPERVISED consent denial: x', 'passed', { stdout: [line] })])); return r }
  const ok = 'C1 RESULT case=consent-denial state=failed code=access_denied aadsts=AADSTS65004'
  assert.equal(row(ready({ reports: withResult(ok) }), 'R8').verdict, 'PASS')
  assert.equal(row(ready({ reports: withResult('C1 RESULT case=consent-denial state=failed code=access_denied aadsts=none') }), 'R8').verdict, 'INCOMPLETE')
  assert.equal(row(ready({ reports: withResult('C1 RESULT case=consent-denial state=failed code=interaction_required aadsts=AADSTS65004') }), 'R8').verdict, 'INCOMPLETE')
  for (const a of [{ ATTEST_NO_PRIOR_CONSENT: 'no' }, { ATTEST_CONSENT_PROMPT_CANCELLED: 'no' }, { ATTEST_NO_PRIOR_CONSENT: 'not-asked' }, { ATTEST_CONSENT_PROMPT_CANCELLED: undefined }]) {
    const r = ready({ meta: { ...meta, ...a }, reports: withResult(ok) })
    assert.equal(row(r, 'R8').verdict, 'INCOMPLETE', JSON.stringify(a)); assert.notEqual(r.overall, 'READY FOR INDEPENDENT REVIEW')
  }
})

test('M-4: supplemental spec no longer accepts a missing AADSTS code', () => {
  assert.doesNotMatch(supSpec, /\['AADSTS65004', 'none'\]/)
  assert.match(supSpec, /expect\(outcome\.aadsts\)\.toBe\('AADSTS65004'\)/)
  assert.match(supSpec, /INCOMPLETE: access_denied without AADSTS65004/)
})

test('L-2: terminal filter withholds identities, tokens, URLs and GUIDs; keeps safe lines', () => {
  const raw = [
    '\u001b[32m  1 passed\u001b[0m', '1) <span>Signed in: alice.tester@contoso.example</span>',
    'Error: AADSTS50076 Trace ID: 773e9051-c546-4b75-9432-90f4f8242660', 'token eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.x',
    'navigated to https://login.microsoftonline.com/x/oauth2/v2.0/authorize?client_id=1', 'M1: DETECTED (persistent MSAL cache)',
  ].join('\n')
  const out = filterLines(raw)
  assert.ok(out.includes('  1 passed')); assert.ok(out.includes('M1: DETECTED (persistent MSAL cache)'))
  assert.ok(!out.join('\n').match(/alice|eyJ|773e9051|oauth2/))
  assert.ok(out.some(l => l === '(line withheld by sanitizer)'))
})

test('L-2: per-test summary lines are sanitized and title-allowlisted', () => {
  const lines = summaryLines(report([spec('M2 browser: loopback-origin redirect', 'passed'), spec('bad <alice@contoso.example>', 'failed')]))
  assert.ok(lines.some(l => /PASS\s+M2 browser/.test(l)))
  assert.ok(lines.some(l => /FAIL\s+unprintable-title/.test(l)))
  assert.ok(!lines.join('\n').includes('alice'))
})

test('N-1: R1 FAILS when any mandatory preflight check is missing from the evidence', () => {
  for (const drop of ['auth_ancestor_and_approved_diff', 'exact_head_clean_worktree', 'harness_clean', 'clean_after_dependency_install']) {
    const CHECKS = meta.CHECKS.split(',').filter(c => c !== drop).join(',')
    const r = ready({ meta: { ...meta, CHECKS } })
    assert.equal(row(r, 'R1').verdict, 'FAIL', drop); assert.match(row(r, 'R1').detail, new RegExp('MISSING: ' + drop))
    assert.equal(r.overall, 'FAIL')
  }
})
