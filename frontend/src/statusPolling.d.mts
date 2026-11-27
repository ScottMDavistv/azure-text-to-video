export const MAX_AUTOMATIC_POLL_MS: number
export const MAX_CONSECUTIVE_STATUS_ERRORS: number
export function shouldPauseStatusPolling(elapsedMs: number, consecutiveErrors: number): boolean
