// Static source-level mutation regression. Run IN ADDITION to the Playwright
// browser suite and run-browser-mutations.mjs, never in place of them.
import assert from 'node:assert/strict'
import { loadSources, sourceViolations, coverageViolations, AUTH_WRAPPER, AUTH_CORE, APP } from './c1-source-policy.mjs'
import { mutations, applyMutation } from './c1-mutations.mjs'

const files = loadSources()
assert.deepEqual(sourceViolations(files), [], 'Baseline must satisfy all static auth constraints')
assert.equal(mutations.length, 7, 'exactly seven C1 security mutations are required')

let detected = 0
for (const m of mutations) {
  const target = m.file.replace(/^src\//, '')
  assert.ok(typeof files[target] === 'string', m.id + ': mutation target is not an inspected source')
  const mutated = { ...files, [target]: applyMutation(files[target], m) }
  const errors = sourceViolations(mutated)
  assert.ok(errors.some(e => e.startsWith(m.id + ' ')), m.id + ': mutated source escaped its own ' + m.id + ' assertion: ' + JSON.stringify(errors))
  detected++
  console.log(m.id + ': DETECTED (' + errors.join(', ') + ')')
}

// Anti-vacuity decoys: the policy must fail closed when the code it inspects is
// no longer the code that performs authentication.
const decoys = [
  ['C-a auth flow moved to an uninspected file', f => ({ ...f, 'authBypass.ts': "import { PublicClientApplication } from '@azure/msal-browser'\nexport const c = new PublicClientApplication({ auth: { clientId: 'x' } })\nexport const go = () => c.loginPopup()\n" })],
  ['C-b runtime MSAL import masked by a preceding type import', f => ({ ...f, 'helper.ts': "import type { FC } from 'react'\nimport { PublicClientApplication } from '@azure/msal-browser'\nexport type X = FC\nexport const P = PublicClientApplication\n" })],
  ['C-c App bypasses the wrapper', f => ({ ...f, [APP]: f[APP].replace("from './entraAuth'", "from './entraAuthCore'") })],
  ['C-d wrapper builds its own MSAL configuration', f => ({ ...f, [AUTH_WRAPPER]: f[AUTH_WRAPPER].replace('createClient: (config) => new PublicClientApplication(config)', "createClient: () => new PublicClientApplication({ auth: { clientId: 'x' } })") })],
  ['C-e core imports MSAL runtime values', f => ({ ...f, [AUTH_CORE]: f[AUTH_CORE].replace('import type {', 'import {') })],
  ['C-f interaction detection unbound from MSAL class', f => ({ ...f, [AUTH_WRAPPER]: f[AUTH_WRAPPER].replace('error instanceof InteractionRequiredAuthError', 'Boolean(error)') })],
  ['C-g core file missing', f => { const g = { ...f }; delete g[AUTH_CORE]; return g }],
]
for (const [name, change] of decoys) {
  const changed = change(files)
  assert.notDeepEqual(changed, files, name + ': decoy did not apply')
  const errors = coverageViolations(changed)
  assert.ok(errors.length > 0, name + ': coverage policy passed vacuously')
  console.log(name + ': REJECTED (' + errors.join(', ') + ')')
}
console.log('C1 SOURCE MUTATION REGRESSION: ' + detected + '/7 detected; ' + decoys.length + '/' + decoys.length +
  ' coverage decoys rejected. Browser-level mutation proof is a separate gate.')
