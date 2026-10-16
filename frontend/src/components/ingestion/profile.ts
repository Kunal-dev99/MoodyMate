import type { Schemas } from '@/api/client'
import { isMasked } from '@/components/common/MaskedValue'

/** GET /imports/{id}/profile (BE-9.3), from the generated types. */
export type ImportProfile = Schemas['ImportProfile']
export type ColumnProfile = Schemas['ColumnProfile']
export type TopValue = Schemas['TopValue']
type Masked = Schemas['MaskedValue']

/**
 * The inferred type is a suggestion (DI-06, `suggestion: true`): it is always shown with
 * the word "Suggested", never as the column's type. No candidate type, no suggestion.
 */
export function suggestedTypeLabel(column: Pick<ColumnProfile, 'suggested_type'>): string {
  return column.suggested_type ? `Suggested: ${column.suggested_type}` : 'No type suggested'
}

/** The other candidate types (all fit every non-empty value), after the suggested one. */
export function otherCandidates(column: Pick<ColumnProfile, 'candidate_types' | 'suggested_type'>): string[] {
  return column.candidate_types.filter((t) => t !== column.suggested_type)
}

/**
 * The null-rate bar's value, 0–100, from the API's decimal text (4 places). Null when the
 * import has no rows (the API sends null) or the text is not a number: no bar is drawn
 * rather than a misleading empty one.
 */
export function nullRatePercent(rate: string | null): number | null {
  if (rate === null || rate.trim() === '') return null
  const n = Number(rate)
  if (!Number.isFinite(n)) return null
  return Math.min(100, Math.max(0, n * 100))
}

/** The null rate as text: "4.00%", or "—" without rows. */
export function nullRateText(rate: string | null): string {
  const pct = nullRatePercent(rate)
  return pct === null ? '—' : `${pct.toFixed(2)}%`
}

export const NOT_IN_CONTRACT = 'Not in contract'

/** A column the pinned source contract does not expect is flagged; others are not. */
export function contractFlag(column: Pick<ColumnProfile, 'in_contract'>): string | null {
  return column.in_contract ? null : NOT_IN_CONTRACT
}

/** The suggested target field as a suggestion, never as a mapping (mappings are Phase 10). */
export function targetSuggestion(column: Pick<ColumnProfile, 'suggested_target_field'>): string | null {
  return column.suggested_target_field ? `Suggested: ${column.suggested_target_field}` : null
}

/**
 * How a profiled value is shown. A masked value (`{masked: true, classification}`) is
 * rendered with MaskedValue, never as text; a missing one as "—"; '' is shown quoted so it
 * is not mistaken for a missing value.
 */
export type ValueDisplay = { kind: 'masked'; value: Masked } | { kind: 'empty' } | { kind: 'text'; text: string }

export function valueDisplay(value: string | Masked | null | undefined): ValueDisplay {
  if (isMasked(value)) return { kind: 'masked', value }
  if (value === null || value === undefined) return { kind: 'empty' }
  return { kind: 'text', text: value === '' ? '""' : value }
}

/** The Phase 5 "New proposal" page, with the import's schema chosen when it is known. */
export function proposalHref(schemaId: string | null): string {
  return schemaId ? `/schemas/proposals/new?schema=${encodeURIComponent(schemaId)}` : '/schemas/proposals/new'
}
