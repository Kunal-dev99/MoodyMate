import { ApiError, type Schemas } from '@/api/client'
import { isMasked } from '@/components/common/MaskedValue'

/*
 * FE-12.1: the reconciliation tab's pure helpers. Every payload type comes from the
 * generated API schema. Figures (source, target, held, variance, tolerance) are exact
 * decimal text from the server and are shown as that text: this file and the tab never
 * turn them into numbers (a test greps both for number parsing).
 */

export type ReconciliationRun = Schemas['ReconciliationOut']
export type ReconciliationItem = Schemas['ReconciliationItemOut']
export type ReconciliationOutcome = Schemas['ReconciliationOutcome']
export type ReconciliationItemStatus = Schemas['ReconciliationItemStatus']
export type ReconciliationCheck = Schemas['ReconciliationCheck']
type Masked = Schemas['MaskedValue']

/**
 * D-23 `reconciliation_run.outcome`. Only MATCHED looks finished, and only because the
 * server said so: the UI never derives an outcome from the items (Record: a new backend
 * value is a compile error).
 */
export const OUTCOME_STYLE: Record<ReconciliationOutcome, { label: string; className: string; meaning: string }> = {
  MATCHED: { label: 'Matched', className: 'status-published', meaning: 'Every check matched.' },
  REVIEW_REQUIRED: {
    label: 'Review required',
    className: 'status-testing',
    meaning: 'A check differs, and held (quarantined or rejected) rows explain the gap: review them.',
  },
  BROKEN: {
    label: 'Broken',
    className: 'status-failed',
    meaning: 'A gap is not explained by held rows, or a declared figure differs from the file.',
  },
}
export const OUTCOMES = Object.keys(OUTCOME_STYLE) as ReconciliationOutcome[]

/** D-23 `reconciliation_item.status`, a function of the variance and the tolerance (server-side). */
export const ITEM_STATUS_STYLE: Record<ReconciliationItemStatus, { label: string; className: string }> = {
  MATCHED: { label: 'Matched', className: 'status-published' },
  WITHIN_TOLERANCE: { label: 'Within tolerance', className: 'status-testing' },
  VARIANCE: { label: 'Variance', className: 'status-failed' },
}
export const ITEM_STATUSES = Object.keys(ITEM_STATUS_STYLE) as ReconciliationItemStatus[]

/** What each check compares (BE-12.2, the ReconciliationCheck description). */
export const CHECK_LABEL: Record<ReconciliationCheck, { label: string; compares: string }> = {
  ROW_COUNT: { label: 'Row count', compares: 'Source rows vs accepted rows; held = quarantined + rejected rows.' },
  EXPECTED_ROW_COUNT: { label: 'Expected row count', compares: 'The upload’s declared row count vs the source rows.' },
  CONTROL_TOTAL: { label: 'Control total', compares: 'The column’s source total vs the accepted rows’ total; held = the held rows’ total.' },
  DECLARED_CONTROL_TOTAL: { label: 'Declared control total', compares: 'The upload’s declared total vs the column’s source total.' },
}
export const CHECKS = Object.keys(CHECK_LABEL) as ReconciliationCheck[]

/**
 * How one figure is shown: masked by the server's policy, absent (`held` is null for the
 * declared checks), or the server's exact text, unchanged.
 */
export type FigureDisplay = { kind: 'masked'; value: Masked } | { kind: 'none' } | { kind: 'text'; text: string }

export function figureDisplay(value: string | Masked | null | undefined): FigureDisplay {
  if (isMasked(value)) return { kind: 'masked', value }
  if (value === null || value === undefined) return { kind: 'none' }
  return { kind: 'text', text: value }
}

/** GET /imports/{id}/reconciliation before validation: an empty state, not an error. */
export function isNotReconciled(err: unknown): boolean {
  return err instanceof ApiError && err.status === 404 && err.envelope.code === 'reconciliation_not_found'
}

/** A JSON value from a free-form object (`tolerance_policy`, `details`) as text; never parsed. */
function text(value: unknown): string | null {
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return null
}

/** The PROVISIONAL tolerance policy the run applied, as the server's text. */
export function policyFields(policy: Record<string, unknown>): {
  kind: string | null
  tolerance: string | null
  countTolerance: string | null
  setting: string | null
  provisional: boolean
} {
  return {
    kind: text(policy.kind),
    tolerance: text(policy.tolerance),
    countTolerance: text(policy.count_tolerance),
    setting: text(policy.setting),
    provisional: policy.provisional === true,
  }
}

/** A control total's value counts (parseable, unparseable, empty); none for row counts. */
export function detailCounts(details: Record<string, unknown>): { parseable: string; unparseable: string; empty: string } | null {
  const parseable = text(details.parseable_values)
  const unparseable = text(details.unparseable_values)
  const empty = text(details.empty_values)
  if (parseable === null && unparseable === null && empty === null) return null
  return { parseable: parseable ?? '—', unparseable: unparseable ?? '—', empty: empty ?? '—' }
}
