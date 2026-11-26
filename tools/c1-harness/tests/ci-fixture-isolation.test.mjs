import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'

const runner = fileURLToPath(new URL('./run-ci-fixtures-isolated.sh', import.meta.url))
const workflow = readFileSync(new URL('../../../.github/workflows/frontend-quality-gates.yml', import.meta.url), 'utf8')
const script = readFileSync(runner, 'utf8')
const payload = script.split("<<'FIXTURES'\n")[1].split('\nFIXTURES\n')[0]

function call(args, flags = {}) {
  const env = { PATH: '/usr/bin:/bin', HOME: '/tmp', ...flags }
  return spawnSync('/bin/bash', [runner, ...args], { encoding: 'utf8', env, timeout: 3000 })
}

test('runner accepts no arbitrary command, live mode, or inherited mutation mode', () => {
  for (const [args, env, message] of [
    [[], {}, /choose browser or harness/],
    [['browser', '/bin/true'], {}, /choose browser or harness/],
    [['r11-only'], {}, /choose browser or harness/],
    [['browser'], { C1_LIVE: '1' }, /live or mutation mode/],
    [['harness'], { C1_MUTATION_MODE: '1' }, /live or mutation mode/],
  ]) {
    const result = call(args, env)
    assert.equal(result.status, 1, result.error?.message)
    assert.match(result.stderr, message)
  }
})

test('fixture payload refuses the parent namespace before touching fixture paths', () => {
  const net = spawnSync('/usr/bin/readlink', ['/proc/self/ns/net'], { encoding: 'utf8' })
  assert.equal(net.status, 0)
  const result = spawnSync('/bin/bash', ['-c', payload, 'fixture-test', net.stdout.trim(), '/missing-fixtures', 'harness'], {
    encoding: 'utf8', timeout: 3000,
  })
  assert.equal(result.status, 1, result.error?.message)
  assert.equal(result.stdout, '')
  assert.match(result.stderr, /runner is root or still in the parent network namespace/)
})

test('hosted workflow stages dependencies before the isolated runners, with no direct C1 fixture invocation', () => {
  const browserJob = workflow.split('\n  c1-harness:\n')[0]
  const harnessJob = workflow.split('\n  c1-harness:\n')[1]
  assert.match(browserJob, /playwright install --with-deps chromium\n +bash \.\.\/tools\/c1-harness\/tests\/run-ci-fixtures-isolated\.sh browser\n/)
  assert.match(harnessJob, /apt-get install[^\n]* iproute2[\s\S]*\n +bash tools\/c1-harness\/tests\/run-ci-fixtures-isolated\.sh harness\n/)
  assert.doesNotMatch(workflow, /^\s+(?:bash|npx|node|xvfb-run)\b[^\n]*(?:c1-session\.sh|test_c1_|c1-webrtc-udp-check|run-browser-mutations|c1-mutation-check|playwright test)/m)
  assert.match(payload, /xvfb-run -a node e2e\/c1-webrtc-udp-check\.mjs/)
  assert.match(payload, /test "\$rc" -eq 78/)
})
