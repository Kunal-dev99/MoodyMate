import type { Schemas } from '@/api/client'
import { isMasked } from '@/components/common/Masked'
import type { InstrumentVersion } from './domain'

/*
 * FE-32.1 (D-61): the instrument detail's rate-terms helpers. Every payload type is the generated
 * one. Term values are the server's exact text (decimals, ISO dates, ISO-8601 periods) and are
 * shown as that text, never parsed or recomputed. Which values sit outside the seeded PROVISIONAL
 * vocabularies is the server's answer (`unknown_fields`), never re-derived here.
 */

export type InstrumentTerms = Schemas['InstrumentTermsOut']
export type CurrencyOut = Schemas['CurrencyOut']
export type FxConsolidationStatus = Schemas['FxConsolidationStatusOut']

/** A term value as the API sends it: text, the masking marker, or absent. */
export type TermValue = InstrumentTerms['rate_type']

/** The notice on the maturity-ladder and LCR views while FX consolidation is blocked (D-61 §6). */
export const FX_BLOCKED_NOTICE =
  'Per-currency results only; FX consolidation is blocked until an approved rate source and conversion policy exist'

/** The pinned schema version is not the rate-terms contract (projector v1/v2): it carries no terms. */
export const TERMS_NOT_IN_CONTRACT = 'No rate terms in this schema version'
/** The rate-terms contract was pinned but the server wrote no term row: the terms were inconsistent. */
export const TERMS_NOT_WRITTEN = 'Terms not recorded: inconsistent in the source row'
/**
 * A version's "Effective from" when no term row was written: `effective_from` lives on the term
 * row, so inconsistent terms leave it null. Shown in place of "Not stated", which would misread
 * as the source row giving no date.
 */
export const EFFECTIVE_FROM_NOT_WRITTEN = 'Not recorded: terms inconsistent'
/** A vocabulary value outside the seeded PROVISIONAL vocabulary, kept as given (UNKNOWN_VALUE). */
export const UNKNOWN_VALUE_TEXT = 'Not in vocabulary: kept as given'

/** The term fields the panel shows, in order (the decimal ones carry the pinned `rate_unit`). */
export const TERM_FIELDS = [
  { key: 'rate_type', label: 'Rate type' },
  { key: 'reference_index', label: 'Reference index' },
  { key: 'spread', label: 'Spread', rate: true },
  { key: 'reset_frequency', label: 'Reset frequency' },
  { key: 'next_reset_date', label: 'Next reset date' },
  { key: 'cap_rate', label: 'Cap rate', rate: true },
  { key: 'floor_rate', label: 'Floor rate', rate: true },
  { key: 'day_count', label: 'Day count' },
  { key: 'payment_frequency', label: 'Payment frequency' },
  { key: 'lifecycle_state', label: 'Lifecycle state' },
] as const satisfies readonly { key: keyof InstrumentTerms; label: string; rate?: boolean }[]

export type TermField = (typeof TERM_FIELDS)[number]['key']

/**
 * The rate type of a recorded term row. `FIXED`/`FLOATING`: the seeded codes; `unknown`: a value
 * the server named in `unknown_fields` (kept as given); `other`: any other text the server did not
 * flag; `masked`: withheld by the caller's clearance; `not-stated`: the source row gave none.
 */
export type RateKind = 'FIXED' | 'FLOATING' | 'unknown' | 'other' | 'masked' | 'not-stated'

/**
 * How one instrument version's rate terms are shown.
 * - `not-in-contract`: the pinned schema version is not the INSTRUMENT_RATE_TERMS contract.
 * - `not-written`: the contract was pinned but no term row exists. The server writes none when the
 *   terms are inconsistent (TERMS_INCONSISTENT: FIXED with an index, FLOATING with no reset, or
 *   floor above cap); the instrument version itself is still recorded.
 * - `recorded`: the term row, its rate kind and the fields the server flagged as outside the
 *   seeded vocabulary.
 */
export type TermsState =
  | { kind: 'not-in-contract' }
  | { kind: 'not-written' }
  | { kind: 'recorded'; terms: InstrumentTerms; rate: RateKind; unknown: readonly string[] }

export function rateKind(terms: Pick<InstrumentTerms, 'rate_type' | 'unknown_fields'>): RateKind {
  const v = terms.rate_type
  if (isMasked(v)) return 'masked'
  if (v === null || v === '') return 'not-stated'
  if (terms.unknown_fields.includes('rate_type')) return 'unknown'
  if (v === 'FIXED' || v === 'FLOATING') return v
  return 'other'
}

export function termsState(v: Pick<InstrumentVersion, 'terms' | 'terms_typed'>): TermsState {
  if (!v.terms_typed) return { kind: 'not-in-contract' }
  if (!v.terms) return { kind: 'not-written' }
  return { kind: 'recorded', terms: v.terms, rate: rateKind(v.terms), unknown: v.terms.unknown_fields }
}

/** The badge text of a rate kind (null: no badge). */
export const RATE_KIND_TEXT: Record<RateKind, string | null> = {
  FIXED: 'Fixed rate',
  FLOATING: 'Floating rate',
  unknown: null,
  other: null,
  masked: null,
  'not-stated': null,
}

/** The short text of a version's terms in the versions table. */
export function termsSummary(state: TermsState): string {
  if (state.kind === 'not-in-contract') return 'Not in contract'
  if (state.kind === 'not-written') return 'Not recorded (inconsistent)'
  if (state.rate === 'FIXED' || state.rate === 'FLOATING') return RATE_KIND_TEXT[state.rate] as string
  if (state.rate === 'masked') return 'Masked'
  if (state.rate === 'not-stated') return 'Rate type not stated'
  return state.rate === 'unknown' ? 'Rate type not in vocabulary' : 'Rate type as given'
}
