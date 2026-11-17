// Real offline browser mutations in isolated disposable Git worktrees.
// This runner never calls Microsoft, Azure, FAL or other external providers.
//
// A mutation counts as BROWSER DETECTED only when ALL of the following hold:
//   - the unmutated baseline passes in the same isolated setup (no environment failure);
//   - static source checks are demonstrably disabled in the worktree;
//   - the mutation anchor occurs exactly once and the file actually changes;
//   - the grepped browser test fails with a real expect() assertion whose
//     received value matches the mutation's `proof` (and `alsoProve`);
//   - for egress mutations, the local sink received zero HTTP requests and
//     zero WebSocket upgrades.
import { execFileSync, spawn } from 'node:child_process'
import { createServer } from 'node:http'
import { readFileSync, writeFileSync, symlinkSync, mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { mutations, applyMutation } from './c1-mutations.mjs'

if (process.env.C1_LIVE === '1') throw new Error('Never mutate a live Entra session')
if (mutations.length !== 7) throw new Error('exactly seven C1 security mutations are required')
const frontend = process.cwd()
const repo = resolve(frontend, '..')
const nodeModules = join(frontend, 'node_modules')

const hits = []
const sink = createServer((request, response) => { hits.push('http ' + request.method + ' ' + request.url); response.end('c1-sink') })
sink.on('upgrade', (request, socket) => { hits.push('ws ' + request.url); socket.destroy() })
await new Promise(done => sink.listen(0, '127.0.0.1', done))
const sinkPort = sink.address().port

function runPlaywright(cwd, grep) {
  const args = [join(nodeModules, '@playwright', 'test', 'cli.js'), 'test', '--config=playwright.config.mjs', '--workers=1', '--reporter=list']
  if (grep) args.push('--grep', grep)
  return new Promise((done, fail) => {
    const child = spawn(process.execPath, args, {
      cwd,
      env: { ...process.env, C1_LIVE: '0', C1_UNCONFIGURED: '0', C1_MUTATION_MODE: '1', VITE_ENTRA_SPA_CLIENT_ID: '' },
      stdio: ['ignore', 'pipe', 'pipe'],
    })
    let output = ''
    const timer = setTimeout(() => { child.kill('SIGKILL'); fail(new Error('playwright timed out')) }, 180000)
    child.stdout.on('data', d => { output += d })
    child.stderr.on('data', d => { output += d })
    child.on('close', status => { clearTimeout(timer); done({ status, output }) })
  })
}

async function inWorktree(label, prepare, check) {
  const temporary = mkdtempSync(join(tmpdir(), 'c1-browser-mutation-'))
  let added = false
  try {
    execFileSync('git', ['worktree', 'add', '--detach', temporary, 'HEAD'], { cwd: repo, stdio: 'pipe' })
    added = true
    const isolatedFrontend = join(temporary, 'frontend')
    symlinkSync(nodeModules, join(isolatedFrontend, 'node_modules'), 'dir')
    const specFile = join(isolatedFrontend, 'e2e', 'entra-c1.spec.mjs')
    const spec = readFileSync(specFile, 'utf8')
    const disabled = spec.replaceAll('  assertSourceInvariants()\n', '  // source-only checks disabled for browser mutation\n')
    if ((spec.match(/^ {2}assertSourceInvariants\(\)$/gm) ?? []).length < 1 || /^\s+assertSourceInvariants\(\)$/m.test(disabled)) {
      throw new Error(label + ': static source checks could not be disabled; detection would not be browser-only')
    }
    writeFileSync(specFile, disabled)
    const grep = prepare(isolatedFrontend)
    hits.length = 0
    const result = await runPlaywright(isolatedFrontend, grep)
    check(result, [...hits])
  } finally {
    if (added) {
      try { execFileSync('git', ['worktree', 'remove', '--force', temporary], { cwd: repo, stdio: 'pipe' }) }
      catch { /* preserve failure above */ }
    }
    rmSync(temporary, { recursive: true, force: true })
  }
}

let detected = 0
try {
  // Baseline gate: the unmutated build must pass browser-only, so a later
  // failure is attributable to the mutation and not to the environment.
  await inWorktree('BASELINE', () => null, ({ status, output }) => {
    const passed = Number(output.match(/(\d+) passed/)?.[1] ?? 0)
    if (status !== 0 || /\d+ failed/.test(output) || passed < 7) {
      throw new Error('BASELINE: unmutated browser-only suite must pass; status=' + status + ' passed=' + passed)
    }
    console.log('BASELINE: browser-only suite passed (' + passed + ' passed)')
  })
  for (const m of mutations) {
    await inWorktree(m.id, isolatedFrontend => {
      const target = join(isolatedFrontend, m.file)
      writeFileSync(target, applyMutation(readFileSync(target, 'utf8'), m, sinkPort))
      return m.grep
    }, ({ status, output }, delivered) => {
      const realAssertion = /expect\(received\)|Error: expect\(/.test(output)
      const proofs = [m.proof, ...(m.alsoProve ?? [])].map(p => p.test(output))
      if (status === 0 || !realAssertion || proofs.includes(false)) {
        throw new Error(m.id + ': no discriminating browser assertion; status=' + status +
          ', assertion=' + realAssertion + ', proofs=' + JSON.stringify(proofs))
      }
      if (m.zeroDelivery && delivered.length !== 0) {
        throw new Error(m.id + ': egress reached the local sink (' + delivered.length + ' deliveries)')
      }
      detected++
      console.log(m.id + ': BROWSER DETECTED (' + m.property + ')' + (m.zeroDelivery ? '; sink deliveries=0' : ''))
    })
  }
} finally {
  await new Promise(done => sink.close(done))
}
if (detected !== mutations.length) throw new Error('C1 BROWSER MUTATIONS: only ' + detected + '/' + mutations.length + ' detected')
console.log('C1 BROWSER MUTATIONS: ' + detected + '/' + mutations.length + ' detected; no real auth or paid provider.')
