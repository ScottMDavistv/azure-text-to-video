import test from 'node:test'
import assert from 'node:assert/strict'
import { shouldPauseStatusPolling, MAX_AUTOMATIC_POLL_MS } from '../src/statusPolling.mjs'
test('automatic observation pauses at its time boundary without declaring failure', () => {
  assert.equal(shouldPauseStatusPolling(0, 0), false)
  assert.equal(shouldPauseStatusPolling(MAX_AUTOMATIC_POLL_MS - 1, 0), false)
  assert.equal(shouldPauseStatusPolling(MAX_AUTOMATIC_POLL_MS, 0), true)
})
test('repeated failures pause; successful checks can reset the streak', () => {
  assert.equal(shouldPauseStatusPolling(1000, 2), false)
  assert.equal(shouldPauseStatusPolling(1000, 3), true)
  assert.equal(shouldPauseStatusPolling(1000, 0), false)
})
test('unknown observation counters fail closed', () => {
  for (const elapsed of [NaN, Infinity, -1]) assert.equal(shouldPauseStatusPolling(elapsed, 0), true)
  for (const count of [NaN, Infinity, -1, 0.5]) assert.equal(shouldPauseStatusPolling(0, count), true)
})
