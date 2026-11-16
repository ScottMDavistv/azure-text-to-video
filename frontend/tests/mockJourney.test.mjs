import test from 'node:test'
import assert from 'node:assert/strict'
import { SCENES, TOTAL_SECONDS, MOCK_STAGES, validatePrompt, createMockJob, advanceMockJob, mockProgress } from '../src/mockJourney.mjs'
test('five ordered synthetic scenes total exactly 300 seconds', () => {
  assert.equal(SCENES.length, 5)
  assert.equal(TOTAL_SECONDS, 300)
  assert.ok(SCENES.every(s => s.duration > 0))
})
test('prompt validation rejects empty, short and oversized text', () => {
  assert.match(validatePrompt('  short  '), /10/)
  assert.match(validatePrompt('x'.repeat(2001)), /2,000/)
  assert.equal(validatePrompt('  A film about alpine forests  '), null)
})
test('mock job has a deterministic ID and never leaves bounded stages', () => {
  let job = createMockJob('  A film about alpine forests  ', 7)
  assert.equal(job.id, 'demo-007')
  assert.equal(job.prompt, 'A film about alpine forests')
  assert.equal(mockProgress(job), 0)
  for (let i = 0; i < 30; i++) job = advanceMockJob(job)
  assert.equal(MOCK_STAGES[job.stage], 'completed')
  assert.equal(mockProgress(job), 100)
  assert.deepEqual(advanceMockJob(job), job)
})
test('mock submission fails closed on invalid input', () => {
  assert.throws(() => createMockJob('bad', 1), /at least 10/)
})
