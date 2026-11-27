export class VideoApiError extends Error { status: number; outcomeUnknown?: boolean; constructor(status: number, message: string) }
export type VideoJob = { job_id: string; prompt: string; status: 'created' | 'queued' | 'processing' | 'completed' | 'failed' | 'cancelled'; created_at: string }
export type VideoMetadata = { job_id: string; status: string; available: true; content_type: 'video/mp4'; size_bytes: number; download_endpoint: string }
export function validateProjectId(value: string): string
export function validateJob(data: unknown): VideoJob
export function createVideoApi(options: {
  baseUrl?: string
  tokenProvider: () => Promise<string>
  fetcher?: typeof fetch
  requestTimeoutMs?: number
}): {
  submit(projectId: string, prompt: string, signal?: AbortSignal): Promise<VideoJob>
  queue(projectId: string, jobId: string, signal?: AbortSignal): Promise<{job_id: string;status: 'queued';dispatched: boolean}>
  status(projectId: string, jobId: string, signal?: AbortSignal): Promise<VideoJob>
  metadata(projectId: string, jobId: string, signal?: AbortSignal): Promise<VideoMetadata>
  download(projectId: string, jobId: string, signal?: AbortSignal): Promise<Blob>
}
