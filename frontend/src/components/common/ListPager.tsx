'use client'

import { useState, type ReactNode } from 'react'
import { ChevronLeft, ChevronRight, ChevronsLeft, ListX } from 'lucide-react'
import { EmptyState } from '@/components/common/EmptyState'
import { Button } from '@/components/ui/button'
import { LIST_PAGING_UI_ENABLED } from '@/config/features'
import {
  currentCursor,
  currentStart,
  firstPage,
  isInvalidCursor,
  nextTrail,
  pageSummary,
  previousTrail,
  stepsFor,
  type CappedPage,
  type CursorTrail,
  type Truncation,
} from '@/lib/paging'
import { cn } from '@/lib/utils'

/** The cursor paging of one list (FE-33.1): what to send, and the moves. */
export interface CursorPaging {
  /** The cursor to send (`cursor`), or null on the first page. */
  cursor: string | null
  /** The index of the first row on screen, for "Showing n of total". */
  start: number
  /** How many pages after the first have been walked. */
  depth: number
  next: (page: CappedPage) => void
  previous: () => void
  first: () => void
}

/**
 * Cursor paging for one list. `scope` names the list and its server-side filters (e.g.
 * `JSON.stringify(query)`): a new scope starts again at the first page, so a cursor is only
 * ever sent with the filters it was issued for (D-59 §6).
 */
export function useCursorPaging(scope: string): CursorPaging {
  const [trail, setTrail] = useState<CursorTrail>(() => firstPage(scope))
  const steps = stepsFor(trail, scope)
  return {
    cursor: currentCursor(steps),
    start: currentStart(steps),
    depth: steps.length,
    next: (page) => setTrail(nextTrail(scope, steps, page)),
    previous: () => setTrail(previousTrail(scope, steps)),
    first: () => setTrail(firstPage(scope)),
  }
}

/**
 * FE-33.1: "Showing n of total" under a capped list, with Previous and Next. Next is offered
 * only while the server says `has_more` and gave a `next_cursor`; nothing here counts rows
 * itself or guesses that a list is complete.
 */
export function ListPager({
  page,
  paging,
  testId,
  busy = false,
  className,
}: {
  page: CappedPage
  paging: CursorPaging
  testId: string
  busy?: boolean
  className?: string
}) {
  const summary = pageSummary(page, paging.start)
  const canNext = page.has_more && !!page.next_cursor
  return (
    <div
      className={cn('mt-3 flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground', className)}
      data-testid={testId}
      data-total={summary.total}
      data-shown={summary.shown}
      data-has-more={page.has_more}
    >
      <span data-testid={`${testId}-summary`}>{summary.text}</span>
      {(summary.truncated || paging.depth > 0) && (
        <div className="flex gap-2">
          <Button type="button" variant="outline" size="sm" disabled={paging.depth === 0 || busy} onClick={paging.previous} data-testid={`${testId}-previous`}>
            <ChevronLeft className="h-3.5 w-3.5" />
            Previous
          </Button>
          <Button type="button" variant="outline" size="sm" disabled={!canNext || busy} onClick={() => paging.next(page)} data-testid={`${testId}-next`}>
            Next
            <ChevronRight className="h-3.5 w-3.5" />
          </Button>
        </div>
      )}
    </div>
  )
}

/**
 * Q18: the note under a lookup the server cut at its page size ("Showing the first 200 of 340
 * sources; the rest are not listed here."). Nothing when nothing was cut, and nothing with
 * NEXT_PUBLIC_LIST_PAGING_UI=off (the Phase 32 lookup exactly).
 */
export function TruncationNote({
  note,
  testId,
  className,
  enabled = LIST_PAGING_UI_ENABLED,
}: {
  note: Truncation | null
  testId: string
  className?: string
  enabled?: boolean
}) {
  if (!enabled || !note) return null
  return (
    <p className={cn('text-xs text-muted-foreground', className)} data-testid={testId} data-shown={note.shown} data-total={note.total}>
      {note.text}
    </p>
  )
}

/**
 * Q19: the error state of a list whose cursor the server refused (400 `invalid_cursor`, e.g.
 * after its signing key was rotated). "Back to first page" clears the cursor and keeps the
 * filters (the list's scope); nothing is retried until the user chooses it.
 */
export function CursorRefused({ paging, cid, testId }: { paging: Pick<CursorPaging, 'first'>; cid: string | null; testId: string }) {
  return (
    <div data-testid={`${testId}-cursor-refused`}>
      <EmptyState
        icon={ListX}
        title="This page can no longer be read"
        description={`The server refused the page's cursor (invalid_cursor), for example because its signing key was rotated. Your filters are kept.${cid ? ` Correlation ID: ${cid}` : ''}`}
        action={
          <Button type="button" size="sm" variant="outline" onClick={() => paging.first()} data-testid={`${testId}-first-page`}>
            <ChevronsLeft className="mr-1 h-3.5 w-3.5" />
            Back to first page
          </Button>
        }
      />
    </div>
  )
}

/**
 * A list's error body for a refused cursor, or null for any other failure (the list then shows
 * its own error state). With NEXT_PUBLIC_LIST_PAGING_UI=off no cursor is ever sent: always null.
 */
export function cursorRefusedBody(failure: unknown, paging: Pick<CursorPaging, 'first'>, testId: string, enabled: boolean = LIST_PAGING_UI_ENABLED): ReactNode | null {
  if (!enabled || !isInvalidCursor(failure)) return null
  const cid = (failure as { cid?: string | null; correlationId?: string | null }).cid ?? (failure as { correlationId?: string | null }).correlationId ?? null
  return <CursorRefused paging={paging} cid={cid} testId={testId} />
}
