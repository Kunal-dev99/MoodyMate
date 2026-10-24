import { ApiError } from '@/api/client'

/**
 * FE-33.1: the retry time of a retryable refusal, as the server sent it. 429 `rate_limited`
 * (D-53) and 503 `workers_busy` (D-59 §4) / `db_pool_exhausted` (BE-33.4) carry `Retry-After`
 * in whole seconds; the UI shows it and never retries sooner by itself. The wait is the
 * server's to enforce (D-04): the text only tells the user when to try again.
 */

/** Titles of the retryable "busy" refusals; an area's own title for the same code wins. */
export const RETRY_REFUSAL_TITLES: Readonly<Record<string, string>> = {
  rate_limited: 'Too many requests: wait before retrying',
  workers_busy: 'Every calculation worker is busy',
  db_pool_exhausted: 'The platform is busy',
}

/**
 * Whole seconds to wait before retrying: the `Retry-After` header, else the envelope's
 * `details.retry_after_seconds`; null when the refusal carries neither (or is not an API error).
 */
export function retryAfterOf(err: unknown): number | null {
  if (!(err instanceof ApiError)) return null
  if (err.retryAfterSeconds !== null) return err.retryAfterSeconds
  const details = err.envelope.details
  const value = details && !Array.isArray(details) ? details.retry_after_seconds : undefined
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : null
}

/** "Retry in 30 s." / "Retry in 2 min." (rounded up) / "Retry now." */
export function retryText(seconds: number): string {
  if (seconds <= 0) return 'Retry now.'
  if (seconds < 60) return `Retry in ${seconds} s.`
  return `Retry in ${Math.ceil(seconds / 60)} min.`
}
