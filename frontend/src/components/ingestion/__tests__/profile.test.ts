import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import {
  NOT_IN_CONTRACT,
  contractFlag,
  nullRatePercent,
  nullRateText,
  otherCandidates,
  proposalHref,
  suggestedTypeLabel,
  targetSuggestion,
  valueDisplay,
} from '@/components/ingestion/profile'

describe('profile view helpers (FE-9.1)', () => {
  it('labels the inferred type "Suggested", never as the column type', () => {
    expect(suggestedTypeLabel({ suggested_type: 'DECIMAL' })).toBe('Suggested: DECIMAL')
    expect(suggestedTypeLabel({ suggested_type: null })).toBe('No type suggested')
  })

  it('lists the other candidate types after the suggested one', () => {
    expect(otherCandidates({ suggested_type: 'INTEGER', candidate_types: ['INTEGER', 'DECIMAL', 'STRING'] })).toEqual([
      'DECIMAL',
      'STRING',
    ])
    expect(otherCandidates({ suggested_type: null, candidate_types: [] })).toEqual([])
  })

  it.each([
    ['0.0000', 0],
    ['0.0400', 4],
    ['0.5000', 50],
    ['1.0000', 100],
  ])('turns null rate %s into a bar value of %d', (rate, pct) => {
    expect(nullRatePercent(rate)).toBeCloseTo(pct, 10)
  })

  it('draws no bar without rows or for text that is not a number, and clamps to 0–100', () => {
    expect(nullRatePercent(null)).toBeNull()
    expect(nullRatePercent('')).toBeNull()
    expect(nullRatePercent('n/a')).toBeNull()
    expect(nullRatePercent('1.5')).toBe(100)
    expect(nullRatePercent('-0.1')).toBe(0)
    expect(nullRateText('0.0400')).toBe('4.00%')
    expect(nullRateText(null)).toBe('—')
  })

  it('flags a column the contract does not expect as "Not in contract"', () => {
    expect(contractFlag({ in_contract: false })).toBe(NOT_IN_CONTRACT)
    expect(NOT_IN_CONTRACT).toBe('Not in contract')
    expect(contractFlag({ in_contract: true })).toBeNull()
  })

  it('shows the target field only as a suggestion', () => {
    expect(targetSuggestion({ suggested_target_field: 'notional' })).toBe('Suggested: notional')
    expect(targetSuggestion({ suggested_target_field: null })).toBeNull()
  })

  it('renders a masked value with MaskedValue, never as its text', () => {
    const masked = { masked: true as const, classification: 'restricted' }
    expect(valueDisplay(masked)).toEqual({ kind: 'masked', value: masked })
    expect(valueDisplay('EUR')).toEqual({ kind: 'text', text: 'EUR' })
    expect(valueDisplay(null)).toEqual({ kind: 'empty' })
    // '' is a value, not a missing one.
    expect(valueDisplay('')).toEqual({ kind: 'text', text: '""' })
  })

  it('links to the Phase 5 "New proposal" page, with the schema when known', () => {
    expect(proposalHref(null)).toBe('/schemas/proposals/new')
    expect(proposalHref('a1b2')).toBe('/schemas/proposals/new?schema=a1b2')
  })
})

/**
 * Done-when (FE-9.1): no profile code calls a mutating endpoint. The profile code is
 * grepped for openapi-fetch mutating calls, raw fetch with a mutating method and the
 * multipart helper; the only API call allowed is GET.
 */
describe('profile code is read-only (FE-9.1)', () => {
  const root = join(__dirname, '..', '..', '..')
  const files = [
    'components/ingestion/profile.ts',
    'components/ingestion/ImportProfileTab.tsx',
    'app/(platform)/ingestion/imports/[importId]/page.tsx',
  ]
  const MUTATING = [
    /\.\s*(POST|PUT|PATCH|DELETE)\s*\(/,
    /method\s*:\s*['"`](POST|PUT|PATCH|DELETE)['"`]/i,
    /\bfetch\s*\(/,
    /toFormData|callWithResponse/,
  ]

  it.each(files)('%s makes no mutating call', (file) => {
    const source = readFileSync(join(root, file), 'utf8')
    for (const pattern of MUTATING) expect(source, `${file} matches ${pattern}`).not.toMatch(pattern)
  })

  it('the grep would catch a mutating call', () => {
    const sample = "call(() => api.POST('/api/v1/imports', { body }))"
    expect(MUTATING.some((p) => p.test(sample))).toBe(true)
    expect(MUTATING.some((p) => p.test("fetch(url, { method: 'DELETE' })"))).toBe(true)
  })

  it('the profile tab reads only through GET', () => {
    const source = readFileSync(join(root, 'components/ingestion/ImportProfileTab.tsx'), 'utf8')
    const calls = source.match(/api\.[A-Z]+\(/g) ?? []
    expect(calls.length).toBeGreaterThan(0)
    expect(new Set(calls)).toEqual(new Set(['api.GET(']))
  })
})
