/**
 * FE-29.2: the one maker-checker gate (D-06, D-32 §3, D-39 §3, D-44 §5). Approve and Reject are
 * offered only on an item in review, never to its author (the server answers 409
 * `self_approval`), only while changes are switched on, and only with the approve permission
 * (403). Cosmetic: the server checks every one of these again and decides (D-04).
 */

export type MakerCheckerGate = 'allowed' | 'not_in_review' | 'self_approval' | 'change_disabled' | 'no_permission'

/** The signed-in user authored the item. A seeded item has no author user, so nobody is its author. */
export const isAuthorOf = (authorUserId: string | null | undefined, me: { user_id: string }): boolean =>
  authorUserId !== null && authorUserId !== undefined && authorUserId === me.user_id

export interface MakerCheckerInput {
  /** The item is in the state a decision applies to (IN_REVIEW, or SUBMITTED for schema proposals). */
  inReview: boolean
  /** Who proposed or authored it. */
  authorUserId: string | null | undefined
  me: { user_id: string }
  /** The caller holds the area's approve permission. */
  mayApprove: boolean
  /** The area's change switch (default on). */
  enabled?: boolean
}

/** With no change switch, the answer is never `change_disabled`. */
export function makerCheckerGate(input: Omit<MakerCheckerInput, 'enabled'>): Exclude<MakerCheckerGate, 'change_disabled'>
export function makerCheckerGate(input: MakerCheckerInput): MakerCheckerGate
export function makerCheckerGate({ inReview, authorUserId, me, mayApprove, enabled = true }: MakerCheckerInput): MakerCheckerGate {
  if (!inReview) return 'not_in_review'
  if (isAuthorOf(authorUserId, me)) return 'self_approval'
  if (!enabled) return 'change_disabled'
  if (!mayApprove) return 'no_permission'
  return 'allowed'
}

export const mayDecide = (input: MakerCheckerInput): boolean => makerCheckerGate(input) === 'allowed'
