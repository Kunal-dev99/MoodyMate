import { ApiError, type Schemas } from '@/api/client'

export type Source = Schemas['SourceSummary']
export type SourceDetail = Schemas['SourceDetail']
export type Contract = Schemas['ContractOut']
export type ContractStatus = Schemas['ContractStatus']
export type ContractIn = Schemas['ContractIn']
export type SourceCreate = Schemas['SourceCreate']
export type SourceFormat = Schemas['SourceFormat']
export type SourceKind = Schemas['SourceKind']
export type CsvDelimiter = Schemas['CsvDelimiter']

/**
 * Source contract lifecycle (Data Model §9 `source_contract.status`) on the design
 * system's status styles. The Record type turns a status the backend adds into a compile
 * error here rather than an unstyled pill (D-23).
 */
export const CONTRACT_STATUS_STYLE: Record<ContractStatus, { label: string; className: string }> = {
  PROVISIONAL: { label: 'Provisional', className: 'status-testing' },
  IN_REVIEW: { label: 'In review', className: 'status-testing' },
  APPROVED: { label: 'Approved', className: 'status-published' },
  SUPERSEDED: { label: 'Superseded', className: 'status-draft' },
}

/** D-23 source kinds. Only FILE_DROP is usable in the MVP; the rest show as planned. */
export const SOURCE_KINDS: Record<SourceKind, { label: string; usable: boolean }> = {
  FILE_DROP: { label: 'File drop', usable: true },
  WAREHOUSE: { label: 'Warehouse (planned)', usable: false },
  APPLICATION: { label: 'Application (planned)', usable: false },
  FEED: { label: 'Feed (planned)', usable: false },
}

export const FORMATS: SourceFormat[] = ['CSV', 'XLSX', 'JSON']

/** D-16 CSV delimiters, with a readable label for the tab. */
export const DELIMITERS: Record<CsvDelimiter, string> = {
  ',': 'Comma (,)',
  ';': 'Semicolon (;)',
  '\t': 'Tab',
  '|': 'Pipe (|)',
}

export function delimiterLabel(d: CsvDelimiter | null): string {
  return d == null ? '—' : DELIMITERS[d]
}

/**
 * Classification codes for a profile's default classification. There is no
 * classification endpoint yet: these mirror the provisional seed (migration 0005), the
 * same list the schema proposal editor offers. The server is authoritative and answers
 * 422 `unknown_classification` for anything else.
 */
export const CLASSIFICATIONS = ['public', 'internal', 'confidential', 'restricted']

export interface ColumnDraft {
  name: string
  required: boolean
  businessKey: boolean
  controlTotal: boolean
}

export interface ContractDraft {
  schemaId: string
  columns: ColumnDraft[]
  changeSummary: string
}

export interface ProfileDraft {
  format: SourceFormat
  delimiter: CsvDelimiter
  sheetName: string
  cadence: string
  owner: string
  defaultClassification: string
}

export interface SourceDraft {
  code: string
  name: string
  description: string
  kind: SourceKind
  profile: ProfileDraft
  contract: ContractDraft
}

export const emptyColumn = (): ColumnDraft => ({ name: '', required: true, businessKey: false, controlTotal: false })

export function emptySourceDraft(owner = ''): SourceDraft {
  return {
    code: '',
    name: '',
    description: '',
    kind: 'FILE_DROP',
    profile: { format: 'CSV', delimiter: ',', sheetName: '', cadence: '', owner, defaultClassification: 'internal' },
    contract: { schemaId: '', columns: [emptyColumn()], changeSummary: '' },
  }
}

/** The next version starts from the contract in effect; the user edits a full contract. */
export function draftFromContract(c: Contract): ContractDraft {
  const keys = new Set(c.business_key_columns)
  const totals = new Set(c.control_total_columns)
  return {
    schemaId: c.schema_id,
    columns: c.expected_columns.map((col) => ({
      name: col.name,
      required: col.required,
      businessKey: keys.has(col.name),
      controlTotal: totals.has(col.name),
    })),
    changeSummary: '',
  }
}

/** Source columns built from the target schema's fields (a starting point only). */
export function columnsFromFields(fields: { name: string; required: boolean }[]): ColumnDraft[] {
  return fields.map((f) => ({ name: f.name, required: f.required, businessKey: false, controlTotal: false }))
}

const CODE = /^[a-z][a-z0-9_]{0,99}$/

/** Client-side checks mirroring the API's ContractIn rules, so problems show before the request. */
export function contractProblems(d: ContractDraft): string[] {
  const problems: string[] = []
  if (!d.schemaId) problems.push('Choose the target canonical schema.')
  if (d.columns.length === 0) problems.push('Add at least one expected column.')
  if (d.columns.length > 200) problems.push('At most 200 expected columns.')
  const seen = new Set<string>()
  for (const c of d.columns) {
    const name = c.name.trim()
    if (!name) problems.push('Every expected column needs a name.')
    else if (name.length > 200) problems.push(`${name.slice(0, 20)}…: a column name has at most 200 characters.`)
    else if (seen.has(name)) problems.push(`${name}: duplicate column name.`)
    seen.add(name)
  }
  const keys = d.columns.filter((c) => c.businessKey).length
  if (keys === 0) problems.push('Mark at least one business-key column.')
  if (keys > 20) problems.push('At most 20 business-key columns.')
  if (d.columns.filter((c) => c.controlTotal).length > 20) problems.push('At most 20 control-total columns.')
  return [...new Set(problems)]
}

export function profileProblems(p: ProfileDraft): string[] {
  const problems: string[] = []
  if (!p.owner.trim()) problems.push('Name the profile owner.')
  if (p.format === 'XLSX' && p.sheetName.trim().length > 31) problems.push('An XLSX sheet name has at most 31 characters.')
  return problems
}

export function sourceProblems(d: SourceDraft): string[] {
  const problems: string[] = []
  if (!CODE.test(d.code)) problems.push('Code: use lower_snake_case, starting with a letter.')
  if (!d.name.trim()) problems.push('Give the source a name.')
  if (!SOURCE_KINDS[d.kind].usable) problems.push('Only file-drop sources can be registered in this release.')
  return [...problems, ...profileProblems(d.profile), ...contractProblems(d.contract)]
}

const orNull = (s: string): string | null => (s.trim() === '' ? null : s.trim())

export function toContractIn(d: ContractDraft): ContractIn {
  const cols = d.columns.map((c) => ({ ...c, name: c.name.trim() }))
  return {
    schema_id: d.schemaId,
    expected_columns: cols.map((c) => ({ name: c.name, required: c.required })),
    business_key_columns: cols.filter((c) => c.businessKey).map((c) => c.name),
    control_total_columns: cols.filter((c) => c.controlTotal).map((c) => c.name),
    change_summary: orNull(d.changeSummary),
  }
}

/** The POST /sources body. Delimiter is sent only for CSV and sheet name only for XLSX. */
export function toSourceCreate(d: SourceDraft): SourceCreate {
  const p = d.profile
  return {
    code: d.code,
    name: d.name.trim(),
    description: orNull(d.description),
    kind: d.kind,
    profile: {
      format: p.format,
      encoding: 'UTF-8',
      delimiter: p.format === 'CSV' ? p.delimiter : null,
      sheet_name: p.format === 'XLSX' ? orNull(p.sheetName) : null,
      cadence: orNull(p.cadence),
      owner: p.owner.trim(),
      default_classification: p.defaultClassification,
      provisional: true,
    },
    contract: toContractIn(d.contract),
  }
}

/** Titles for the documented error codes of POST /sources and POST /sources/{id}/contracts. */
const ERROR_TITLES: Record<string, string> = {
  forbidden: 'You may not manage sources',
  ingestion_disabled: 'Ingestion changes are switched off',
  duplicate_code: 'That code is already registered',
  no_active_version: 'The target schema has no active version',
  not_found: 'Not found',
  unsupported_source_kind: 'Only file-drop sources can be registered',
  unknown_classification: 'Unknown classification',
  no_change: 'Nothing changed',
  validation_error: 'Some values were rejected',
}

export function errorTitle(err: unknown, fallback: string): string {
  return err instanceof ApiError ? ERROR_TITLES[err.envelope.code] ?? fallback : fallback
}

/** Contracts newest first, for the versions table. */
export function newestFirst(contracts: Contract[]): Contract[] {
  return [...contracts].sort((a, b) => b.version - a.version)
}
