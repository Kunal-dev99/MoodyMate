import { ApiError, type Schemas } from '@/api/client'
import type { ImportStatus } from './imports'
import type { Refusal } from './mappings'

/*
 * FE-11.1/11.2: the validation dashboard's and the quarantine view's pure helpers. Every
 * payload type comes from the generated API schema.
 */

export type ImportManifest = Schemas['ImportManifest']
export type ManifestQuality = Schemas['ManifestQuality']
export type ImportIssue = Schemas['IssueOut']
export type IssuePage = Schemas['IssuePage']
export type ValidationSeverity = Schemas['ValidationSeverity']
export type ValidationCode = Schemas['ValidationCode']
export type IssueDisposition = Schemas['IssueDisposition']
export type QuarantineBatch = Schemas['QuarantineBatch']
export type QuarantineItem = Schemas['QuarantineItemOut']
export type QuarantineStatus = Schemas['QuarantineStatus']

/** An import the server may still validate (BE-11.3: RECEIVED or MAPPING_REQUIRED, no manifest). */
export const canValidate = (status: ImportStatus, manifest: ImportManifest | null | undefined): boolean =>
  !manifest && (status === 'RECEIVED' || status === 'MAPPING_REQUIRED')

/** D-20 §3: a corrected file may be uploaded for a QUARANTINED or FAILED import. */
export const canRetry = (status: ImportStatus): boolean => status === 'QUARANTINED' || status === 'FAILED'

/** The import wizard, pre-filled with the source and `retry_of_import_id`. */
export const retryHref = (job: { id: string; source_id: string }): string =>
  `/ingestion/imports/new?source=${encodeURIComponent(job.source_id)}&retry_of=${encodeURIComponent(job.id)}`

/**
 * D-20 §1: every source row has exactly one outcome, so accepted + quarantined + rejected
 * equals the row count. Warnings count issues, not rows, and are not part of the sum.
 */
export function outcomeTotal(q: ManifestQuality): { sum: number; rowCount: number; addsUp: boolean } {
  const sum = q.accepted_records + q.quarantined_records + q.rejected_records
  return { sum, rowCount: q.row_count, addsUp: sum === q.row_count }
}

/** Issue severity on the design system's status styles (Record: a new backend value is a compile error). */
export const SEVERITY_STYLE: Record<ValidationSeverity, { label: string; className: string }> = {
  INFO: { label: 'Info', className: 'status-draft' },
  WARNING: { label: 'Warning', className: 'status-testing' },
  ERROR: { label: 'Error', className: 'status-failed' },
  BLOCKER: { label: 'Blocker', className: 'status-failed' },
}
export const SEVERITIES = Object.keys(SEVERITY_STYLE) as ValidationSeverity[]

/** Every issue code, for the filter (Record: kept equal to the OpenAPI enum by the compiler and a test). */
const CODE_SET: Record<ValidationCode, true> = {
  REQUIRED_FIELD_UNMAPPED: true,
  REQUIRED_COLUMN_MISSING: true,
  BUSINESS_KEY_COLUMN_MISSING: true,
  MAPPING_RULE_INVALID: true,
  SOURCE_COLUMN_MISSING: true,
  UNMAPPED_COLUMN: true,
  BLANK_ROW: true,
  EXTRA_CELLS: true,
  MISSING_CELLS: true,
  CELL_ERROR: true,
  FORMULA_WITHOUT_CACHED_VALUE: true,
  FORMULA_CACHED_VALUE: true,
  PRECISION_BEYOND_15_DIGITS: true,
  REQUIRED_VALUE_MISSING: true,
  INVALID_TYPE: true,
  INVALID_DATE: true,
  SCALE_EXCEEDED: true,
  PRECISION_EXCEEDED: true,
  INVALID_CURRENCY_CODE: true,
  UNMAPPED_VALUE: true,
  MISSING_BUSINESS_KEY: true,
  DUPLICATE_BUSINESS_KEY: true,
}
export const ISSUE_CODES = Object.keys(CODE_SET) as ValidationCode[]

/**
 * `quarantine_item` lifecycle (Data Model §9, D-20 §2). RESOLVED is the only finished state;
 * REJECTED is final but not success.
 */
export const QUARANTINE_STATUS_STYLE: Record<QuarantineStatus, { label: string; className: string }> = {
  OPEN: { label: 'Open', className: 'status-failed' },
  UNDER_REVIEW: { label: 'Under review', className: 'status-testing' },
  RETRY_READY: { label: 'Retry ready', className: 'status-building' },
  CORRECTED: { label: 'Corrected', className: 'status-building' },
  REJECTED: { label: 'Rejected', className: 'status-draft' },
  RESOLVED: { label: 'Resolved', className: 'status-published' },
}
export const QUARANTINE_STATUSES = Object.keys(QUARANTINE_STATUS_STYLE) as QuarantineStatus[]

/**
 * D-20 §2, the moves a reviewer makes by hand (the backend's MANUAL_QUARANTINE_MOVES).
 * RETRY_READY → CORRECTED → RESOLVED | RETRY_READY happen only when a retry import is
 * validated, so they are never offered as buttons.
 */
export const MANUAL_MOVES: Record<QuarantineStatus, readonly QuarantineStatus[]> = {
  OPEN: ['UNDER_REVIEW'],
  UNDER_REVIEW: ['RETRY_READY', 'REJECTED', 'OPEN'],
  RETRY_READY: [],
  CORRECTED: [],
  REJECTED: [],
  RESOLVED: [],
}

/**
 * The disposition buttons for an item: the moves both the D-20 table and the server's
 * `allowed_dispositions` permit (fail closed: if they disagree, the move is not offered,
 * and the server decides anyway).
 */
export function legalMoves(item: Pick<QuarantineItem, 'status' | 'allowed_dispositions'>): QuarantineStatus[] {
  const allowed = new Set(item.allowed_dispositions)
  return MANUAL_MOVES[item.status].filter((to) => allowed.has(to))
}

export const MOVE_LABEL: Record<QuarantineStatus, string> = {
  OPEN: 'Reopen',
  UNDER_REVIEW: 'Start review',
  RETRY_READY: 'Mark retry ready',
  REJECTED: 'Reject row',
  CORRECTED: 'Corrected',
  RESOLVED: 'Resolved',
}

/** Titles for the documented refusals of the validate, issues and quarantine endpoints. */
const ERROR_TITLES: Record<string, string> = {
  forbidden: 'You may not do this',
  not_found: 'Not found',
  mapping_required: 'An active mapping is required first',
  already_validated: 'This import was validated already',
  invalid_status: 'The import is not in a status that can be validated',
  validation_profile_missing: 'No validation profile exists',
  ingestion_disabled: 'Ingestion changes are switched off',
  invalid_transition: 'This move is not allowed',
  validation_error: 'Some values were rejected',
}

export function validationRefusal(err: unknown, fallback: string): Refusal {
  if (err instanceof ApiError)
    return {
      title: ERROR_TITLES[err.envelope.code] ?? fallback,
      status: err.status,
      code: err.envelope.code,
      message: err.envelope.message,
      cid: err.correlationId,
    }
  return { title: fallback, status: null, code: 'unexpected_error', message: 'Something went wrong.', cid: null }
}
