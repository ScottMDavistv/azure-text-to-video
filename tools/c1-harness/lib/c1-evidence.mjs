#!/usr/bin/env node
// C1 harness sanitized evidence + independent review package generator.
// Reads raw Playwright JSON reports from the private session directory IN
// MEMORY ONLY, extracts allowlisted tokens (titles, statuses, sanitized MSAL
// codes, hostnames, booleans), and refuses to write any output that fails the
// sanitizer. Raw reports are deleted by session cleanup, never copied.
import { createHash } from 'node:crypto'
import { lstatSync, readdirSync, readFileSync, writeFileSync, mkdirSync, existsSync, chmodSync } from 'node:fs'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'

export const PHASES = {
  'static-mutation': { label: 'Static source mutation regression', live: false },
  'browser-mutation': { label: 'Browser-level security mutation regression', live: false },
  offline: { label: 'Offline synthetic Playwright suite', live: false },
  unconfigured: { label: 'Unconfigured fail-closed suite', live: false },
  'consent-denial': { label: 'Supervised consent denial (harness supplemental)', live: true },
  live: { label: 'Supervised approved LIVE suite', live: true },
  'cancelled-logout': { label: 'Supervised cancelled logout (harness supplemental)', live: true },
}

// N-1: R1 passes only if every mandatory preflight check actually ran and passed.
export const REQUIRED_CHECKS = ['harness_clean', 'auth_ancestor_and_approved_diff', 'exact_head_clean_worktree',
  'localhost_loopback_only', 'ports_free', 'clean_after_dependency_install']

export const OFFLINE_EXPECTED = [
  'M-1/M-4/M-5: configured auth runtime',
  'M-1/M-2: attempted login sends correct tenant',
  'M2 browser: loopback-origin redirect',
  'M4 browser: interaction-required token recovery',
  'M5 browser: signOut invokes MSAL logoutPopup',
  'M6-B/M6-C browser: egress firewall',
  'Static source invariants',
]

export const ROWS = [
  { id: 'R1', label: 'Clean exact-head checkout verification', kind: 'meta' },
  { id: 'R2', label: 'Static source mutation regression (all mutations detected)', kind: 'exit', phase: 'static-mutation' },
  { id: 'R2B', label: 'Browser-level security mutation regression (all mutations detected)', kind: 'exit', phase: 'browser-mutation' },
  { id: 'R3', label: 'Offline synthetic suite (all 7 executed tests)', kind: 'all', phase: 'offline', expect: OFFLINE_EXPECTED },
  { id: 'R4', label: 'Unconfigured SPA is fail-closed', kind: 'test', phase: 'unconfigured', title: 'M-1: missing SPA configuration is fail-closed' },
  { id: 'R5', label: 'Localhost routing and canonical localhost redirect', kind: 'redirect', phase: 'offline', title: 'M2 browser: loopback-origin redirect' },
  { id: 'R6', label: 'Live sign-in, initial consent, claims, silent token, verified logout', kind: 'test', phase: 'live', title: 'LIVE: popup login, validated claims' },
  { id: 'R7', label: 'Cancelled login returns user_cancelled', kind: 'test', phase: 'live', title: 'LIVE: deliberately cancelled popup', passCode: 'user_cancelled' },
  { id: 'R8', label: 'Consent denial fails closed (access_denied + AADSTS65004 + operator attestations)', kind: 'consent', phase: 'consent-denial', title: 'SUPERVISED consent denial' },
  { id: 'R9', label: 'Cancelled logout leaves a consistent, fail-closed local state', kind: 'test', phase: 'cancelled-logout', title: 'SUPERVISED cancelled logout' },
  { id: 'R10', label: 'Toolchain: exact Playwright and the browser build it expects (headless and headed)', kind: 'toolchain' },
  { id: 'R11', label: 'Private desktop: Codespaces port privacy technically verified (decoy probe)', kind: 'privacy' },
  { id: 'R12', label: 'Every executed test and phase succeeded (no unlisted failures)', kind: 'integrity' },
]

const RESULT_RE = /^C1 RESULT case=(consent-denial|cancelled-logout) state=(not_observed|succeeded|failed|unrecognized) code=([a-z0-9_]{1,64}) aadsts=(none|AADSTS\d{5,6}|unrecognized)$/
const OBS_RE = /^C1 OBSERVATION case=cancelled-logout ui_signed_in=(true|false) msal_account_present=(true|false) failure_message_shown=(true|false) server_signout_confirmed=(true|false)$/
const HOST_RE = /(?:Blocked unauthorized HTTP origin|Unexpected request host|Blocked unauthorized WebSocket host|Unauthorized WebSocket host|Authorization header to unauthorized host) ([a-z0-9.-]{1,253})/gi
const CRED_MARK = /Credential-like|Authorization header to unauthorized/
const EGRESS_MARK = /Unexpected request host|Blocked unauthorized|Unauthorized WebSocket/
const INCOMPLETE_RE = /^INCOMPLETE: [A-Za-z0-9 ;,.()'-]{1,160}$/
const TITLE_RE = /^[A-Za-z0-9 /:;,.()'_-]{1,200}$/

/** Throws if text contains anything that must never leave the session. */
export function assertSanitized(text) {
  const rules = [
    [/eyJ[A-Za-z0-9_-]{10,}/, 'JWT-like material'],
    [/\bbearer\s/i, 'bearer credential'],
    [/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/, 'email address / UPN'],
    [/https?:\/\/[^\s)]*[?#]/, 'URL with query or fragment'],
    [/client_secret|access_token|id_token|refresh_token|code_verifier|code_challenge=/i, 'OAuth parameter name'],
    [/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i, 'GUID (tenant/client/object id)'],
    [/[A-Za-z0-9+/_-]{48,}/, 'long opaque string'],
    [/(?:cookie|set-cookie)\s*:/i, 'cookie header'],
  ]
  // Exact 40-hex commit SHAs and 64-hex SHA-256 digests are expected identifiers.
  const scan = text.replace(/(?<![A-Za-z0-9+/_-])(?:[0-9a-f]{64}|[0-9a-f]{40})(?![A-Za-z0-9+/_-])/g, '<hash>')
  for (const [re, what] of rules) if (re.test(scan)) throw new Error('Sanitizer rejected evidence: ' + what)
  return text
}

export function parseMeta(text) {
  const allowed = {
    AUTH_SHA: /^[0-9a-f]{40}$/, PW_SHA: /^[0-9a-f]{40}$/, HARNESS_SHA: /^[0-9a-f]{40}$/,
    HARNESS_CLEAN: /^(true|false)$/, CHECKOUT_VERIFIED: /^(PASS|FAIL)$/,
    CHECKS: /^[a-z0-9_,-]{0,400}$/, PLAYWRIGHT_VERSION: /^[0-9.]{1,20}$/, BROWSER_VERSION: /^[0-9.]{1,32}$/,
    NODE_VERSION: /^v[0-9.]{1,20}$/, SESSION_START: /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$/, SESSION_END: /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$/,
    MODE: /^(supervised|offline-only)$/, CODESPACES: /^(true|false)$/,
    PORT_PRIVATE: /^(verified-probe-gh|verified-probe-user-repo|failed|not-started)$/, LOCALHOST: /^(PASS|FAIL)$/,
    EXPECTED_PLAYWRIGHT_VERSION: /^[0-9.]{1,20}$/, EXPECTED_BROWSER_VERSION: /^[0-9.]{1,32}$/,
    EXPECTED_HEADED_BROWSER_VERSION: /^[0-9.]{1,32}$/, BROWSER_HEADED_VERSION: /^([0-9.]{1,32}|not-launched|unknown)$/,
    ATTEST_NO_PRIOR_CONSENT: /^(yes|no|not-asked)$/, ATTEST_CONSENT_PROMPT_CANCELLED: /^(yes|no|not-asked)$/,
  }
  const meta = {}
  for (const line of text.split('\n')) {
    const m = line.match(/^([A-Z_]+)=(.*)$/)
    if (!m) continue
    const [, k, v] = m
    if (allowed[k] && allowed[k].test(v)) meta[k] = v
  }
  return meta
}

export function parsePhases(text) {
  const phases = {}
  for (const line of text.split('\n')) {
    const m = line.match(/^([a-z-]+)\|(ran|skipped)\|(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ|-)\|(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ|-)\|(\d{1,3}|-)$/)
    if (m && PHASES[m[1]]) phases[m[1]] = { state: m[2], start: m[3], end: m[4], rc: m[5] }
  }
  return phases
}

function collectSpecs(suite, out = []) {
  for (const spec of suite.specs || []) out.push(spec)
  for (const child of suite.suites || []) collectSpecs(child, out)
  return out
}

function textOf(entry) {
  if (typeof entry === 'string') return entry
  if (entry?.text) return entry.text
  if (entry?.buffer) return Buffer.from(entry.buffer, 'base64').toString('utf8')
  return ''
}

/** Reduce a raw Playwright JSON report to allowlisted per-test facts. */
export function summarizeReport(report) {
  const tests = []
  for (const top of report?.suites || []) {
    for (const spec of collectSpecs(top)) {
      const t = spec.tests?.[0] || {}
      const results = t.results || []
      const last = results[results.length - 1]
      const annotations = [...(t.annotations || []), ...(last?.annotations || [])]
      const status = last?.status || 'skipped'
      let verdict
      let reason = ''
      if (status === 'passed') verdict = 'PASS'
      else if (['failed', 'timedOut', 'interrupted'].includes(status)) verdict = 'FAIL'
      else {
        const inc = annotations.find(a => a?.type === 'skip' && INCOMPLETE_RE.test(String(a.description || '')))
        verdict = inc ? 'INCOMPLETE' : 'SKIPPED'
        if (inc) reason = inc.description
      }
      const errors = results.flatMap(r => (r.errors || []).map(e => String(e?.message || '')))
      const stdout = results.flatMap(r => (r.stdout || []).map(textOf)).join('\n').split('\n')
      const result = stdout.map(l => l.trim().match(RESULT_RE)).find(Boolean)
      const obs = stdout.map(l => l.trim().match(OBS_RE)).find(Boolean)
      const hosts = new Set()
      for (const msg of errors) for (const m of msg.matchAll(HOST_RE)) hosts.add(m[1].toLowerCase())
      const title = TITLE_RE.test(spec.title) ? spec.title : 'unprintable-title'
      tests.push({
        title, verdict, reason,
        code: result ? result[3] : null, aadsts: result && result[4] !== 'none' ? result[4] : null,
        state: result ? result[2] : null,
        observation: obs ? { uiSignedIn: obs[1], msalAccountPresent: obs[2], failureMessageShown: obs[3], serverSignoutConfirmed: obs[4] } : null,
        credentialMarker: errors.some(m => CRED_MARK.test(m)),
        egressMarker: errors.some(m => EGRESS_MARK.test(m)),
        hosts: [...hosts],
      })
    }
  }
  return tests
}

/** Scan the private session directory for token-like material (no symlink following). */
export function scanArtifacts(dir) {
  const hits = []
  const re = /eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}|Bearer\s+[A-Za-z0-9._-]{24,}/
  const walk = d => {
    let entries = []
    try { entries = readdirSync(d) } catch { return }
    for (const name of entries) {
      const p = join(d, name)
      let st
      try { st = lstatSync(p) } catch { continue }
      if (st.isSymbolicLink()) continue
      if (st.isDirectory()) walk(p)
      else if (st.isFile() && st.size < 50 * 1024 * 1024) {
        try { if (re.test(readFileSync(p, 'latin1'))) hits.push(1) } catch { /* unreadable */ }
      }
    }
  }
  walk(dir)
  return hits.length
}

const find = (tests, prefix) => tests.find(t => t.title.startsWith(prefix))

export function evaluate({ meta, phases, reports, artifactHits }) {
  const rows = []
  const allTests = []
  for (const [id, tests] of Object.entries(reports)) for (const t of tests) allTests.push({ phase: id, ...t })
  const phaseDetail = id => {
    const p = phases[id]
    if (!p || p.state !== 'ran') return { verdict: 'INCOMPLETE', detail: 'not executed' }
    return null
  }
  for (const row of ROWS) {
    let verdict
    let detail = ''
    let code = null
    if (row.kind === 'meta') {
      const have = new Set((meta.CHECKS || '').split(',').filter(Boolean))
      const missing = REQUIRED_CHECKS.filter(c => !have.has(c))
      verdict = meta.CHECKOUT_VERIFIED === 'PASS' && meta.HARNESS_CLEAN === 'true' && !missing.length ? 'PASS' : 'FAIL'
      detail = (meta.CHECKS || '').replace(/,/g, ', ') + (missing.length ? '; MISSING: ' + missing.join(', ') : '')
    } else if (row.kind === 'toolchain') {
      const pwOk = Boolean(meta.PLAYWRIGHT_VERSION) && meta.PLAYWRIGHT_VERSION === meta.EXPECTED_PLAYWRIGHT_VERSION
      const bOk = Boolean(meta.BROWSER_VERSION) && meta.BROWSER_VERSION === meta.EXPECTED_BROWSER_VERSION
      const headedRequired = meta.MODE !== 'offline-only'
      const hOk = !headedRequired || (Boolean(meta.BROWSER_HEADED_VERSION) && meta.BROWSER_HEADED_VERSION === meta.EXPECTED_HEADED_BROWSER_VERSION)
      verdict = pwOk && bOk && hOk ? 'PASS' : 'FAIL'
      detail = 'playwright ' + (meta.PLAYWRIGHT_VERSION ?? 'unknown') + ' (expected ' + (meta.EXPECTED_PLAYWRIGHT_VERSION ?? 'unknown') + '); headless ' +
        (meta.BROWSER_VERSION ?? 'unknown') + ' (expected ' + (meta.EXPECTED_BROWSER_VERSION ?? 'unknown') + ')' +
        (headedRequired ? '; headed ' + (meta.BROWSER_HEADED_VERSION ?? 'unknown') + ' (expected ' + (meta.EXPECTED_HEADED_BROWSER_VERSION ?? 'unknown') + ')' : '')
    } else if (row.kind === 'privacy') {
      if (meta.MODE !== 'supervised') { verdict = 'INCOMPLETE'; detail = 'desktop not started (offline-only)' }
      else if (/^verified-probe-(gh|user-repo)$/.test(meta.PORT_PRIVATE || '')) { verdict = 'PASS'; detail = meta.PORT_PRIVATE }
      else { verdict = 'FAIL'; detail = 'privacy not verified: ' + (meta.PORT_PRIVATE ?? 'unknown') }
    } else if (row.kind === 'integrity') {
      const failedTests = allTests.filter(t => t.verdict === 'FAIL')
      const badPhases = Object.entries(phases).filter(([, p]) => p.state === 'ran' && p.rc !== '0')
      verdict = failedTests.length || badPhases.length ? 'FAIL' : 'PASS'
      detail = failedTests.length + ' failed test(s); ' + badPhases.length + ' phase(s) with non-zero exit' +
        (badPhases.length ? ' (' + badPhases.map(([id]) => id).join(', ') + ')' : '')
    } else if (row.kind === 'exit') {
      const pd = phaseDetail(row.phase)
      if (pd) ({ verdict, detail } = pd)
      else { verdict = phases[row.phase].rc === '0' ? 'PASS' : 'FAIL'; detail = 'exit ' + phases[row.phase].rc }
    } else {
      const pd = phaseDetail(row.phase)
      const tests = reports[row.phase]
      if (pd) ({ verdict, detail } = pd)
      else if (!tests) { verdict = 'FAIL'; detail = 'no report (startup/config failure, exit ' + phases[row.phase].rc + ')' }
      else if (row.kind === 'all') {
        const missing = row.expect.filter(p => !find(tests, p) || find(tests, p).verdict === 'SKIPPED')
        const failed = row.expect.filter(p => find(tests, p)?.verdict === 'FAIL')
        verdict = missing.length || failed.length ? 'FAIL' : 'PASS'
        detail = (row.expect.length - missing.length - failed.length) + '/' + row.expect.length + ' passed' +
          (missing.length ? '; missing ' + missing.length : '') + (failed.length ? '; failed ' + failed.length : '')
      } else {
        const t = find(tests, row.title)
        if (!t) { verdict = 'FAIL'; detail = 'test did not execute' }
        else if (t.verdict === 'SKIPPED') { verdict = 'INCOMPLETE'; detail = 'unexpectedly skipped' }
        else { verdict = t.verdict; detail = t.reason; code = t.code ? t.code + (t.aadsts ? ' / ' + t.aadsts : '') : null }
        if (row.kind === 'redirect' && verdict === 'PASS' && meta.LOCALHOST !== 'PASS') { verdict = 'FAIL'; detail = 'localhost preflight failed' }
        if (row.kind === 'consent' && verdict === 'PASS') {
          // M-4: only complete evidence passes; anything less is INCOMPLETE, never accepted.
          const missing = []
          if (t.code !== 'access_denied') missing.push('MSAL access_denied not evidenced')
          if (t.aadsts !== 'AADSTS65004') missing.push('AADSTS65004 not evidenced')
          if (meta.ATTEST_NO_PRIOR_CONSENT !== 'yes') missing.push('no-prior-consent attestation missing')
          if (meta.ATTEST_CONSENT_PROMPT_CANCELLED !== 'yes') missing.push('consent-prompt attestation missing')
          if (missing.length) { verdict = 'INCOMPLETE'; detail = missing.join('; ') }
          else detail = 'operator attested: no prior consent; consent prompt shown and cancelled'
        }
        if (row.passCode && verdict === 'PASS') code = row.passCode + ' (asserted)'
      }
    }
    rows.push({ ...row, verdict, detail, code })
  }
  const r6 = rows.find(r => r.id === 'R6').verdict
  const m6 = find(reports.offline || [], 'M6-B/M6-C')
  const liveTests = allTests.filter(t => PHASES[t.phase].live)
  const credMarked = allTests.some(t => t.credentialMarker)
  const egressMarked = liveTests.some(t => t.egressMarker) ||
    allTests.some(t => t.egressMarker && !t.title.startsWith('M6-B/M6-C'))
  const credential = credMarked || artifactHits > 0 ? 'FAIL' : r6 === 'PASS' ? 'PASS' : 'INCOMPLETE'
  const egress = egressMarked || m6?.verdict === 'FAIL' ? 'FAIL' : (m6?.verdict === 'PASS' && r6 === 'PASS') ? 'PASS' : 'INCOMPLETE'
  const hosts = [...new Set(allTests.filter(t => !t.title.startsWith('M6-B/M6-C')).flatMap(t => t.hosts))].sort()
  const overall = rows.some(r => r.verdict === 'FAIL') || credential === 'FAIL' || egress === 'FAIL'
    ? 'FAIL' : rows.every(r => r.verdict === 'PASS') && credential === 'PASS' && egress === 'PASS'
      ? 'READY FOR INDEPENDENT REVIEW' : 'INCOMPLETE'
  return { rows, allTests, credential, egress, hosts, overall, artifactHits }
}

export function renderEvidence({ meta, phases, result, generated }) {
  const L = []
  const v = x => x ?? 'unknown'
  L.push('# C1 sanitized evidence — ' + generated, '')
  L.push('**Harness summary: ' + result.overall + '.** This harness never issues C1 certification; an independent security review must evaluate this evidence.', '')
  L.push('## Identity', '')
  L.push('| Item | Value |', '|---|---|')
  L.push('| Reviewed authentication SHA (PR #46) | `' + v(meta.AUTH_SHA) + '` |')
  L.push('| Playwright suite SHA (PR #60, tested HEAD) | `' + v(meta.PW_SHA) + '` |')
  L.push('| Harness SHA | `' + v(meta.HARNESS_SHA) + '` (clean: ' + v(meta.HARNESS_CLEAN) + ') |')
  L.push('| Clean-checkout verification | ' + v(meta.CHECKOUT_VERIFIED) + ' |')
  L.push('| @playwright/test | ' + v(meta.PLAYWRIGHT_VERSION) + ' |')
  L.push('| Chromium (browser.version()) | ' + v(meta.BROWSER_VERSION) + ' |')
  L.push('| Node.js | ' + v(meta.NODE_VERSION) + ' |')
  L.push('| Session UTC | ' + v(meta.SESSION_START) + ' → ' + v(meta.SESSION_END) + ' |')
  L.push('| Mode | ' + v(meta.MODE) + ' (Codespaces: ' + v(meta.CODESPACES) + ', noVNC port privacy: ' + v(meta.PORT_PRIVATE) + ') |', '')
  L.push('## Phases', '', '| Phase | Executed | Start UTC | End UTC | Exit |', '|---|---|---|---|---|')
  for (const [id, def] of Object.entries(PHASES)) {
    const p = phases[id]
    L.push('| ' + def.label + ' | ' + (p?.state === 'ran' ? 'yes' : 'no') + ' | ' + (p?.start ?? '-') + ' | ' + (p?.end ?? '-') + ' | ' + (p?.rc ?? '-') + ' |')
  }
  L.push('', '## C1 matrix', '', '| Row | Requirement | Result | Sanitized MSAL code | Detail |', '|---|---|---|---|---|')
  for (const r of result.rows) L.push('| ' + r.id + ' | ' + r.label + ' | **' + r.verdict + '** | ' + (r.code ?? '-') + ' | ' + (r.detail || '-') + ' |')
  L.push('', '## Test-by-test', '', '| Phase | Test | Result | Code |', '|---|---|---|---|')
  for (const t of result.allTests.filter(t => t.verdict !== 'SKIPPED')) {
    L.push('| ' + t.phase + ' | ' + t.title + ' | ' + t.verdict + ' | ' + (t.code ? t.code + (t.aadsts ? ' / ' + t.aadsts : '') : '-') + ' |')
  }
  const skipped = result.allTests.filter(t => t.verdict === 'SKIPPED').length
  L.push('', skipped + ' test(s) were skipped by design for their mode (offline vs live) and are omitted.', '')
  L.push('## Security verdicts', '')
  L.push('- Credential confidentiality: **' + result.credential + '** (token-like artifacts found before deletion: ' + result.artifactHits + ')')
  L.push('- Network egress: **' + result.egress + '**')
  L.push('- Unapproved hosts observed by the egress guard: ' + (result.hosts.length ? result.hosts.map(h => '`' + h + '`').join(', ') : 'none'))
  const obs = result.allTests.find(t => t.observation)?.observation
  if (obs) {
    L.push('', '## Observations', '', '- Cancelled logout: UI still shows signed in = ' + obs.uiSignedIn +
      '; MSAL account still cached = ' + obs.msalAccountPresent + '; failure message shown = ' + obs.failureMessageShown +
      '; Microsoft server sign-out confirmed = ' + obs.serverSignoutConfirmed + '.',
      '- MSAL 4.x clears the local cache before opening the sign-out popup and treats server sign-out as best effort, so a cancelled popup can leave the Microsoft SSO session alive in that browser profile while the app is locally signed out. The harness browser context is discarded at test end. Reviewer: decide whether PR #46 should surface this to users.')
  }
  L.push('', '## Cleanup', '', '- @@C1_CLEANUP_VERDICT@@', '')
  L.push('## Not covered by this evidence', '')
  L.push('- No tokens, claims, cookies, URLs, UPNs, HAR files, traces, screenshots or console dumps were retained. Raw Playwright reports were read in memory and deleted with the session.')
  L.push('- INCOMPLETE rows require another supervised session; they are not failures of the application.')
  L.push('- R2B reflects the approved browser-mutation runner at the pinned Playwright SHA; if that SHA has no runner, R2B is INCOMPLETE.')
  L.push('- R8 relies on two operator attestations (no prior consent; consent prompt shown and cancelled) in addition to the sanitized MSAL code and AADSTS65004.')
  return L.join('\n') + '\n'
}

export function renderReviewPackage({ evidence, meta }) {
  const digest = createHash('sha256').update(evidence).digest('hex')
  const L = []
  L.push('# Independent security review request — C1 Entra validation session', '')
  L.push('You are an independent security reviewer. You did not write the harness or run this session. Do not approve or merge anything; report findings and a verdict only.', '')
  L.push('## Scope', '')
  L.push('- Authentication implementation: PR #46 at `' + (meta.AUTH_SHA ?? 'unknown') + '` (not modified by the harness).')
  L.push('- Approved Playwright suite: PR #60 at `' + (meta.PW_SHA ?? 'unknown') + '` (executed unmodified from a detached clean worktree).')
  L.push('- Harness: `tools/c1-harness/` at `' + (meta.HARNESS_SHA ?? 'unknown') + '` (draft PR; must itself be independently reviewed).', '')
  L.push('## Reviewer checklist', '')
  L.push('1. Confirm `git diff --name-only <auth> <playwright>` lists only the six approved test files, and `git diff --name-only <playwright> <harness>` lists only `tools/c1-harness/**`.')
  L.push('2. Confirm the evidence SHAs match the PR heads you are reviewing and that rows marked PASS correspond to executed tests (no SKIPPED row is counted as PASS).')
  L.push('3. Re-run the offline suites yourself from a clean checkout; compare with R2–R5.')
  L.push('4. Review `tools/c1-harness/supplemental/c1-supervised.spec.mjs` helpers for equivalence with the approved suite\'s LIVE egress/storage checks.')
  L.push('5. Check network exposure: only `127.0.0.1:6080` (noVNC) listened during the session; R11 shows Codespaces port privacy was proven by the decoy probe (unauthenticated probe denied, authenticated probe reached it) before the desktop was served; VNC on a 0600 Unix socket; X server with no TCP/abstract listeners.')
  L.push('6. Check credential confidentiality and cleanup verdicts below; treat any FAIL or INCOMPLETE as blocking C1 PASS.')
  L.push('7. Decide: C1 PASS / C1 INCOMPLETE / REQUEST CHANGES, with reasons. Do not self-certify on behalf of the implementing agent.', '')
  L.push('Evidence SHA-256 (of the section below, for transcription checks only): `' + digest + '`', '')
  L.push('---', '', evidence)
  return L.join('\n')
}

/** Is a terminal line safe to show? (same rules as the evidence sanitizer) */
export function safeLine(line) {
  try { assertSanitized(line); return true } catch { return false }
}

/** Sanitized terminal view of raw phase output (L-2): unsafe lines are withheld. */
export function filterLines(text, max = 40) {
  // eslint-disable-next-line no-control-regex
  const lines = text.replace(/\u001b\[[0-9;?]*[A-Za-z]/g, '').split('\n').map(l => l.replace(/\s+$/, '')).filter(Boolean)
  const out = []
  for (const l of lines.slice(-max)) {
    const shown = safeLine(l) ? l.slice(0, 300) : '(line withheld by sanitizer)'
    if (!(shown.startsWith('(line withheld') && out[out.length - 1] === shown)) out.push(shown)
  }
  return out
}

/** One sanitized line per executed test of a raw Playwright JSON report. */
export function summaryLines(report) {
  const tests = summarizeReport(report)
  const out = tests.filter(t => t.verdict !== 'SKIPPED').map(t => '  ' + t.verdict.padEnd(10) + t.title)
  const n = v => tests.filter(t => t.verdict === v).length
  out.push('  ' + n('PASS') + ' pass, ' + n('FAIL') + ' fail, ' + n('INCOMPLETE') + ' incomplete, ' + n('SKIPPED') + ' skipped by design')
  return out.map(l => (safeLine(l) ? l : '  (line withheld by sanitizer)'))
}

function main(argv) {
  const args = Object.fromEntries(argv.map(a => a.match(/^--([a-z-]+)=(.*)$/)).filter(Boolean).map(m => [m[1], m[2]]))
  if (args.filter) {
    const text = existsSync(args.filter) ? readFileSync(args.filter, 'utf8') : ''
    for (const l of filterLines(text, Number(args.max) || 40)) process.stdout.write('[c1]   | ' + l + '\n')
    return
  }
  if (args.summarize) {
    let report = null
    try { report = JSON.parse(readFileSync(args.summarize, 'utf8')) } catch { process.stdout.write('[c1]   (report unreadable)\n'); return }
    for (const l of summaryLines(report)) process.stdout.write('[c1] ' + l + '\n')
    return
  }
  const session = args.session
  const out = args.out
  if (!session || !out) throw new Error('usage: c1-evidence.mjs --session=DIR --out=DIR')
  const meta = parseMeta(existsSync(join(session, 'meta')) ? readFileSync(join(session, 'meta'), 'utf8') : '')
  const phases = parsePhases(existsSync(join(session, 'phases')) ? readFileSync(join(session, 'phases'), 'utf8') : '')
  const reports = {}
  for (const id of Object.keys(PHASES)) {
    const p = join(session, 'reports', id + '.json')
    if (existsSync(p)) { try { reports[id] = summarizeReport(JSON.parse(readFileSync(p, 'utf8'))) } catch { reports[id] = [] } }
  }
  const artifactHits = scanArtifacts(session)
  const result = evaluate({ meta, phases, reports, artifactHits })
  const generated = new Date().toISOString().replace(/\.\d{3}Z$/, 'Z')
  const evidence = assertSanitized(renderEvidence({ meta, phases, result, generated }))
  const pkg = assertSanitized(renderReviewPackage({ evidence, meta }))
  mkdirSync(out, { recursive: true, mode: 0o700 })
  const stamp = generated.replace(/[-:]/g, '')
  const ev = join(out, 'c1-evidence-' + stamp + '.md')
  const rp = join(out, 'c1-review-package-' + stamp + '.md')
  writeFileSync(ev, evidence, { mode: 0o600 })
  writeFileSync(rp, pkg, { mode: 0o600 })
  chmodSync(out, 0o700)
  process.stdout.write(ev + '\n' + rp + '\n' + result.overall + '\n')
}

if (import.meta.url === pathToFileURL(process.argv[1] || '').href) {
  try { main(process.argv.slice(2)) } catch (error) { console.error('[c1] evidence: ' + error.message); process.exit(2) }
}
