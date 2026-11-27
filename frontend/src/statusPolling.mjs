// Client observation limits only: never infer job failure or retry a mutation.
export const MAX_AUTOMATIC_POLL_MS = 10 * 60 * 1000
export const MAX_CONSECUTIVE_STATUS_ERRORS = 3
export function shouldPauseStatusPolling(elapsedMs, consecutiveErrors) {
  return !Number.isFinite(elapsedMs) || elapsedMs < 0 ||
    !Number.isInteger(consecutiveErrors) || consecutiveErrors < 0 ||
    elapsedMs >= MAX_AUTOMATIC_POLL_MS ||
    consecutiveErrors >= MAX_CONSECUTIVE_STATUS_ERRORS
}
