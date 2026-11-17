export class VideoApiError extends Error { status: number; constructor(message: string, status?: number) }
export type Job = { job_id: string; status: 'created' | 'queued' | 'processing' | 'completed' | 'failed'; prompt?: string; created_at?: string }
export function validateApiBase(value: string): string
export function validJobId(value: unknown): boolean
export function createVideoApi(base: string, fetcher?: typeof fetch): {
  submit(prompt: string, token: string, signal?: AbortSignal): Promise<Job>
  queue(jobId: string, token: string, signal?: AbortSignal): Promise<{ job_id: string; status: string; dispatched?: boolean }>
  status(jobId: string, token: string, signal?: AbortSignal): Promise<Job>
  metadata(jobId: string, token: string, signal?: AbortSignal): Promise<{job_id: string; status: string; available: boolean; content_type: string; size_bytes: number; download_endpoint: string}>
  download(jobId: string, token: string, signal?: AbortSignal): Promise<Blob>
}
