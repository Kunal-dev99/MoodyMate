import { api, call, type Schemas } from '@/api/client'
import type { operations } from '@/api/schema'
import { RATING_STATUSES, isProposer, type ObligorRating, type ObligorRatingStatus } from '@/components/credit/ratings/ratings'

/*
 * FE-20.2: the ratings review queue's pure helpers (D-39 §3). Every payload type is the
 * generated one. Reading the queue needs `domain:read`; approving or rejecting needs
 * `rating:approve` and a comment. Every gate here is cosmetic: the server checks the permission
 * on each POST (403), and refuses self-approval, stale and non-IN_REVIEW versions (409, D-04).
 * Nothing here suggests or derives a grade (D-39 §7).
 */

export type RatingPage = Schemas['ObligorRatingPage']
export type RatingDecisionBody = Schemas['ObligorRatingDecision']
export type Decision = Schemas['ProposalDecision']
export type RatingQuery = NonNullable<operations['listObligorRatings']['parameters']['query']>
type Me = Schemas['Me']
type Has = (permission: Schemas['Permission']) => boolean

export const ratingsHref = '/domain/ratings'
export const PAGE_SIZE = 50

/** The limit BE-20.2 enforces (ObligorRatingDecision.comment maxLength; a test reads openapi.json). */
export const COMMENT_MAX = 1000

/**
 * The queue's status filter: IN_REVIEW by default (what waits for a decision). The other
 * statuses are shown on request: superseded and rejected versions are hidden by status, never
 * deleted. `ALL` sends no status.
 */
export type StatusFilter = ObligorRatingStatus | 'ALL'
export const DEFAULT_STATUS: StatusFilter = 'IN_REVIEW'
export const STATUS_FILTERS: StatusFilter[] = [...RATING_STATUSES, 'ALL']

/** A `?status=` value from the URL as a filter; anything unknown falls back to the default. */
export function statusFilterOf(raw: string | null): StatusFilter {
  if (raw === 'ALL') return 'ALL'
  return (RATING_STATUSES as string[]).includes(raw ?? '') ? (raw as ObligorRatingStatus) : DEFAULT_STATUS
}

/** The GET /obligor-ratings query for a filter and page. */
export function queueQuery(status: StatusFilter, offset: number): RatingQuery {
  const query: RatingQuery = { limit: PAGE_SIZE, offset }
  if (status !== 'ALL') query.status = status
  return query
}

/**
 * Whether the signed-in user is offered Approve and Reject for a version. The proposer is never
 * offered them (409 `self_approval`); without `rating:approve` they are hidden (403).
 */
export type DecisionGate = 'allowed' | 'not_in_review' | 'self_approval' | 'no_permission'

export function decisionGate(r: Pick<ObligorRating, 'status' | 'proposer_user_id'>, me: Pick<Me, 'user_id'>, has: Has): DecisionGate {
  if (r.status !== 'IN_REVIEW') return 'not_in_review'
  if (isProposer(r, me)) return 'self_approval'
  if (!has('rating:approve')) return 'no_permission'
  return 'allowed'
}

/** Why the comment cannot be sent yet, or null. The server strips surrounding whitespace too. */
export function commentProblem(text: string): string | null {
  const t = text.trim()
  if (!t) return 'A comment is required.'
  if (t.length > COMMENT_MAX) return `At most ${COMMENT_MAX.toLocaleString('en')} characters (${t.length.toLocaleString('en')} now).`
  return null
}

/** The request body, or null while the comment has a problem. */
export function decisionBody(decision: Decision, text: string): RatingDecisionBody | null {
  return commentProblem(text) ? null : { decision, comment: text.trim() }
}

export const fetchQueue = (query: RatingQuery) => (signal: AbortSignal) => call(() => api.GET('/api/v1/obligor-ratings', { params: { query }, signal }))

export const decideRating = (ratingId: string, body: RatingDecisionBody) =>
  call(() => api.POST('/api/v1/obligor-ratings/{rating_id}/approval', { params: { path: { rating_id: ratingId } }, body }))
