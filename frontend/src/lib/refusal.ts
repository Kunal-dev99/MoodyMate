import { ApiError } from '@/api/client'

/**
 * FE-29.1: a refused request as shown inline: the backend's envelope (status, code, message,
 * correlation ID), never reworded away. Each area keeps only its own code → title map and calls
 * `refusalOf`; the shape and the fallback for a non-API error live here once.
 */
export interface Refusal {
  title: string
  status: number | null
  code: string
  message: string
  cid: string | null
}

/** Titles for the documented error codes of one area; an unknown code gets the fallback. */
export type RefusalTitles = Readonly<Record<string, string>>

export function refusalOf(err: unknown, fallback: string, titles: RefusalTitles = {}): Refusal {
  if (err instanceof ApiError) {
    const code = err.envelope.code
    const title = Object.prototype.hasOwnProperty.call(titles, code) ? titles[code] : fallback
    return { title, status: err.status, code, message: err.envelope.message, cid: err.correlationId }
  }
  return { title: fallback, status: null, code: 'unexpected_error', message: 'Something went wrong.', cid: null }
}
