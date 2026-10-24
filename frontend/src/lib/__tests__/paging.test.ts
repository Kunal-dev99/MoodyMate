import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { Fragment, createElement, isValidElement, type ReactElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import { ApiError, parseRetryAfter } from '@/api/client'
import { CursorRefused, ListPager, TruncationNote, cursorRefusedBody, type CursorPaging } from '@/components/common/ListPager'
import { loadFailure } from '@/components/common/load'
import {
  cursorQuery,
  currentCursor,
  currentStart,
  firstPage,
  isInvalidCursor,
  nextTrail,
  pageSummary,
  previousTrail,
  stepsFor,
  truncationOf,
  type CappedPage,
} from '@/lib/paging'
import { refusalOf } from '@/lib/refusal'
import { RETRY_REFUSAL_TITLES, retryAfterOf, retryText } from '@/lib/retry'

/*
 * FE-33.1: the list helper (D-59 §6) and the Retry-After helpers. Every capped page of the
 * contract carries the fields the helper reads; the test checks that against openapi.json so a
 * list cannot lose them unnoticed.
 */

const openapi = JSON.parse(readFileSync(fileURLToPath(new URL('../../../../backend/openapi.json', import.meta.url)), 'utf8'))

const page = (over: Partial<CappedPage> & { n?: number } = {}): CappedPage => {
  const { n = 2, ...rest } = over
  return { items: Array.from({ length: n }, (_, i) => i), total: 3, has_more: true, next_cursor: 'c1.mac', offset: 0, ...rest }
}

describe('FE-33.1 the capped-list helper', () => {
  it('every capped page in the contract carries total, has_more and next_cursor (and its lists take cursor)', () => {
    const schemas = openapi.components.schemas as Record<string, { properties?: Record<string, unknown>; required?: string[] }>
    const capped = Object.entries(schemas).filter(([, s]) => s.properties && 'has_more' in s.properties)
    expect(capped.length).toBeGreaterThanOrEqual(17)
    for (const [name, s] of capped) expect(s.required, name).toEqual(expect.arrayContaining(['items', 'total', 'has_more', 'next_cursor']))
    const lists = Object.values(openapi.paths as Record<string, Record<string, { parameters?: { name: string }[] }>>)
      .flatMap((ops) => Object.values(ops))
      .filter((op) => op.parameters?.some((p) => p.name === 'cursor'))
    expect(lists.length).toBe(capped.length)
  })

  it('3 rows with limit 2: "Showing 2 of 3", then the cursor gives the 3rd row', () => {
    const scope = JSON.stringify({ status: 'RUNNING' })
    const first = page()
    expect(pageSummary(first)).toEqual({ shown: 2, total: 3, from: 1, to: 2, truncated: true, text: 'Showing 2 of 3 (rows 1–2)' })
    let trail = firstPage(scope)
    expect(currentCursor(stepsFor(trail, scope))).toBeNull()
    expect(cursorQuery(null)).toEqual({}) // the first page sends neither cursor nor offset
    trail = nextTrail(scope, stepsFor(trail, scope), first)
    const steps = stepsFor(trail, scope)
    expect(currentCursor(steps)).toBe('c1.mac')
    expect(currentStart(steps)).toBe(2)
    expect(cursorQuery(currentCursor(steps))).toEqual({ cursor: 'c1.mac' }) // the cursor replaces offset
    const last = page({ n: 1, has_more: false, next_cursor: null, offset: 2 })
    expect(pageSummary(last, currentStart(steps)).text).toBe('Showing 1 of 3 (rows 3–3)')
    // No has_more, no next page: the trail does not move.
    expect(nextTrail(scope, steps, last)).toEqual({ scope, steps })
    expect(previousTrail(scope, steps)).toEqual(firstPage(scope))
  })

  it('a list that fits says "Showing n of total" without a range; a page without offset uses the trail', () => {
    expect(pageSummary(page({ n: 3, has_more: false, next_cursor: null })).text).toBe('Showing 3 of 3')
    expect(pageSummary(page({ n: 0, total: 0, has_more: false, next_cursor: null })).text).toBe('Showing 0 of 0')
    // ProposalList has no offset: the start comes from the trail.
    const noOffset = { items: [1], total: 201, has_more: false, next_cursor: null }
    expect(pageSummary(noOffset, 200)).toMatchObject({ from: 201, to: 201, text: 'Showing 1 of 201 (rows 201–201)' })
    const trail = nextTrail('s', [], { items: new Array(200).fill(0), total: 201, has_more: true, next_cursor: 'p2' })
    expect(currentStart(trail.steps)).toBe(200)
  })

  it('a cursor never travels to other filters or another list: a new scope starts at page one', () => {
    const trail = nextTrail('status=RUNNING', [], page())
    expect(stepsFor(trail, 'status=RUNNING')).toHaveLength(1)
    expect(stepsFor(trail, 'status=FAILED')).toEqual([])
    expect(currentCursor(stepsFor(trail, 'status=FAILED'))).toBeNull()
  })

  it('renders "Showing n of total" with Next only while the server says has_more', () => {
    const paging = (depth: number): CursorPaging => ({ cursor: depth ? 'c1.mac' : null, start: depth ? 2 : 0, depth, next: () => {}, previous: () => {}, first: () => {} })
    const disabled = (html: string, id: string) => {
      const tag = new RegExp(`<button[^>]*data-testid="${id}"[^>]*>`).exec(html)?.[0]
      expect(tag, id).toBeTruthy()
      return /\sdisabled=""/.test(tag!)
    }
    const firstHtml = renderToStaticMarkup(createElement(ListPager, { page: page(), paging: paging(0), testId: 'run-pager' }))
    expect(firstHtml).toContain('Showing 2 of 3 (rows 1–2)')
    expect(firstHtml).toContain('data-has-more="true"')
    expect(disabled(firstHtml, 'run-pager-previous')).toBe(true)
    expect(disabled(firstHtml, 'run-pager-next')).toBe(false)
    const lastHtml = renderToStaticMarkup(createElement(ListPager, { page: page({ n: 1, has_more: false, next_cursor: null, offset: 2 }), paging: paging(1), testId: 'run-pager' }))
    expect(lastHtml).toContain('Showing 1 of 3 (rows 3–3)')
    expect(disabled(lastHtml, 'run-pager-next')).toBe(true)
    expect(disabled(lastHtml, 'run-pager-previous')).toBe(false)
    // A list that fits shows the count and no controls.
    const fits = renderToStaticMarkup(createElement(ListPager, { page: page({ n: 3, has_more: false, next_cursor: null }), paging: paging(0), testId: 'run-pager' }))
    expect(fits).toContain('Showing 3 of 3')
    expect(fits).not.toContain('run-pager-next')
  })
})

/** The element with `data-testid` in a React element tree (props walked, components not rendered). */
function findByTestId(node: unknown, testId: string): ReactElement<Record<string, unknown>> | null {
  if (Array.isArray(node)) return node.map((n) => findByTestId(n, testId)).find(Boolean) ?? null
  if (!isValidElement(node)) return null
  const props = node.props as Record<string, unknown>
  if (props['data-testid'] === testId) return node as ReactElement<Record<string, unknown>>
  for (const value of Object.values(props)) {
    const hit = findByTestId(value, testId)
    if (hit) return hit
  }
  return null
}

describe('FE-33.1 lookups and refused cursors (Q18, Q19)', () => {
  it('Q18: a lookup the server cut at its page size says so; one that fits says nothing; off, nothing', () => {
    const sources = { items: new Array(200).fill(0), total: 340, has_more: true, next_cursor: 'c2.mac', offset: 0 }
    const cut = truncationOf(sources, 'sources')
    expect(cut).toEqual({ shown: 200, total: 340, text: 'Showing the first 200 of 340 sources; the rest are not listed here.' })
    // A short page against its total is cut too, whatever has_more says (fail closed).
    expect(truncationOf({ items: [1], total: 2, has_more: false, next_cursor: null }, 'imports')).toMatchObject({ shown: 1, total: 2 })
    expect(truncationOf(page({ n: 3, has_more: false, next_cursor: null }), 'sources')).toBeNull()
    // One page per status (the baseline lookup): the counts add up; cut if any page is.
    const fits = page({ n: 2, total: 2, has_more: false, next_cursor: null })
    expect(truncationOf([fits, page({ n: 2, total: 5 })], 'runs')?.text).toBe('Showing the first 4 of 7 runs; the rest are not listed here.')
    expect(truncationOf([fits, fits], 'runs')).toBeNull()
    // The note under the lookup, with the server's counts; nothing when nothing was cut or the switch is off.
    const html = renderToStaticMarkup(createElement(TruncationNote, { note: cut, testId: 'filter-source-truncated' }))
    expect(html).toContain('data-testid="filter-source-truncated"')
    expect(html).toContain('data-shown="200"')
    expect(html).toContain('data-total="340"')
    expect(html).toContain(cut!.text)
    expect(renderToStaticMarkup(createElement(TruncationNote, { note: null, testId: 'x' }))).toBe('')
    expect(renderToStaticMarkup(createElement(TruncationNote, { note: cut, testId: 'x', enabled: false }))).toBe('')
  })

  it('Q19: a 400 invalid_cursor offers "Back to first page", which clears the cursor and keeps the filters; nothing retries by itself', () => {
    const err = (status: number, code: string) => new ApiError(status, { code, message: `${code} message`, details: null, correlation_id: 'cid-19', retryable: false }, null)
    const refused = loadFailure(err(400, 'invalid_cursor'))
    expect(refused).toEqual({ kind: 'not_found', status: 400, cid: 'cid-19', code: 'invalid_cursor' })
    expect(isInvalidCursor(refused)).toBe(true)
    expect(isInvalidCursor(err(400, 'invalid_cursor'))).toBe(true)
    // Any other refusal keeps its Phase 32 shape and the list's own error state.
    const other = loadFailure(err(400, 'invalid_request'))
    expect(other).toEqual({ kind: 'not_found', status: 400, cid: 'cid-19' })
    expect(isInvalidCursor(other)).toBe(false)
    expect(isInvalidCursor(null)).toBe(false)

    const paging = { cursor: 'c1.mac', start: 2, depth: 1, next: vi.fn(), previous: vi.fn(), first: vi.fn() }
    const body = cursorRefusedBody(refused, paging, 'run-pager')
    const html = renderToStaticMarkup(createElement(Fragment, null, body))
    expect(html).toContain('data-testid="run-pager-cursor-refused"')
    expect(html).toContain('Back to first page')
    expect(html).toContain('Correlation ID: cid-19')
    // Showing the error state moves nothing: no automatic retry, no reset.
    expect(paging.first).not.toHaveBeenCalled()
    // The action resets the trail to page one and nothing else.
    const button = findByTestId(CursorRefused((body as ReactElement<Parameters<typeof CursorRefused>[0]>).props), 'run-pager-first-page')
    expect(button).not.toBeNull()
    ;(button!.props.onClick as () => void)()
    expect(paging.first).toHaveBeenCalledTimes(1)
    expect(paging.next).not.toHaveBeenCalled()
    expect(paging.previous).not.toHaveBeenCalled()
    // What first() sets (useCursorPaging): the same scope, so the same filters, and no cursor.
    const scope = JSON.stringify({ status: 'FAILED' })
    const reset = firstPage(scope)
    expect(reset.scope).toBe(scope)
    expect(cursorQuery(currentCursor(stepsFor(reset, scope)))).toEqual({})
    // Any other failure, or NEXT_PUBLIC_LIST_PAGING_UI=off (no cursor is ever sent): the list's own state.
    expect(cursorRefusedBody(other, paging, 'run-pager')).toBeNull()
    expect(cursorRefusedBody(refused, paging, 'run-pager', false)).toBeNull()
  })
})

describe('FE-33.1 Retry-After', () => {
  const err = (status: number, code: string, retryAfter: number | null, details: Record<string, unknown> | null = null) =>
    new ApiError(status, { code, message: `${code} message`, details, correlation_id: 'cid', retryable: true }, retryAfter)

  it('parses delay-seconds and HTTP dates; anything else is no retry time', () => {
    const now = Date.parse('2026-10-24T10:00:00Z')
    expect(parseRetryAfter('30', now)).toBe(30)
    expect(parseRetryAfter(' 0 ', now)).toBe(0)
    expect(parseRetryAfter('Sat, 24 Oct 2026 10:00:45 GMT', now)).toBe(45)
    expect(parseRetryAfter('Sat, 24 Oct 2026 09:00:00 GMT', now)).toBe(0)
    expect(parseRetryAfter(null, now)).toBeNull()
    expect(parseRetryAfter('-5', now)).toBeNull()
    expect(parseRetryAfter('soon', now)).toBeNull()
    expect(parseRetryAfter('12.5', now)).toBeNull()
  })

  it('takes the header first, then details.retry_after_seconds', () => {
    expect(retryAfterOf(err(503, 'workers_busy', 7, { retry_after_seconds: 99 }))).toBe(7)
    expect(retryAfterOf(err(429, 'rate_limited', null, { retry_after_seconds: 12 }))).toBe(12)
    expect(retryAfterOf(err(429, 'rate_limited', null, { retry_after_seconds: 'x' }))).toBeNull()
    expect(retryAfterOf(new Error('x'))).toBeNull()
    expect([0, 1, 59, 60, 61, 600].map(retryText)).toEqual(['Retry now.', 'Retry in 1 s.', 'Retry in 59 s.', 'Retry in 1 min.', 'Retry in 2 min.', 'Retry in 10 min.'])
  })

  it('both refusals carry their title and retry time; with NEXT_PUBLIC_RETRY_AFTER_UI=off the Phase 32 refusal exactly', () => {
    for (const [status, code] of [
      [503, 'workers_busy'],
      [429, 'rate_limited'],
    ] as const) {
      expect(refusalOf(err(status, code, 5), 'Could not submit the run')).toEqual({
        title: RETRY_REFUSAL_TITLES[code],
        status,
        code,
        message: `${code} message`,
        cid: 'cid',
        retryAfterSeconds: 5,
      })
      const off = refusalOf(err(status, code, 5), 'Could not submit the run', {}, false)
      expect(off).toStrictEqual({ title: 'Could not submit the run', status, code, message: `${code} message`, cid: 'cid' })
    }
    // An area's own title for the code wins; a refusal without Retry-After has no retry field.
    expect(refusalOf(err(429, 'rate_limited', null), 'x', { rate_limited: 'Slow down' })).toStrictEqual({
      title: 'Slow down',
      status: 429,
      code: 'rate_limited',
      message: 'rate_limited message',
      cid: 'cid',
    })
  })
})
