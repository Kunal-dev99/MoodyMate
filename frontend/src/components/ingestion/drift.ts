import type { Schemas } from '@/api/client'
import { commentProblem as sharedCommentProblem } from '@/lib/forms'
import { refusalOf, type Refusal } from '@/lib/refusal'
import { proposalHref } from './profile'

/*
 * FE-12.2: the drift tab's and the review dialog's pure helpers. Every payload type comes
 * from the generated API schema. Statistics (null rates, thresholds, min and max) are the
 * server's text and are shown as that text, never parsed.
 */

export type DriftEvent = Schemas['DriftEventOut']
export type DriftPage = Schemas['DriftPage']
export type DriftSummary = Schemas['DriftSummary']
export type DriftType = Schemas['DriftType']
export type DriftStatus = Schemas['DriftStatus']
export type DriftReview = Schemas['DriftReviewIn']
export type DriftReviewOutcome = DriftReview['outcome']
export type ProposalLink = Schemas['ProposalLink']

/** D-23 `data_drift_event.drift_type` (Record: a new backend value is a compile error). */
export const DRIFT_TYPE_STYLE: Record<DriftType, { label: string; className: string; meaning: string }> = {
  COLUMN_ADDED: { label: 'Column added', className: 'status-building', meaning: 'This import has a column the baseline did not.' },
  COLUMN_REMOVED: { label: 'Column removed', className: 'status-failed', meaning: 'The baseline had a column this import does not.' },
  TYPE_CHANGED: { label: 'Type changed', className: 'status-failed', meaning: 'The column’s suggested (most specific) type differs from the baseline’s.' },
  NULL_RATE_SHIFT: { label: 'Null rate shift', className: 'status-testing', meaning: 'The column’s null rate moved by more than the null-rate threshold.' },
  RANGE_SHIFT: {
    label: 'Range shift',
    className: 'status-testing',
    meaning: 'A numeric column’s min or max lies beyond the baseline’s by more than the range threshold times the baseline’s range.',
  },
}
export const DRIFT_TYPES = Object.keys(DRIFT_TYPE_STYLE) as DriftType[]

/**
 * D-23 `data_drift_event.status`. OPEN waits for a person; both reviewed states are final
 * (a second review is 409 already_reviewed).
 */
export const DRIFT_STATUS_STYLE: Record<DriftStatus, { label: string; className: string }> = {
  OPEN: { label: 'Open', className: 'status-testing' },
  ACKNOWLEDGED: { label: 'Acknowledged', className: 'status-published' },
  ACTION_REQUIRED: { label: 'Action required', className: 'status-failed' },
}
export const DRIFT_STATUSES = Object.keys(DRIFT_STATUS_STYLE) as DriftStatus[]

/** The outcomes a reviewer chooses from (DriftReviewIn.outcome). */
export const REVIEW_OUTCOME: Record<DriftReviewOutcome, { label: string; hint: string }> = {
  ACKNOWLEDGED: { label: 'Acknowledged', hint: 'The change is expected; nothing else is needed.' },
  ACTION_REQUIRED: {
    label: 'Action required',
    hint: 'A schema change should be proposed. This links to the proposal page and creates nothing.',
  },
}
export const REVIEW_OUTCOMES = Object.keys(REVIEW_OUTCOME) as DriftReviewOutcome[]

/**
 * The outcomes offered for an event: OPEN only, and only those the server's
 * `allowed_outcomes` also lists (fail closed: the server decides anyway).
 */
export function reviewOutcomes(event: Pick<DriftEvent, 'status' | 'allowed_outcomes'>): DriftReviewOutcome[] {
  if (event.status !== 'OPEN') return []
  const allowed = new Set<string>(event.allowed_outcomes)
  return REVIEW_OUTCOMES.filter((o) => allowed.has(o))
}

export const canReview = (event: Pick<DriftEvent, 'status' | 'allowed_outcomes'>): boolean => reviewOutcomes(event).length > 0

/** DriftReviewIn.comment: required, 1 to 1000 characters after trimming (the server trims too). */
export { COMMENT_MAX } from '@/lib/forms'

/**
 * The shared check (FE-29.1), bound to the drift dialog's short wording ("At most 1000
 * characters."), which drift.test.ts asserts unchanged.
 */
export const commentProblem = (comment: string): string | null => sharedCommentProblem(comment, { style: 'short' })

/** The request body, or null while the form is incomplete. */
export function reviewBody(outcome: DriftReviewOutcome | null, comment: string): DriftReview | null {
  if (!outcome || commentProblem(comment)) return null
  return { outcome, comment: comment.trim() }
}

/**
 * The Phase 5 "New proposal" page with the schema prefilled (schema only). The route is
 * built here from the hint's `schema_id`, so a link is only ever to that page; it equals
 * the server's `href` (`/schemas/proposals/new?schema={id}`), which a test checks.
 */
export function driftProposalHref(link: Pick<ProposalLink, 'schema_id'>): string {
  return proposalHref(link.schema_id)
}

/** A free-form value (`thresholds`) as text; never parsed. */
function text(value: unknown): string | null {
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return null
}

/** The PROVISIONAL thresholds the detector applied, as the server's text. */
export function thresholdFields(thresholds: Record<string, unknown>): { nullRate: string | null; range: string | null; provisional: boolean } {
  return { nullRate: text(thresholds.null_rate_threshold), range: text(thresholds.range_threshold), provisional: thresholds.provisional === true }
}

/** Titles for the documented refusals of POST /drift-events/{id}/review. */
const ERROR_TITLES: Record<string, string> = {
  forbidden: 'You may not review drift',
  not_found: 'Drift event not found',
  already_reviewed: 'This drift event was reviewed already',
  ingestion_disabled: 'Ingestion changes are switched off',
  validation_error: 'Some values were rejected',
}

export const driftRefusal = (err: unknown, fallback: string): Refusal => refusalOf(err, fallback, ERROR_TITLES)
