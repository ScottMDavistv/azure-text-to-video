export type MockJob = { id: string; prompt: string; stage: number }
export type Scene = { title: string; duration: number; description: string }
export const SCENES: ReadonlyArray<Scene>
export const TOTAL_SECONDS: number
export const MOCK_STAGES: ReadonlyArray<string>
export function validatePrompt(input: string): string | null
export function createMockJob(prompt: string, sequence: number): MockJob
export function advanceMockJob(job: MockJob): MockJob
export function mockProgress(job: MockJob): number
