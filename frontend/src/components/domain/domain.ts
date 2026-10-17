import { ApiError, type Schemas } from '@/api/client'
import type { ImportDetail, ImportStatus } from '@/components/ingestion/imports'
import type { Refusal } from '@/components/ingestion/mappings'

/*
 * FE-14.1/14.2: the Financial Domain workspace's pure helpers. Every payload type comes from
 * the generated API schema. Amounts, rates and dates are exact text from the server and are
 * shown as that text: this file and the domain components never turn them into numbers (a
 * test greps them for number parsing).
 */

export type InstrumentPage = Schemas['InstrumentPage']
export type InstrumentSummary = Schemas['InstrumentSummary']
export type InstrumentDetail = Schemas['InstrumentDetail']
export type InstrumentVersion = Schemas['InstrumentVersionOut']
export type InstrumentType = Schemas['InstrumentTypeOut']
export type InstrumentAttribute = Schemas['AttributeOut']
export type CashFlow = Schemas['CashFlowOut']
export type Projection = Schemas['ProjectionOut']
export type ProjectionSummary = Schemas['ProjectionSummary']
export type DomainSchemaCode = Schemas['DomainSchemaCode']
/** A value as the domain API sends it: text, the masking marker, or absent. */
export type DomainValue = InstrumentVersion['notional']

/** The schema codes the projector supports (D-29 §1; Record: kept equal to the OpenAPI enum). */
const SCHEMA_CODES: Record<DomainSchemaCode, { one: string; many: string }> = {
  instrument: { one: 'instrument version', many: 'instrument versions' },
  position: { one: 'position version', many: 'position versions' },
  cashflow_event: { one: 'cash-flow event', many: 'cash-flow events' },
}
export const DOMAIN_SCHEMA_CODES = Object.keys(SCHEMA_CODES) as DomainSchemaCode[]

export const isDomainSchemaCode = (code: string | null | undefined): code is DomainSchemaCode =>
  !!code && (DOMAIN_SCHEMA_CODES as string[]).includes(code)

/** What projected rows are called for a schema code. */
export const rowLabel = (code: DomainSchemaCode): string => SCHEMA_CODES[code].one
export const rowsLabel = (code: DomainSchemaCode): string => SCHEMA_CODES[code].many

/** D-29 §1: the import statuses a projection accepts (accepted rows only for QUARANTINED). */
export const PROJECTABLE_STATUSES: readonly ImportStatus[] = ['COMPLETED', 'COMPLETED_WITH_WARNINGS', 'QUARANTINED']

/**
 * FE-14.2: the "Project to domain" action is offered only for a validated import (it has a
 * manifest and a projectable status) of a supported schema. Cosmetic: the server decides and
 * answers 409 `not_validated` or `projection_unsupported` otherwise (D-04).
 */
export function canProject(job: Pick<ImportDetail, 'status' | 'manifest' | 'schema_code'>): boolean {
  return !!job.manifest && PROJECTABLE_STATUSES.includes(job.status) && isDomainSchemaCode(job.schema_code)
}

/** D-29 §2: one row per accepted canonical record. Shown as a check, never computed for display. */
export function projectionConserves(p: Pick<Projection, 'accepted_count' | 'projected_count'>): boolean {
  return p.projected_count === p.accepted_count
}

/** A per-field count map (TYPE_NOT_PROJECTED, UNKNOWN_VALUE) as sorted rows, zero counts kept. */
export function fieldCounts(counts: Record<string, number> | undefined): [string, number][] {
  return Object.entries(counts ?? {}).sort(([a], [b]) => a.localeCompare(b))
}

/** The projection refusals the server names (BE-14.3), with a title each. */
const PROJECTION_TITLES: Record<string, string> = {
  projection_unsupported: 'This import cannot be projected',
  not_validated: 'The import is not validated',
  domain_projection_disabled: 'Projection is switched off',
  forbidden: 'You may not project imports',
  not_found: 'Import not found',
}

export function projectionRefusal(err: unknown, fallback = 'Could not project the import'): Refusal {
  if (err instanceof ApiError)
    return {
      title: PROJECTION_TITLES[err.envelope.code] ?? fallback,
      status: err.status,
      code: err.envelope.code,
      message: err.envelope.message,
      cid: err.correlationId,
    }
  return { title: fallback, status: null, code: 'unexpected_error', message: 'Something went wrong.', cid: null }
}

/** The catalogue filters (GET /instruments query), empty strings meaning "any". */
export interface CatalogueFilters {
  instrument_type: string
  currency: string
  source_id: string
  import_id: string
}
export const NO_FILTERS: CatalogueFilters = { instrument_type: '', currency: '', source_id: '', import_id: '' }

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const CURRENCY = /^[A-Z]{3}$/

/**
 * The query the server gets: only set, well-formed filters (the API's own patterns), so a
 * half-typed currency or import id is not sent as a 422. Returns the problems too, for the form.
 */
export function catalogueQuery(f: CatalogueFilters): {
  query: { instrument_type?: string; currency?: string; source_id?: string; import_id?: string }
  problems: (keyof CatalogueFilters)[]
} {
  const query: { instrument_type?: string; currency?: string; source_id?: string; import_id?: string } = {}
  const problems: (keyof CatalogueFilters)[] = []
  const type = f.instrument_type.trim()
  if (type) query.instrument_type = type
  const currency = f.currency.trim().toUpperCase()
  if (currency) {
    if (CURRENCY.test(currency)) query.currency = currency
    else problems.push('currency')
  }
  if (f.source_id) {
    if (UUID.test(f.source_id)) query.source_id = f.source_id
    else problems.push('source_id')
  }
  const importId = f.import_id.trim()
  if (importId) {
    if (UUID.test(importId)) query.import_id = importId
    else problems.push('import_id')
  }
  return { query, problems }
}

/** The catalogue link for the instruments one import projected (FE-14.2 → FE-14.1). */
export const catalogueHref = (importId: string): string => `/domain?import_id=${encodeURIComponent(importId)}`
export const instrumentHref = (instrumentId: string): string => `/domain/instruments/${encodeURIComponent(instrumentId)}`
export const importHref = (importId: string): string => `/ingestion/imports/${encodeURIComponent(importId)}`

/**
 * The cash flows of an instrument, once: the API lists the same events (matched by business
 * key) under every version, so they are shown in one table, oldest version's order first.
 */
export function cashFlowsOf(detail: Pick<InstrumentDetail, 'versions'>): CashFlow[] {
  const seen = new Set<string>()
  const out: CashFlow[] = []
  for (const v of detail.versions)
    for (const c of v.cash_flows)
      if (!seen.has(c.cashflow_event_id)) {
        seen.add(c.cashflow_event_id)
        out.push(c)
      }
  return out
}

/** The vocabulary entry for an instrument type as given (exact code match), if seeded. */
export function vocabularyEntry(types: readonly InstrumentType[], code: string | null): InstrumentType | undefined {
  return code === null ? undefined : types.find((t) => t.code === code)
}

/**
 * GET /imports/{id}/projection answers 404 `projection_not_found` until the import is
 * projected: that is "not projected yet", not a failure. Any other 404 (`not_found`: an
 * absent or another tenant's import) stays an error.
 */
export const isNotProjected = (err: unknown): boolean =>
  err instanceof ApiError && err.status === 404 && err.envelope.code === 'projection_not_found'

/** Where a shown projection came from: read on load, written by this click (201), or replayed (200). */
export type ProjectionOrigin = 'existing' | 'created' | 'replayed'

export const originOf = (status: number): ProjectionOrigin => (status === 200 ? 'replayed' : 'created')
