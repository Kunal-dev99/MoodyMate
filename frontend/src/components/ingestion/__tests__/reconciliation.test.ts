import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { ApiError } from '@/api/client'
import { ReconciliationChecks } from '@/components/ingestion/ImportReconciliationTab'
import { ReconciliationItemStatusPill, ReconciliationOutcomePill } from '@/components/ingestion/ReconciliationPills'
import {
  CHECKS,
  CHECK_LABEL,
  ITEM_STATUSES,
  ITEM_STATUS_STYLE,
  OUTCOMES,
  OUTCOME_STYLE,
  detailCounts,
  figureDisplay,
  isNotReconciled,
  policyFields,
  type ReconciliationItem,
} from '@/components/ingestion/reconciliation'

// The OpenAPI document the types are generated from: pills are tested against its enums (D-23).
const openapi = JSON.parse(readFileSync(fileURLToPath(new URL('../../../../../backend/openapi.json', import.meta.url)), 'utf8'))
const enumOf = (name: string): string[] => [...openapi.components.schemas[name].enum].sort()

const envelope = (code: string) => ({ code, message: code, details: null, correlation_id: 'cid-1', retryable: false })

describe('reconciliation pills (FE-12.1, D-23)', () => {
  it('styles exactly the API outcomes; only MATCHED looks finished, BROKEN looks failed', () => {
    expect([...OUTCOMES].sort()).toEqual(enumOf('ReconciliationOutcome'))
    for (const o of OUTCOMES) {
      const html = renderToStaticMarkup(createElement(ReconciliationOutcomePill, { outcome: o }))
      expect(html).toContain(`data-outcome="${o}"`)
      expect(html).toContain(OUTCOME_STYLE[o].label)
      expect(html).toContain(OUTCOME_STYLE[o].className)
    }
    expect(OUTCOMES.filter((o) => OUTCOME_STYLE[o].className === 'status-published')).toEqual(['MATCHED'])
    expect(OUTCOME_STYLE.BROKEN.className).toBe('status-failed')
  })

  it('styles exactly the API item statuses; only MATCHED looks finished', () => {
    expect([...ITEM_STATUSES].sort()).toEqual(enumOf('ReconciliationItemStatus'))
    for (const s of ITEM_STATUSES) {
      const html = renderToStaticMarkup(createElement(ReconciliationItemStatusPill, { status: s }))
      expect(html).toContain(`data-status="${s}"`)
      expect(html).toContain(ITEM_STATUS_STYLE[s].label)
    }
    expect(ITEM_STATUSES.filter((s) => ITEM_STATUS_STYLE[s].className === 'status-published')).toEqual(['MATCHED'])
  })

  it('labels exactly the API checks', () => {
    expect([...CHECKS].sort()).toEqual(enumOf('ReconciliationCheck'))
    for (const c of CHECKS) expect(CHECK_LABEL[c].label.length).toBeGreaterThan(0)
  })
})

describe('reconciliation figures are the API strings (FE-12.1)', () => {
  it('shows text unchanged, masked as masked, and a missing held figure as none', () => {
    for (const text of ['4237607.14001', '-1', '0.00', '5', '0', '12345678901234567890.123456789012345678'])
      expect(figureDisplay(text)).toEqual({ kind: 'text', text })
    const masked = { masked: true, classification: 'confidential' } as const
    expect(figureDisplay(masked)).toEqual({ kind: 'masked', value: masked })
    expect(figureDisplay(null)).toEqual({ kind: 'none' })
  })

  it('renders each figure as the exact API text, including precision a float would lose', () => {
    // 2^53 + 1 and 38 significant digits: parseFloat would print 9007199254740992 and round the rest.
    const item = (over: Partial<ReconciliationItem>): ReconciliationItem => ({
      sequence: 1,
      check: 'CONTROL_TOTAL',
      column: 'notional',
      unit: 'currency',
      classification: 'confidential',
      source: '9007199254740993',
      target: '0.1',
      held: '12345678901234567890.123456789012345678',
      variance: '9007199254740992.9',
      tolerance: '0',
      status: 'VARIANCE',
      details: { parseable_values: 24, unparseable_values: 0, empty_values: 1 },
      ...over,
    })
    const masked = { masked: true, classification: 'confidential' } as const
    const html = renderToStaticMarkup(
      createElement(ReconciliationChecks, {
        items: [
          item({}),
          item({ sequence: 2, check: 'ROW_COUNT', column: null, unit: 'rows', classification: 'public', source: '25', target: '20', held: '5', variance: '5', details: {} }),
          item({ sequence: 3, source: masked, target: masked, held: masked, variance: masked }),
          item({ sequence: 4, check: 'EXPECTED_ROW_COUNT', column: null, source: '24', target: '25', held: null, variance: '-1' }),
        ],
      }),
    )
    for (const text of ['9007199254740993', '12345678901234567890.123456789012345678', '9007199254740992.9', '-1'])
      expect(html).toContain(`>${text}</span>`)
    expect(html).not.toContain('9007199254740992<') // the float rounding of 2^53 + 1
    expect(html.match(/data-testid="masked-value"/g)?.length).toBe(4)
    expect(html).toContain('data-check="ROW_COUNT"')
    expect(html).toMatch(/>24<\/span> parseable/)
  })

  it('treats 404 reconciliation_not_found as "not reconciled yet", nothing else', () => {
    expect(isNotReconciled(new ApiError(404, envelope('reconciliation_not_found')))).toBe(true)
    expect(isNotReconciled(new ApiError(404, envelope('not_found')))).toBe(false)
    expect(isNotReconciled(new ApiError(403, envelope('forbidden')))).toBe(false)
    expect(isNotReconciled(new Error('reconciliation_not_found'))).toBe(false)
  })

  it('reads the tolerance policy and value counts as text', () => {
    const policy = { kind: 'ABSOLUTE', tolerance: '0', count_tolerance: '0', provisional: true, setting: 'RECONCILIATION_TOLERANCE' }
    expect(policyFields(policy)).toEqual({ kind: 'ABSOLUTE', tolerance: '0', countTolerance: '0', setting: 'RECONCILIATION_TOLERANCE', provisional: true })
    expect(policyFields({}).provisional).toBe(false)
    expect(detailCounts({ parseable_values: 24, unparseable_values: 0, empty_values: 1 })).toEqual({ parseable: '24', unparseable: '0', empty: '1' })
    expect(detailCounts({})).toBeNull()
  })
})

/**
 * Done-when (FE-12.1): a grep finds no parseFloat / Number( on reconciliation values. The
 * reconciliation and drift code (the files that hold decimal text from the API) is grepped
 * for every way JavaScript turns text into a float.
 */
describe('no number parsing on reconciliation or drift values (FE-12.1)', () => {
  const root = join(__dirname, '..', '..', '..')
  const files = [
    'components/ingestion/reconciliation.ts',
    'components/ingestion/ImportReconciliationTab.tsx',
    'components/ingestion/ReconciliationPills.tsx',
    'components/ingestion/drift.ts',
    'components/ingestion/ImportDriftTab.tsx',
    'components/ingestion/DriftReviewDialog.tsx',
    'components/ingestion/DriftPills.tsx',
  ]
  const PARSING = [/parseFloat/, /parseInt/, /\bNumber\s*\(/, /\bNumber\./, /\bMath\./, /\.toFixed\s*\(/, /NumberFormat/, /\bBigInt\s*\(/, /(^|[=(,:?!&|{]|return)\s*\+\s*[A-Za-z_$(]/m]

  it.each(files)('%s never parses a value to a number', (file) => {
    const source = readFileSync(join(root, file), 'utf8')
    for (const pattern of PARSING) expect(source, `${file} matches ${pattern}`).not.toMatch(pattern)
  })

  it('the grep would catch a parse', () => {
    for (const sample of ['parseFloat(item.variance)', 'Number(i.source)', 'const v = +item.held', 'Math.abs(x)', 'x.toFixed(2)', '{+i.variance}'])
      expect(PARSING.some((p) => p.test(sample)), sample).toBe(true)
  })

  it('the reconciliation tab reads only through GET', () => {
    const source = readFileSync(join(root, 'components/ingestion/ImportReconciliationTab.tsx'), 'utf8')
    const calls = source.match(/api\.[A-Z]+\(/g) ?? []
    expect(calls.length).toBeGreaterThan(0)
    expect(new Set(calls)).toEqual(new Set(['api.GET(']))
    expect(source).not.toMatch(/\bfetch\s*\(/)
  })
})
