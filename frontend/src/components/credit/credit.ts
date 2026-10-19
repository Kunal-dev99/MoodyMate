import type { Schemas } from '@/api/client'
import type { operations } from '@/api/schema'
import { isMasked } from '@/components/common/MaskedValue'

/*
 * FE-19.1: the Counterparties tab's pure helpers. Every payload type comes from the generated
 * API schema. Amounts, rates and dates are exact text from the server and are shown as that
 * text: this file and the credit components never turn them into numbers (a test greps them).
 */

export type CounterpartyPage = Schemas['CounterpartyPage']
export type CounterpartySummary = Schemas['CounterpartySummary']
export type CounterpartyDetail = Schemas['CounterpartyDetail']
export type CounterpartyVersion = Schemas['CounterpartyVersionOut']
export type ExposurePage = Schemas['CreditExposurePage']
export type ExposureSummary = Schemas['CreditExposureSummary']
export type ExposureDetail = Schemas['CreditExposureDetail']
export type ExposureVersion = Schemas['CreditExposureVersionOut']
export type VocabularyEntry = Schemas['CreditVocabularyEntryOut']
export type VocabularyKind = Schemas['CreditVocabularyKind']
export type MaskedValue = Schemas['MaskedValue']

/** Any value a credit read sends: text, an integer (DPD), a flag, the masking marker, or absent. */
export type CreditValue = ExposureVersion['drawn_amount'] | ExposureVersion['days_past_due'] | ExposureVersion['default_flag']

/** The vocabularies (D-37 §5), in the order shown, with a title each (Record: kept equal to the OpenAPI enum). */
const VOCABULARY_TITLES: Record<VocabularyKind, string> = {
  rating_grade: 'Rating grades',
  credit_segment: 'Credit segments',
  facility_type: 'Facility types',
  collateral_type: 'Collateral types',
}
export const VOCABULARY_KINDS = Object.keys(VOCABULARY_TITLES) as VocabularyKind[]
export const vocabularyTitle = (kind: VocabularyKind): string => VOCABULARY_TITLES[kind]

/** The entries of each vocabulary, in the server's order (rank, then code); every kind present, empty ones too. */
export function groupVocabularies(items: readonly VocabularyEntry[]): [VocabularyKind, VocabularyEntry[]][] {
  return VOCABULARY_KINDS.map((kind) => [kind, items.filter((i) => i.vocabulary === kind)])
}

/** The codes of one vocabulary, for a filter's options. */
export const codesOf = (items: readonly VocabularyEntry[], kind: VocabularyKind): VocabularyEntry[] => items.filter((i) => i.vocabulary === kind)

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const COUNTRY = /^[A-Z]{2}$/
const CURRENCY = /^[A-Z]{3}$/

/** The counterparty catalogue filters (GET /counterparties query), empty strings meaning "any". */
export interface CounterpartyFilters {
  segment: string
  country: string
  source_id: string
  import_id: string
}
export const NO_COUNTERPARTY_FILTERS: CounterpartyFilters = { segment: '', country: '', source_id: '', import_id: '' }

/** The generated operations' query types (GET /counterparties, GET /credit-exposures). */
export type CounterpartyQuery = NonNullable<operations['listCounterparties']['parameters']['query']>
export type ExposureQuery = NonNullable<operations['listCreditExposures']['parameters']['query']>

/**
 * The query the server gets: only set, well-formed filters (the API's own patterns: country
 * ISO 3166-1 alpha-2, ids UUIDs), so a half-typed value is not sent as a 422. Returns the
 * problems too, for the form.
 */
export function counterpartyQuery(f: CounterpartyFilters): { query: CounterpartyQuery; problems: (keyof CounterpartyFilters)[] } {
  const query: CounterpartyQuery = {}
  const problems: (keyof CounterpartyFilters)[] = []
  const segment = f.segment.trim()
  if (segment) query.segment = segment
  const country = f.country.trim().toUpperCase()
  if (country) {
    if (COUNTRY.test(country)) query.country = country
    else problems.push('country')
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

/** The credit-exposure catalogue filters (GET /credit-exposures query), empty strings meaning "any". */
export interface ExposureFilters {
  currency: string
  facility_type: string
  counterparty_key: string
  import_id: string
}
export const NO_EXPOSURE_FILTERS: ExposureFilters = { currency: '', facility_type: '', counterparty_key: '', import_id: '' }

export function exposureQuery(f: ExposureFilters): { query: ExposureQuery; problems: (keyof ExposureFilters)[] } {
  const query: ExposureQuery = {}
  const problems: (keyof ExposureFilters)[] = []
  const currency = f.currency.trim().toUpperCase()
  if (currency) {
    if (CURRENCY.test(currency)) query.currency = currency
    else problems.push('currency')
  }
  const facility = f.facility_type.trim()
  if (facility) query.facility_type = facility
  const key = f.counterparty_key.trim()
  if (key) query.counterparty_key = key
  const importId = f.import_id.trim()
  if (importId) {
    if (UUID.test(importId)) query.import_id = importId
    else problems.push('import_id')
  }
  return { query, problems }
}

/** The exposure filters live in the URL under an `exp_` prefix, so both catalogues share one page. */
export const EXPOSURE_PARAM_PREFIX = 'exp_'

const PROBLEM_TEXT: Record<string, string> = {
  country: 'country (two letters, ISO 3166-1)',
  currency: 'currency (three letters, ISO 4217)',
  import_id: 'import ID (a UUID)',
  source_id: 'source',
}
export const problemText = (problems: readonly string[]): string => problems.map((p) => PROBLEM_TEXT[p] ?? p).join(', ')

export const counterpartiesHref = '/domain/counterparties'
export const counterpartyHref = (id: string): string => `${counterpartiesHref}/${encodeURIComponent(id)}`
export const exposureHref = (id: string): string => `${counterpartiesHref}/exposures/${encodeURIComponent(id)}`
export const importHref = (importId: string): string => `/ingestion/imports/${encodeURIComponent(importId)}`

/** What a business key shows as: its text, or a short id when the server masked it. */
export const keyText = (key: string | MaskedValue, id: string): string => (isMasked(key) ? `${id.slice(0, 8)}…` : key)

/**
 * A credit value as display text, never computed: decimal and date text as sent, an integer
 * (days past due) as its digits, a flag as "Yes"/"No". Masked and absent values are the
 * caller's to show (`MaskedValue`, an empty marker); this returns null for them.
 */
export function valueText(value: CreditValue | undefined): string | null {
  if (value === null || value === undefined || isMasked(value)) return null
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  return String(value)
}

/**
 * The exposure versions of a counterparty detail, one row per exposure: the latest version
 * of each (the API lists every version naming the key). The count of versions is kept.
 */
export function latestExposures(versions: readonly ExposureVersion[]): { latest: ExposureVersion; count: number }[] {
  const by = new Map<string, { latest: ExposureVersion; count: number }>()
  for (const v of versions) {
    const seen = by.get(v.credit_exposure_id)
    if (!seen) by.set(v.credit_exposure_id, { latest: v, count: 1 })
    else by.set(v.credit_exposure_id, { latest: v.version > seen.latest.version ? v : seen.latest, count: seen.count + 1 })
  }
  return [...by.values()]
}
