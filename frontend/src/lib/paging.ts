import { ApiError, type Schemas } from '@/api/client'

/**
 * FE-33.1: explicit truncation for every capped list (D-59 §6). A capped page carries
 * `total`, `has_more` and an opaque `next_cursor` (null on the last page); the UI says
 * "Showing n of total" and offers Next only when the server says more rows exist, by sending
 * that cursor back. The cursor is the server's: it is never built, parsed or changed here, and
 * it belongs to one list and one filter set, so a new filter set starts again at page one.
 */

/** The fields every generated capped page shares (taken from one of them, never re-typed). */
type Continuation = Pick<Schemas['RunPage'], 'total' | 'has_more' | 'next_cursor'>
/** A capped page: its rows, the continuation fields and, on most lists, the offset it read. */
export type CappedPage = Continuation & { items: readonly unknown[]; offset?: number }

/** One page after the first: the cursor that loads it and the index of its first row. */
export interface TrailStep {
  cursor: string
  start: number
}

/** The pages walked so far in one scope (a list and its filters); empty = the first page. */
export interface CursorTrail {
  scope: string
  steps: readonly TrailStep[]
}

export const firstPage = (scope: string): CursorTrail => ({ scope, steps: [] })

/** The steps of `scope`: a trail of another scope (other filters, another list) is dropped. */
export const stepsFor = (trail: CursorTrail, scope: string): readonly TrailStep[] => (trail.scope === scope ? trail.steps : [])

/** The cursor of the page on screen; null on the first page (then `offset` is not sent either). */
export const currentCursor = (steps: readonly TrailStep[]): string | null => steps.at(-1)?.cursor ?? null

/** The index of the first row of the page on screen, as the trail knows it. */
export const currentStart = (steps: readonly TrailStep[]): number => steps.at(-1)?.start ?? 0

/**
 * The trail after "Next": the server's `next_cursor` is pushed, and the next page starts after
 * the rows of this one. Without `has_more` and a cursor there is no next page: unchanged.
 */
export function nextTrail(scope: string, steps: readonly TrailStep[], page: CappedPage): CursorTrail {
  if (!page.has_more || !page.next_cursor) return { scope, steps }
  const start = (page.offset ?? currentStart(steps)) + page.items.length
  return { scope, steps: [...steps, { cursor: page.next_cursor, start }] }
}

/** The trail after "Previous": one page back (the first page has no cursor). */
export const previousTrail = (scope: string, steps: readonly TrailStep[]): CursorTrail => ({ scope, steps: steps.slice(0, -1) })

/**
 * The query part that selects the page: `{ cursor }` past the first page, else nothing (the
 * list's default offset 0). A cursor replaces `offset`; the two are never sent together.
 */
export const cursorQuery = (cursor: string | null): { cursor?: string } => (cursor ? { cursor } : {})

/** What the pager says about one page. */
export interface PageSummary {
  shown: number
  total: number
  /** 1-based index of the first and last row shown (0 and 0 for an empty page). */
  from: number
  to: number
  /** Rows exist beyond this page, before or after it. */
  truncated: boolean
  text: string
}

/**
 * "Showing n of total" for a page that starts at `start` (the server's `offset` wins when the
 * page has one), with the row range once the list is truncated. The total is the server's.
 */
export function pageSummary(page: CappedPage, start: number = 0): PageSummary {
  const first = page.offset ?? start
  const shown = page.items.length
  const total = page.total
  const from = shown === 0 ? 0 : first + 1
  const to = shown === 0 ? 0 : first + shown
  const truncated = page.has_more || first > 0
  const text = truncated && shown > 0 ? `Showing ${shown} of ${total} (rows ${from}–${to})` : `Showing ${shown} of ${total}`
  return { shown, total, from, to, truncated, text }
}

/**
 * Q18: a lookup (a dropdown's options, a picker's candidates, the search for one row) reads one
 * capped page and never pages it. When the server says more rows exist, the UI says so instead
 * of presenting the page as the whole list. Several pages (one per status) add up. The counts are
 * the server's rows read, before any filtering on screen.
 */
export interface Truncation {
  shown: number
  total: number
  text: string
}

/** The truncation note of a lookup, or null when the server returned every row. */
export function truncationOf(pages: CappedPage | readonly CappedPage[], what: string): Truncation | null {
  const list: readonly CappedPage[] = 'items' in pages ? [pages as CappedPage] : (pages as readonly CappedPage[])
  // The server's has_more decides; a page shorter than its total counts as cut too (fail closed).
  const cut = list.some((p) => p.has_more || (p.offset ?? 0) + p.items.length < p.total)
  if (!cut) return null
  const shown = list.reduce((n, p) => n + p.items.length, 0)
  const total = list.reduce((n, p) => n + p.total, 0)
  return { shown, total, text: `Showing the first ${shown} of ${total} ${what}; the rest are not listed here.` }
}

/** The server's refusal of a cursor it no longer accepts (D-59 §6). */
export const INVALID_CURSOR = 'invalid_cursor' as const

/**
 * Q19: whether a failed read is the server refusing the page's cursor (400 `invalid_cursor`, for
 * example after its signing key was rotated): an ApiError, or a load failure that kept the code.
 * Nothing retries it; the list offers "Back to first page" and the user chooses.
 */
export function isInvalidCursor(failure: unknown): boolean {
  if (failure instanceof ApiError) return failure.status === 400 && failure.envelope.code === INVALID_CURSOR
  if (typeof failure !== 'object' || failure === null) return false
  const f = failure as { status?: unknown; code?: unknown }
  return f.status === 400 && f.code === INVALID_CURSOR
}
