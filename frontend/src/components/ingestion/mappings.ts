import { ApiError, type Schemas } from '@/api/client'

/*
 * FE-10.1/10.2: the mapping workbench's pure helpers. Every payload type comes from the
 * generated API schema; this file only holds the local draft shape the grid edits and the
 * builder that turns it into the API's RuleIn.
 */

export type MappingSummary = Schemas['MappingSummary']
export type MappingDetail = Schemas['MappingDetail']
export type MappingVersion = Schemas['MappingVersionOut']
export type MappingVersionStatus = Schemas['MappingVersionStatus']
export type MappingValidation = Schemas['MappingValidationOut']
export type ValidationIssue = Schemas['ValidationIssue']
export type RuleIn = Schemas['RuleIn']
export type RuleOut = Schemas['RuleOut']
export type TransformCode = Schemas['TransformCode']
export type NullPolicy = Schemas['NullPolicy']
export type DateFormat = Schemas['DateFormat']
export type OnMissing = Schemas['OnMissing']
export type IssueSeverity = Schemas['IssueSeverity']
type Transform = NonNullable<RuleIn['transform']>
type DecimalSep = Schemas['ParseDecimalParams']['decimal_sep']
type ThousandsSep = Schemas['ParseDecimalParams']['thousands_sep']

/**
 * `mapping_version` lifecycle (D-06/D-23, the schema_version set) on the design system's
 * status styles. Only ACTIVE is shown as live; APPROVED is reserved (an approval activates
 * in the same transaction) and never shown as success.
 */
export const MAPPING_STATUS_STYLE: Record<MappingVersionStatus, { label: string; className: string }> = {
  DRAFT: { label: 'Draft', className: 'status-draft' },
  IN_REVIEW: { label: 'In review', className: 'status-testing' },
  APPROVED: { label: 'Approved', className: 'status-building' },
  ACTIVE: { label: 'Active', className: 'status-published' },
  DEPRECATED: { label: 'Deprecated', className: 'status-draft' },
  RETIRED: { label: 'Retired', className: 'status-draft' },
  REJECTED: { label: 'Rejected', className: 'status-failed' },
}

export const MAPPING_STATUSES = Object.keys(MAPPING_STATUS_STYLE) as MappingVersionStatus[]

/** The D-19 allowlist with what each transform's parameters are. CONSTANT reads no column. */
export const TRANSFORMS: Record<TransformCode, { label: string; params: 'none' | 'decimal' | 'scale' | 'date' | 'boolean' | 'table' | 'value' }> = {
  COPY: { label: 'Copy', params: 'none' },
  TRIM: { label: 'Trim', params: 'none' },
  UPPER: { label: 'Upper case', params: 'none' },
  LOWER: { label: 'Lower case', params: 'none' },
  PARSE_DECIMAL: { label: 'Parse decimal', params: 'decimal' },
  SCALE: { label: 'Scale (×10ⁿ)', params: 'scale' },
  PARSE_DATE: { label: 'Parse date', params: 'date' },
  PARSE_BOOLEAN: { label: 'Parse boolean', params: 'boolean' },
  MAP_VALUES: { label: 'Map values', params: 'table' },
  DEFAULT: { label: 'Default', params: 'value' },
  CONSTANT: { label: 'Constant', params: 'value' },
}
export const COLUMN_TRANSFORMS = (Object.keys(TRANSFORMS) as TransformCode[]).filter((c) => c !== 'CONSTANT')

export const DATE_FORMATS: DateFormat[] = ['YYYY-MM-DD', 'YYYY/MM/DD', 'DD/MM/YYYY', 'MM/DD/YYYY', 'DD.MM.YYYY', 'DD-MM-YYYY', 'YYYYMMDD']
export const NULL_POLICIES: NullPolicy[] = ['ALLOW_NULL', 'REJECT_NULL', 'USE_DEFAULT']
export const ON_MISSING: OnMissing[] = ['ERROR', 'NULL', 'KEEP']

/** The grid's editable parameters, as text, for every transform (only the chosen one's are sent). */
export interface ParamDraft {
  decimal_sep: DecimalSep
  thousands_sep: ThousandsSep
  power_of_ten: string
  format: DateFormat
  /** Comma-separated. */
  true_values: string
  false_values: string
  /** One `source=target` per line. */
  table: string
  on_missing: OnMissing
  value: string
}

/** UNSET: the column has no rule yet (the dry run reports it; it is never silently dropped). */
export type DraftPolicy = 'MAP' | 'PRESERVE' | 'UNSET'

/** One editable row of the grid: a rule, or a source column without one. */
export interface DraftRule {
  key: string
  /** Null only for a CONSTANT rule. */
  source_column: string | null
  policy: DraftPolicy
  target_field: string
  transform: TransformCode
  params: ParamDraft
  null_policy: NullPolicy
  default_value: string
}

export const emptyParams = (): ParamDraft => ({
  decimal_sep: '.',
  thousands_sep: '',
  power_of_ten: '0',
  format: 'YYYY-MM-DD',
  true_values: '',
  false_values: '',
  table: '',
  on_missing: 'ERROR',
  value: '',
})

let counter = 0
const nextKey = () => `r${++counter}`

export function columnRow(column: string, patch: Partial<DraftRule> = {}): DraftRule {
  return {
    key: nextKey(),
    source_column: column,
    policy: 'UNSET',
    target_field: '',
    transform: 'COPY',
    params: emptyParams(),
    null_policy: 'ALLOW_NULL',
    default_value: '',
    ...patch,
  }
}

export const constantRow = (): DraftRule => columnRow('', { source_column: null, policy: 'MAP', transform: 'CONSTANT' })

const csv = (text: string) =>
  text
    .split(',')
    .map((v) => v.trim())
    .filter((v) => v !== '')

/** The API's params as the grid's text fields. */
export function paramsFromOut(params: Record<string, unknown>): ParamDraft {
  const p = emptyParams()
  const str = (v: unknown) => (typeof v === 'string' ? v : undefined)
  if (str(params.decimal_sep)) p.decimal_sep = params.decimal_sep as DecimalSep
  if (typeof params.thousands_sep === 'string') p.thousands_sep = params.thousands_sep as ThousandsSep
  if (typeof params.power_of_ten === 'number') p.power_of_ten = String(params.power_of_ten)
  if (str(params.format)) p.format = params.format as DateFormat
  if (Array.isArray(params.true_values)) p.true_values = params.true_values.join(', ')
  if (Array.isArray(params.false_values)) p.false_values = params.false_values.join(', ')
  if (params.table && typeof params.table === 'object')
    p.table = Object.entries(params.table as Record<string, string>)
      .map(([k, v]) => `${k}=${v}`)
      .join('\n')
  if (str(params.on_missing)) p.on_missing = params.on_missing as OnMissing
  if (str(params.value) !== undefined) p.value = params.value as string
  return p
}

/** A saved rule as a grid row. */
export function rowFromRule(r: RuleOut): DraftRule {
  return {
    key: nextKey(),
    source_column: r.source_column,
    policy: r.column_policy,
    target_field: r.target_field ?? '',
    transform: r.transform.code,
    params: paramsFromOut(r.transform.params),
    null_policy: r.null_policy,
    default_value: r.default_value ?? '',
  }
}

/**
 * The grid for a version: its rules in order, then one UNSET row for each source column of
 * the selected import that no rule reads, so no column is left out silently.
 */
export function draftFromVersion(rules: RuleOut[], columns: string[]): DraftRule[] {
  const rows = [...rules].sort((a, b) => a.position - b.position).map(rowFromRule)
  const covered = new Set(rules.map((r) => r.source_column).filter((c): c is string => c !== null))
  return [...rows, ...columns.filter((c) => !covered.has(c)).map((c) => columnRow(c))]
}

/** Starting rules for a new mapping: the profile's suggested target (COPY), else PRESERVE. */
export function suggestedRules(columns: { name: string; suggested_target_field: string | null }[], useSuggestions: boolean): RuleIn[] {
  return columns.map((c) =>
    useSuggestions && c.suggested_target_field
      ? { source_column: c.name, target_field: c.suggested_target_field, column_policy: 'MAP', transform: { code: 'COPY' }, null_policy: 'ALLOW_NULL' }
      : { source_column: c.name, column_policy: 'PRESERVE', transform: { code: 'COPY' }, null_policy: 'ALLOW_NULL' },
  )
}

/** Fills the rows that map nothing yet with the profile's suggested target (rows already MAP are kept). */
export function applySuggestions(rows: DraftRule[], suggestions: Record<string, string | null>): DraftRule[] {
  return rows.map((r) => {
    const target = r.source_column ? suggestions[r.source_column] : null
    if (!target || r.policy === 'MAP') return r
    return { ...r, policy: 'MAP', target_field: target, transform: 'COPY' }
  })
}

function transformOf(row: DraftRule, label: string, problems: string[]): Transform | null {
  const p = row.params
  switch (row.transform) {
    case 'COPY':
    case 'TRIM':
    case 'UPPER':
    case 'LOWER':
      return { code: row.transform, params: {} }
    case 'PARSE_DECIMAL':
      if (p.decimal_sep === p.thousands_sep) {
        problems.push(`${label}: the decimal and thousands separators must differ.`)
        return null
      }
      return { code: 'PARSE_DECIMAL', params: { decimal_sep: p.decimal_sep, thousands_sep: p.thousands_sep } }
    case 'SCALE': {
      const n = p.power_of_ten.trim()
      if (!/^-?\d{1,2}$/.test(n) || Math.abs(Number(n)) > 18) {
        problems.push(`${label}: the power of ten must be a whole number from -18 to 18.`)
        return null
      }
      return { code: 'SCALE', params: { power_of_ten: Number(n) } }
    }
    case 'PARSE_DATE':
      return { code: 'PARSE_DATE', params: { format: p.format } }
    case 'PARSE_BOOLEAN': {
      const t = csv(p.true_values)
      const f = csv(p.false_values)
      if (t.length === 0 || f.length === 0) {
        problems.push(`${label}: give at least one true and one false value.`)
        return null
      }
      const lower = new Set(t.map((v) => v.toLowerCase()))
      if (f.some((v) => lower.has(v.toLowerCase()))) {
        problems.push(`${label}: a value cannot be both true and false.`)
        return null
      }
      return { code: 'PARSE_BOOLEAN', params: { true_values: t, false_values: f } }
    }
    case 'MAP_VALUES': {
      const table: Record<string, string> = {}
      for (const line of p.table.split('\n')) {
        if (line.trim() === '') continue
        const at = line.indexOf('=')
        if (at <= 0) {
          problems.push(`${label}: write each value mapping as source=target.`)
          return null
        }
        table[line.slice(0, at)] = line.slice(at + 1)
      }
      if (Object.keys(table).length === 0) {
        problems.push(`${label}: add at least one value mapping.`)
        return null
      }
      return { code: 'MAP_VALUES', params: { table, on_missing: p.on_missing } }
    }
    case 'DEFAULT':
    case 'CONSTANT':
      if (p.value === '') {
        problems.push(`${label}: give the value.`)
        return null
      }
      return { code: row.transform, params: { value: p.value } }
  }
}

/**
 * One grid row as the API's RuleIn, mirroring its shape rules: PRESERVE has no target and
 * COPY; MAP needs a target; only CONSTANT reads no column; a default value goes with
 * USE_DEFAULT only. An UNSET row is no rule (null) and no problem: the dry run reports it.
 */
export function toRuleIn(row: DraftRule): { rule: RuleIn | null; problems: string[] } {
  const problems: string[] = []
  const label = row.source_column ?? 'Constant rule'
  if (row.policy === 'UNSET') return { rule: null, problems }
  if (row.policy === 'PRESERVE') {
    if (!row.source_column) return { rule: null, problems: [`${label}: only a source column can be preserved.`] }
    return { rule: { source_column: row.source_column, column_policy: 'PRESERVE', transform: { code: 'COPY', params: {} }, null_policy: 'ALLOW_NULL' }, problems }
  }
  const target = row.target_field.trim()
  if (!target) problems.push(`${label}: choose a target field.`)
  if (row.transform === 'CONSTANT' && row.source_column) problems.push(`${label}: a constant reads no column.`)
  const transform = transformOf(row, label, problems)
  const useDefault = row.null_policy === 'USE_DEFAULT'
  if (useDefault && row.default_value === '') problems.push(`${label}: give the default value.`)
  if (problems.length > 0 || !transform) return { rule: null, problems }
  return {
    rule: {
      ...(row.source_column ? { source_column: row.source_column } : {}),
      target_field: target,
      column_policy: 'MAP',
      transform,
      null_policy: row.null_policy,
      ...(useDefault ? { default_value: row.default_value } : {}),
    },
    problems,
  }
}

/** Every row's RuleIn, in grid order, and every problem; also flags a target used twice. */
export function buildRules(rows: DraftRule[]): { rules: RuleIn[]; problems: string[] } {
  const rules: RuleIn[] = []
  const problems: string[] = []
  const targets = new Set<string>()
  for (const row of rows) {
    const out = toRuleIn(row)
    problems.push(...out.problems)
    if (!out.rule) continue
    const t = out.rule.target_field
    if (t) {
      if (targets.has(t)) problems.push(`Target field ${t} is mapped more than once.`)
      targets.add(t)
    }
    rules.push(out.rule)
  }
  return { rules, problems }
}

/** Whether the grid differs from the version it was loaded from (keys aside). */
export function isDirty(rows: DraftRule[], baseline: DraftRule[]): boolean {
  const strip = (rs: DraftRule[]) => JSON.stringify(rs.map(({ key: _key, ...rest }) => rest))
  return strip(rows) !== strip(baseline)
}

/** Submit is offered to the author of a DRAFT whose latest dry run was ok; the API checks again. */
export function canSubmitVersion(v: MappingVersion, userId: string): boolean {
  return v.status === 'DRAFT' && v.author_user_id === userId && v.last_validation?.ok === true
}

/** The approver decides only on someone else's version in review (maker-checker). */
export function canDecideVersion(v: MappingVersion, userId: string, mayApprove: boolean): boolean {
  return mayApprove && v.status === 'IN_REVIEW' && v.author_user_id !== userId
}

export const activeVersion = (m: MappingDetail): MappingVersion | undefined => m.versions.find((v) => v.status === 'ACTIVE')

const SEVERITY_RANK: Record<IssueSeverity, number> = { BLOCKER: 0, ERROR: 1, WARNING: 2 }
export const bySeverity = (issues: ValidationIssue[]): ValidationIssue[] =>
  [...issues].sort((a, b) => SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity])

/**
 * Cancels the workbench's in-flight requests when the import or version changes. Every
 * request runs in the current scope; `switch()` aborts it and opens a new one, so an
 * answer from before the switch is dropped even if the server sends it anyway.
 */
export class RequestScope {
  private controller = new AbortController()

  switch(): void {
    this.controller.abort()
    this.controller = new AbortController()
  }

  run<T>(fetcher: (signal: AbortSignal) => Promise<T>, onReady: (value: T) => void, onError: (err: unknown) => void): void {
    const signal = this.controller.signal
    fetcher(signal).then(
      (value) => {
        if (!signal.aborted) onReady(value)
      },
      (err: unknown) => {
        if (!signal.aborted) onError(err)
      },
    )
  }
}

/** Titles for the documented refusals of the mapping endpoints. */
const ERROR_TITLES: Record<string, string> = {
  forbidden: 'You may not do this',
  not_found: 'Not found',
  mapping_validation_required: 'A passing dry run of this version is required first',
  schema_version_not_active: 'The pinned schema version is no longer active',
  invalid_status: 'The version is not in the right status for this',
  contract_mismatch: 'The import belongs to another source contract',
  schema_version_mismatch: 'The import pins another schema version',
  profile_required: 'The import has no profile yet',
  contract_superseded: 'The source contract has been superseded',
  no_active_version: 'The target schema has no active version',
  duplicate_code: 'A mapping with this code exists',
  contract_already_mapped: 'This source contract already has a mapping',
  mapping_change_disabled: 'Mapping changes are switched off',
  ingestion_disabled: 'Ingestion changes are switched off',
  self_approval: 'You cannot approve your own mapping',
  not_author: 'Only the author can submit this version',
  no_change: 'The rules equal the latest version',
  unknown_target_field: 'A target field is not in the schema',
  duplicate_target_field: 'A target field is mapped more than once',
  conflicting_column_policy: 'A column is both mapped and preserved',
  schema_mismatch: "A mapping targets its contract's schema",
  comment_required: 'Give a reason for the rejection',
  validation_error: 'Some values were rejected',
}

export function mappingErrorTitle(err: unknown, fallback: string): string {
  return err instanceof ApiError ? ERROR_TITLES[err.envelope.code] ?? fallback : fallback
}

/** A refused request as shown inline: the backend's envelope, never reworded away. */
export interface Refusal {
  title: string
  status: number | null
  code: string
  message: string
  cid: string | null
}

export function refusalOf(err: unknown, fallback: string): Refusal {
  if (err instanceof ApiError)
    return { title: mappingErrorTitle(err, fallback), status: err.status, code: err.envelope.code, message: err.envelope.message, cid: err.correlationId }
  return { title: fallback, status: null, code: 'unexpected_error', message: 'Something went wrong.', cid: null }
}
