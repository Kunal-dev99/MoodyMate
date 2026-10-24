import { ApiError } from '@/api/client'
import { RETRY_AFTER_UI_ENABLED } from '@/config/features'
import { RETRY_REFUSAL_TITLES, retryAfterOf } from '@/lib/retry'

/**
 * FE-29.1: a refused request as shown inline: the backend's envelope (status, code, message,
 * correlation ID), never reworded away. Each area keeps only its own code → title map and calls
 * `refusalOf`; the shape and the fallback for a non-API error live here once.
 * FE-33.1: a retryable refusal that sent `Retry-After` also carries its retry time
 * (`retryAfterSeconds`, absent otherwise), unless NEXT_PUBLIC_RETRY_AFTER_UI=off.
 */
export interface Refusal {
  title: string
  status: number | null
  code: string
  message: string
  cid: string | null
  retryAfterSeconds?: number
}

/** Titles for the documented error codes of one area; an unknown code gets the fallback. */
export type RefusalTitles = Readonly<Record<string, string>>

export function refusalOf(err: unknown, fallback: string, titles: RefusalTitles = {}, retryUi: boolean = RETRY_AFTER_UI_ENABLED): Refusal {
  if (err instanceof ApiError) {
    const code = err.envelope.code
    const known = retryUi ? { ...RETRY_REFUSAL_TITLES, ...titles } : titles
    const title = Object.prototype.hasOwnProperty.call(known, code) ? known[code] : fallback
    const refusal: Refusal = { title, status: err.status, code, message: err.envelope.message, cid: err.correlationId }
    const retryAfter = retryUi ? retryAfterOf(err) : null
    if (retryAfter !== null) refusal.retryAfterSeconds = retryAfter
    return refusal
  }
  return { title: fallback, status: null, code: 'unexpected_error', message: 'Something went wrong.', cid: null }
}
