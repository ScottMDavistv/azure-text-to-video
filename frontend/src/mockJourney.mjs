// Deliberately offline: this module must not call fetch, provider SDKs, or storage.
export const SCENES = Object.freeze([
  { title: 'Opening', duration: 60, description: 'Introduce the topic and establish context.' },
  { title: 'Context', duration: 60, description: 'Explain the core idea with supporting visuals.' },
  { title: 'Development', duration: 60, description: 'Explore the details in a structured sequence.' },
  { title: 'Application', duration: 60, description: 'Demonstrate a practical example.' },
  { title: 'Closing', duration: 60, description: 'Summarize and close the story.' },
])
export const TOTAL_SECONDS = SCENES.reduce((sum, scene) => sum + scene.duration, 0)
export const MOCK_STAGES = Object.freeze(['queued', 'planning', 'rendering', 'assembling', 'completed'])
export function validatePrompt(input) {
  const prompt = input.trim()
  if (prompt.length < 10) return 'Enter at least 10 characters.'
  if (prompt.length > 2000) return 'Limit your prompt to 2,000 characters.'
  return null
}
export function createMockJob(prompt, sequence) {
  const error = validatePrompt(prompt)
  if (error) throw new Error(error)
  return { id: `demo-${String(sequence).padStart(3, '0')}`, prompt: prompt.trim(), stage: 0 }
}
export function advanceMockJob(job) {
  return { ...job, stage: Math.min(job.stage + 1, MOCK_STAGES.length - 1) }
}
export function mockProgress(job) {
  return [0, 18, 48, 81, 100][job.stage]
}
