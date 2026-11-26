import test from 'node:test'
import assert from 'node:assert/strict'
import { requireFreshPermission, requireMicrosoftPopupOrigin } from '../e2e/c1-operation-evidence.mjs'

const success = 'Delegated API permission acquired. User provisioning is still required.'
function permission({ prior = 'Signed in', next = success, enabledAfter = true } = {}) {
  let message = prior
  let clicks = 0
  let enabled = true
  return {
    button: { click: async () => { clicks++; message = next; enabled = enabledAfter } },
    status: { textContent: async () => message },
    expect: locator => ({
      toHaveText: async expected => assert.equal(await locator.textContent(), expected),
      toBeEnabled: async () => assert.equal(enabled, true),
    }),
    clicks: () => clicks,
  }
}

test('fresh permission result requires the new click and a completed operation', async () => {
  const f = permission()
  await requireFreshPermission(f.button, f.status, f.expect)
  assert.equal(f.clicks(), 1)
  const busy = permission({ enabledAfter: false })
  await assert.rejects(requireFreshPermission(busy.button, busy.status, busy.expect))
})

test('stale permission success cannot satisfy a new acquisition', async () => {
  const f = permission({ prior: success })
  await assert.rejects(requireFreshPermission(f.button, f.status, f.expect), /fresh API permission/)
  assert.equal(f.clicks(), 0)
  const unchanged = permission({ next: 'Signed in' })
  await assert.rejects(requireFreshPermission(unchanged.button, unchanged.status, unchanged.expect))
})

const authority = 'https://login.microsoftonline.com'
function popup({ navigated = authority, documentOrigin = authority, closed = false } = {}) {
  return {
    waitForURL: async (predicate, options) => {
      assert.equal(options.waitUntil, 'commit')
      if (!predicate(new URL(navigated))) throw new Error('private URL would be here')
    },
    evaluate: async (check, expected) => {
      // Execute the real predicate in a fixture document without touching globals.
      const invoke = new Function('window', 'expected', 'return (' + check + ')(expected)')
      return invoke({ location: { origin: documentOrigin } }, expected)
    },
    isClosed: () => closed,
  }
}

test('committed exact Microsoft document can be observed before cancellation', async () => {
  await requireMicrosoftPopupOrigin(popup())
})

test('closed, wrong-origin, and uncommitted popups cannot prove Microsoft was reached', async () => {
  for (const p of [
    popup({ navigated: 'about:blank', closed: true }),
    popup({ navigated: authority + '.example.invalid' }),
    popup({ navigated: authority + ':8443' }),
    popup({ navigated: 'http://login.microsoftonline.com' }),
    popup({ documentOrigin: 'null' }),
    popup({ closed: true }),
  ]) {
    await assert.rejects(requireMicrosoftPopupOrigin(p),
      { message: 'Microsoft authority was not verified before popup cancellation' })
  }
})
