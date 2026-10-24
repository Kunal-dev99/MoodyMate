import { ApiError, toFormData, type DataClass, type MultipartFields, type Schemas } from '@/api/client'
import type { paths } from '@/api/schema'

export type ImportStatus = Schemas['ImportStatus']
export type ImportSummary = Schemas['ImportSummary']
export type ImportDetail = Schemas['ImportDetail']
export type ImportPage = Schemas['ImportPage']
/** The POST /imports multipart body, from the generated types. */
export type CreateImportBody = paths['/api/v1/imports']['post']['requestBody']['content']['multipart/form-data']
export type CreateImportFields = MultipartFields<CreateImportBody>

/**
 * `import_job` lifecycle (Data Model §9 plus QUARANTINED, D-06/D-21) on the design
 * system's status styles. The Record type turns a status the backend adds into a compile
 * error here rather than an unstyled pill (D-23). RECEIVED is "in progress", not success:
 * only COMPLETED is shown as finished.
 */
export const IMPORT_STATUS_STYLE: Record<ImportStatus, { label: string; className: string }> = {
  CREATED: { label: 'Created', className: 'status-draft' },
  RECEIVED: { label: 'Received', className: 'status-building' },
  PROFILING: { label: 'Profiling', className: 'status-building' },
  MAPPING_REQUIRED: { label: 'Mapping required', className: 'status-testing' },
  VALIDATING: { label: 'Validating', className: 'status-building' },
  COMPLETED: { label: 'Completed', className: 'status-published' },
  COMPLETED_WITH_WARNINGS: { label: 'Completed with warnings', className: 'status-testing' },
  QUARANTINED: { label: 'Quarantined', className: 'status-failed' },
  FAILED: { label: 'Failed', className: 'status-failed' },
  CANCELLED: { label: 'Cancelled', className: 'status-draft' },
}

export const IMPORT_STATUSES = Object.keys(IMPORT_STATUS_STYLE) as ImportStatus[]

/*
 * D-16 §1: the upload byte limit is the deployment's IMPORT_MAX_BYTES (at most its hard ceiling),
 * as GET config/limits serves it (FE-35.3, `fetchLimits().upload.max_bytes`); nothing here
 * mirrors the value. The client-side pre-check only saves a pointless upload; the server enforces
 * the limit itself (413).
 */

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KiB`
  return `${(n / (1024 * 1024)).toFixed(2)} MiB`
}

/** The client-side size pre-check against the served `limit` (bytes): null when the file may be uploaded. */
export function sizeProblem(size: number, limit: number): string | null {
  if (size === 0) return 'The file is empty.'
  if (size > limit) return `The file is ${formatBytes(size)}; the limit is ${formatBytes(limit)}.`
  return null
}

/** The `accept` attribute of the file input for a source profile's format. */
export const ACCEPT: Record<Schemas['SourceFormat'], string> = {
  CSV: '.csv,text/csv',
  XLSX: '.xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  JSON: '.json,application/json',
}

export interface ImportDraft {
  sourceId: string
  file: File | null
  attested: boolean
  expectedRowCount: string
  /** Control-total column → decimal text; empty values are not sent. */
  controlTotals: Record<string, string>
  /** FE-11.2: the earlier import this corrected file retries (D-20 §3); empty for a new upload. */
  retryOfImportId?: string
}

export const emptyImportDraft = (sourceId = '', retryOfImportId = ''): ImportDraft => ({
  sourceId,
  file: null,
  attested: false,
  expectedRowCount: '',
  controlTotals: {},
  ...(retryOfImportId ? { retryOfImportId } : {}),
})

/**
 * Whether the attestation is asked for. Fails closed: a missing or unknown `X-Data-Class`
 * is treated as synthetic, so the confirmation is never skipped by accident.
 */
export const attestationRequired = (dataClass: DataClass | null): boolean => dataClass !== 'client'

const DECIMAL = /^-?\d+(\.\d+)?$/
const COUNT = /^\d+$/

/** Client-side checks mirroring the API's rules, so problems show before the upload (`limit`: the served max_bytes). */
export function importProblems(d: ImportDraft, dataClass: DataClass | null, limit: number): string[] {
  const problems: string[] = []
  if (!d.sourceId) problems.push('Choose a source.')
  if (!d.file) problems.push('Choose a file.')
  else {
    const size = sizeProblem(d.file.size, limit)
    if (size) problems.push(size)
  }
  if (attestationRequired(dataClass) && !d.attested) problems.push('Confirm that the file holds synthetic data only.')
  const count = d.expectedRowCount.trim()
  if (count && !COUNT.test(count)) problems.push('Expected row count: a whole number, 0 or more.')
  for (const [column, value] of Object.entries(d.controlTotals)) {
    const v = value.trim()
    if (v && !DECIMAL.test(v)) problems.push(`Control total ${column}: a decimal number such as 1234.50.`)
  }
  return problems
}

/**
 * The POST /imports fields. The attestation is sent as given while the environment is
 * synthetic (or unknown) and false for client data; control totals only when declared.
 */
export function toImportFields(d: ImportDraft, file: File, dataClass: DataClass | null): CreateImportFields {
  const totals = Object.fromEntries(
    Object.entries(d.controlTotals)
      .map(([k, v]) => [k, v.trim()] as const)
      .filter(([, v]) => v !== ''),
  )
  const count = d.expectedRowCount.trim()
  return {
    source_id: d.sourceId,
    file,
    synthetic_attested: attestationRequired(dataClass) ? d.attested : false,
    ...(count ? { expected_row_count: Number(count) } : {}),
    ...(Object.keys(totals).length > 0 ? { control_totals: JSON.stringify(totals) } : {}),
    ...(d.retryOfImportId ? { retry_of_import_id: d.retryOfImportId } : {}),
  }
}

/** The multipart body the wizard sends (exposed for tests). */
export function buildImportFormData(d: ImportDraft, file: File, dataClass: DataClass | null): FormData {
  return toFormData(toImportFields(d, file, dataClass) as Record<string, unknown>)
}

/** A fresh `Idempotency-Key` for one upload attempt: the shared one (FE-29.1). */
export { newIdempotencyKey } from '@/lib/idempotency'

/** D-21: an idempotent replay answers 200 with the original import; a new import 201. */
export const isReplay = (response: Response): boolean => response.status === 200

/** Titles for the documented error codes of POST /imports. Details hold positions/limits only. */
const ERROR_TITLES: Record<string, string> = {
  forbidden: 'You may not upload imports',
  ingestion_disabled: 'Ingestion changes are switched off',
  payload_too_large: 'The file is too large',
  synthetic_attestation_required: 'Confirm the file holds synthetic data',
  idempotency_conflict: 'This upload attempt was already used for another file',
  not_found: 'Source not found',
  unknown_control_total_column: 'Unknown control-total column',
  no_current_contract: 'The source has no current contract',
  no_active_version: 'The target schema has no active version',
  too_many_rows: 'The file has too many rows',
  too_many_columns: 'The file has too many columns',
  cell_too_long: 'A cell is too long',
  content_too_large: 'The file holds too much content once decoded',
  xlsx_uncompressed_too_large: 'The workbook is too large when unpacked',
  xlsx_compression_ratio_too_high: 'The workbook is compressed too much',
  unsupported_file_type: 'Unsupported file type',
  format_mismatch: "The file does not match the source's format",
  legacy_xls_not_supported: 'Legacy .xls workbooks are not accepted',
  macro_enabled_workbook: 'Macro-enabled workbooks are not accepted',
  encoding_not_utf8: 'The file is not UTF-8',
  invalid_character: 'The file contains an invalid character',
  missing_header: 'The file has no header row',
  invalid_header: 'The header row is invalid',
  csv_malformed: 'The CSV file is malformed',
  json_malformed: 'The JSON file is malformed',
  json_invalid_shape: 'The JSON file has the wrong shape',
  json_nested_value: 'The JSON file has nested values',
  sheet_not_found: 'The sheet was not found',
  xlsx_invalid: 'The workbook could not be read',
  artifact_integrity_error: 'The stored file failed its integrity check',
  retry_source_mismatch: 'A retry must be for the same source as the import it corrects',
  retry_not_applicable: 'Only a quarantined or failed import can be retried',
  validation_error: 'Some values were rejected',
}

export function importErrorTitle(err: unknown, fallback = 'Could not upload the file'): string {
  return err instanceof ApiError ? ERROR_TITLES[err.envelope.code] ?? fallback : fallback
}

/** Codes that ERROR_TITLES covers (for tests). */
export const IMPORT_ERROR_CODES = Object.keys(ERROR_TITLES)
