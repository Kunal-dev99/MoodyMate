import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Schemas } from '@/api/client'
import { RatingStatusPill } from '@/components/credit/ratings/RatingStatusPill'
import { RATING_STATUS_STYLE, type ObligorRating } from '@/components/credit/ratings/ratings'
import {
  COMMENT_MAX,
  DEFAULT_STATUS,
  PAGE_SIZE,
  STATUS_FILTERS,
  commentProblem,
  decisionBody,
  decisionGate,
  queueQuery,
  statusFilterOf,
} from '@/components/credit/review/review'

/*
 * FE-20.2: the ratings review queue at /domain/ratings. Fixtures use the generated types
 * (ObligorRatingOut, Permission); enums and limits are tested against openapi.json (D-23).
 */

const openapi = JSON.parse(readFileSync(fileURLToPath(new URL('../../../../../../backend/openapi.json', import.meta.url)), 'utf8'))
const schemas = openapi.components.schemas
const enumOf = (name: string): string[] => [...schemas[name].enum].sort()

const ME = { user_id: '11111111-1111-4111-8111-111111111111' }
const OTHER = '99999999-9999-4999-8999-999999999999'
type Permission = Schemas['Permission']
const holding =
  (...ps: Permission[]) =>
  (p: Permission) =>
    ps.includes(p)

const rating = (over: Partial<ObligorRating> = {}): Pick<ObligorRating, 'status' | 'proposer_user_id'> => ({
  status: 'IN_REVIEW',
  proposer_user_id: OTHER,
  ...over,
})

afterEach(() => {
  vi.unstubAllEnvs()
  vi.doUnmock('next/navigation')
  vi.resetModules()
})

describe('rating-status pill (FE-20.2 Done-when)', () => {
  const statuses = enumOf('ObligorRatingStatus')

  it('styles exactly the openapi.json ObligorRatingStatus enum', () => {
    expect(Object.keys(RATING_STATUS_STYLE).sort()).toEqual(statuses)
    expect(statuses).toEqual(['ACTIVE', 'IN_REVIEW', 'REJECTED', 'RETIRED', 'SUPERSEDED'])
  })

  it.each(enumOf('ObligorRatingStatus'))('renders %s as the server sent it, with its label and style', (status) => {
    const html = renderToStaticMarkup(createElement(RatingStatusPill, { status: status as ObligorRating['status'], testId: 'review-status' }))
    const style = RATING_STATUS_STYLE[status as ObligorRating['status']]
    expect(html).toContain(`data-status="${status}"`)
    expect(html).toContain('data-testid="review-status"')
    expect(html).toContain(`>${style.label}</span>`)
    expect(html).toContain(style.className)
  })

  it('IN_REVIEW is never styled as a success (no run or rating shows successful before the server says so)', () => {
    expect(RATING_STATUS_STYLE.IN_REVIEW.className).not.toBe(RATING_STATUS_STYLE.ACTIVE.className)
    expect(RATING_STATUS_STYLE.REJECTED.className).toBe('status-failed')
  })

  it('the status filter offers every enum value plus "all", IN_REVIEW by default', () => {
    expect(STATUS_FILTERS.filter((s) => s !== 'ALL').sort()).toEqual(statuses)
    expect(STATUS_FILTERS).toContain('ALL')
    expect(DEFAULT_STATUS).toBe('IN_REVIEW')
  })
})

describe('the queue query', () => {
  it.each([
    [null, 'IN_REVIEW'],
    ['', 'IN_REVIEW'],
    ['ACTIVE', 'ACTIVE'],
    ['SUPERSEDED', 'SUPERSEDED'],
    ['ALL', 'ALL'],
    ['active', 'IN_REVIEW'],
    ['DRAFT', 'IN_REVIEW'],
  ])('?status=%s reads as %s', (raw, expected) => expect(statusFilterOf(raw)).toBe(expected))

  it('sends the status, or none for "all", and pages within the API maximum', () => {
    expect(queueQuery('IN_REVIEW', 0)).toEqual({ limit: PAGE_SIZE, offset: 0, status: 'IN_REVIEW' })
    expect(queueQuery('ALL', 50)).toEqual({ limit: PAGE_SIZE, offset: 50 })
    const limit = openapi.paths['/api/v1/obligor-ratings'].get.parameters.find((p: { name: string }) => p.name === 'limit').schema
    expect(PAGE_SIZE).toBeLessThanOrEqual(limit.maximum)
  })
})

describe('who is offered Approve and Reject (cosmetic, D-04)', () => {
  it('an approver who did not propose it, on an IN_REVIEW version', () => {
    expect(decisionGate(rating(), ME, holding('domain:read', 'rating:approve'))).toBe('allowed')
  })

  it('never the proposer, even holding rating:approve (409 self_approval)', () => {
    expect(decisionGate(rating({ proposer_user_id: ME.user_id }), ME, holding('rating:approve'))).toBe('self_approval')
  })

  it('not risk_analyst (rating:propose without rating:approve: the server answers 403)', () => {
    expect(decisionGate(rating(), ME, holding('domain:read', 'rating:propose'))).toBe('no_permission')
  })

  it.each(['ACTIVE', 'REJECTED', 'SUPERSEDED', 'RETIRED'] as const)('not on a %s version (409 invalid_status)', (status) => {
    expect(decisionGate(rating({ status }), ME, holding('rating:approve'))).toBe('not_in_review')
  })
})

describe('the required decision comment', () => {
  it('is capped at the openapi.json ObligorRatingDecision.comment maxLength', () => {
    expect(COMMENT_MAX).toBe(schemas.ObligorRatingDecision.properties.comment.maxLength)
    expect(schemas.ObligorRatingDecision.required.sort()).toEqual(['comment', 'decision'])
    expect(enumOf('ProposalDecision')).toEqual(['APPROVE', 'REJECT'])
  })

  it.each([
    ['', 'A comment is required.'],
    ['   \n ', 'A comment is required.'],
    ['x'.repeat(COMMENT_MAX + 1), 'At most 1,000 characters (1,001 now).'],
  ])('refuses %j', (comment, problem) => {
    expect(commentProblem(comment)).toBe(problem)
    expect(decisionBody('APPROVE', comment)).toBeNull()
  })

  it('accepts 1 to 1,000 characters after trimming, and sends the trimmed text', () => {
    expect(commentProblem('x'.repeat(COMMENT_MAX))).toBeNull()
    expect(commentProblem(`  ${'x'.repeat(COMMENT_MAX)}  `)).toBeNull()
    expect(decisionBody('REJECT', '  Rationale not supported by the financials. ')).toEqual({ decision: 'REJECT', comment: 'Rationale not supported by the financials.' })
  })
})

describe('NEXT_PUBLIC_RATINGS_UI (D-02)', () => {
  const notFound = vi.fn(() => {
    throw new Error('NEXT_NOT_FOUND')
  })
  const child = createElement('p', null, 'queue')
  const render = async () => {
    vi.doMock('next/navigation', () => ({ notFound }))
    vi.resetModules()
    const { RatingsReviewGate } = await import('@/components/credit/review/RatingsReviewGate')
    return () => renderToStaticMarkup(createElement(RatingsReviewGate, null, child))
  }

  it('renders /domain/ratings while on', async () => {
    notFound.mockClear()
    expect((await render())()).toBe('<p>queue</p>')
    expect(notFound).not.toHaveBeenCalled()
  })

  it.each(['NEXT_PUBLIC_RATINGS_UI', 'NEXT_PUBLIC_CREDIT_DOMAIN_UI'])('makes /domain/ratings a 404 while %s=off', async (flag) => {
    notFound.mockClear()
    vi.stubEnv(flag, 'off')
    expect(await render()).toThrow('NEXT_NOT_FOUND')
    expect(notFound).toHaveBeenCalledTimes(1)
  })
})

/**
 * The queue's files: no value parsed to a number, no hand-written payload type, and exactly one
 * write (the decision POST).
 */
describe('review queue code hygiene (FE-20.2)', () => {
  const root = join(__dirname, '..', '..', '..', '..')
  const files = [
    'components/credit/review/review.ts',
    'components/credit/review/RatingsReviewGate.tsx',
    'components/credit/review/RatingsReviewQueue.tsx',
    'components/credit/review/RatingDecisionDialog.tsx',
    'app/(platform)/domain/ratings/layout.tsx',
    'app/(platform)/domain/ratings/page.tsx',
  ]
  const PARSING = [/parseFloat/, /parseInt/, /\bNumber\s*\(/, /\bNumber\./, /\bMath\./, /\.toFixed\s*\(/, /NumberFormat/, /\bBigInt\s*\(/]
  const HAND_WRITTEN = /\b(rating_id|counterparty_id|counterparty_key|grade|grade_rank|effective_from|rationale|comment|decision|status|proposer_user_id)\??\s*:\s*(string|number|boolean)\b/

  it.each(files)('%s never parses a value to a number', (file) => {
    const source = readFileSync(join(root, file), 'utf8')
    for (const pattern of PARSING) expect(source, `${file} matches ${pattern}`).not.toMatch(pattern)
  })

  it.each([...files, '../e2e/ratings-review.spec.ts'])('%s declares no hand-written payload type', (file) => {
    expect(readFileSync(join(root, file), 'utf8')).not.toMatch(HAND_WRITTEN)
  })

  it('the only write is POST /obligor-ratings/{id}/approval', () => {
    const all = files.map((f) => readFileSync(join(root, f), 'utf8')).join('\n')
    expect(all.match(/api\.(POST|PUT|PATCH|DELETE)\('[^']+'/g)).toEqual(["api.POST('/api/v1/obligor-ratings/{rating_id}/approval'"])
  })
})
