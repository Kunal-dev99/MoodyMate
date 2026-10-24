import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/api/client'
import {
  columnLabel,
  columnsOf,
  combinedOnlyBuckets,
  combinedOnlyUnknown,
  compareMagnitude,
  comparisonHref,
  deltaText,
  fetchParts,
  firstNegativeText,
  flagState,
  heatLevel,
  heatScale,
  parentRunOf,
  partMove,
  scenarioRunsHref,
  toneOf,
  turnsNegative,
  unknownCombinedOnly,
  whatIfRows,
  type ComparisonBucket,
  type ComparisonCell,
  type ComparisonCurrency,
  type ComparisonFigure,
  type ComparisonRow,
  type ScenarioComparison,
  type ScenarioContribution,
  type ScenarioRunSummary,
} from '@/components/advanced/comparison'
import { ComparisonTable, DeltaCell } from '@/components/advanced/ComparisonView'
import { PartRow } from '@/components/advanced/ComparisonDrilldown'
import { WhatIfBody, WhatIfTable } from '@/components/advanced/WhatIfComparison'
import type { Schemas } from '@/api/client'

/*
 * FE-25.2 (Done-when): masked deltas, `null` flags and the combined-only highlight, plus the
 * exact (no-float) heat map, the drill-down's masked amounts and source-row link, the "what if"
 * alignment and the NEXT_PUBLIC_STRESS_COMPARE_UI switch. Fixtures use the generated types
 * (src/api/schema.d.ts) only; every figure is what a server would send.
 */

vi.mock('@/components/ui/use-toast', () => ({ toast: () => undefined }))

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllEnvs()
  vi.doUnmock('next/navigation')
  vi.resetModules()
})

const SCENARIO = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
const SCENARIO_RUN = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'
const RUN = (n: number) => `10000000-0000-4000-8000-${String(n).padStart(12, '0')}`
const M: Schemas['MaskedValue'] = { masked: true, classification: 'confidential' }

const fig = (baseline: ComparisonFigure['baseline'], stressed: ComparisonFigure['stressed'], delta: ComparisonFigure['delta']): ComparisonFigure => ({ baseline, stressed, delta })
const ZERO = fig('0', '0', '0')

function cell(subset: number[], combined: boolean, over: Partial<ComparisonCell> = {}): ComparisonCell {
  const id = combined ? 3 : subset[0]
  return {
    calculation_run_id: RUN(id),
    shock_subset: subset,
    combined,
    changed_flow_count: 0,
    inflows: ZERO,
    outflows: ZERO,
    net_gap: ZERO,
    cumulative_gap: ZERO,
    turns_negative_net: false,
    turns_negative_cumulative: false,
    ...over,
  }
}

const row = (code: string, seq: number, cells: ComparisonCell[], only: boolean | null): ComparisonRow => ({
  bucket_code: code,
  bucket_label: `Bucket ${seq}`,
  bucket_sequence: seq,
  cells,
  breaks_only_combined: only,
})

const BUCKETS: ComparisonBucket[] = [
  { bucket_definition_id: RUN(101), sequence: 1, code: 'B1', label: 'Bucket 1', lower_exclusive_days: 0, upper_inclusive_days: 7 },
  { bucket_definition_id: RUN(102), sequence: 2, code: 'B2', label: 'Bucket 2', lower_exclusive_days: 7, upper_inclusive_days: 30 },
  { bucket_definition_id: RUN(103), sequence: 3, code: 'B3', label: 'Bucket 3', lower_exclusive_days: 30, upper_inclusive_days: null },
]

/**
 * INR, two shocks and the combined run. B1: shock 2's net delta is masked (fewer than the minimum
 * of distinct changed flows), so the flags that read it are `null`, and its cumulative delta
 * stays masked for every later bucket (D-46 §3). B2 breaks only when combined. B3 is unchanged.
 */
const INR: ComparisonCurrency = {
  currency: 'INR',
  rows: [
    row(
      'B1',
      1,
      [
        cell([1], false, { changed_flow_count: 4, net_gap: fig('20', '10', '-10'), cumulative_gap: fig('20', '10', '-10'), inflows: fig('100', '90', '-10') }),
        cell([2], false, { changed_flow_count: 1, net_gap: fig('20', M, M), cumulative_gap: fig('20', M, M), turns_negative_net: null, turns_negative_cumulative: null, outflows: fig('80', M, M) }),
        cell([1, 2], true, { changed_flow_count: 5, net_gap: fig('20', '-5', '-25'), cumulative_gap: fig('20', '-5', '-25'), turns_negative_net: true, turns_negative_cumulative: true }),
      ],
      null,
    ),
    row(
      'B2',
      2,
      [
        cell([1], false, { changed_flow_count: 3, net_gap: fig('5', '0', '-5'), cumulative_gap: fig('25', '10', '-15') }),
        cell([2], false, { changed_flow_count: 3, net_gap: fig('5', '2', '-3'), cumulative_gap: fig('25', M, M), turns_negative_cumulative: null }),
        cell([1, 2], true, { changed_flow_count: 6, net_gap: fig('5', '-3', '-8'), cumulative_gap: fig('25', '-8', '-33'), turns_negative_net: true, turns_negative_cumulative: true }),
      ],
      true,
    ),
    row('B3', 3, [cell([1], false), cell([2], false, { cumulative_gap: fig('25', M, M), turns_negative_cumulative: null }), cell([1, 2], true, { cumulative_gap: fig('25', '-8', '-33') })], false),
  ],
  first_negative_cumulative: [
    { shock_subset: [1], combined: false, baseline_bucket_code: null, stressed_bucket_code: null },
    { shock_subset: [2], combined: false, baseline_bucket_code: null, stressed_bucket_code: M },
    { shock_subset: [1, 2], combined: true, baseline_bucket_code: null, stressed_bucket_code: 'B1' },
  ],
}

/** The attributes of every rendered element with this test id, in document order. */
function attrsOf(html: string, testId: string): Record<string, string>[] {
  const out: Record<string, string>[] = []
  for (const m of html.matchAll(new RegExp(`<[a-z]+\\b[^>]*data-testid="${testId}"[^>]*>`, 'g'))) {
    const attrs: Record<string, string> = {}
    for (const a of m[0].matchAll(/([a-z-]+)="([^"]*)"/g)) attrs[a[1]] = a[2]
    out.push(attrs)
  }
  return out
}

const renderTable = (metric: 'net_gap' | 'cumulative_gap', currency = INR) => renderToStaticMarkup(createElement(ComparisonTable, { currency, buckets: BUCKETS, metric, onDrill: () => undefined }))

describe('masked deltas (FE-25.2 Done-when)', () => {
  it('renders a masked delta as "masked" (MaskedValue), never as a blank or a zero, and never shades it', () => {
    const html = renderTable('net_gap')
    const cells = attrsOf(html, 'comparison-cell')
    expect(cells).toHaveLength(9)
    const b1s2 = cells.find((c) => c['data-subset'] === '2' && c['data-delta'] === 'masked')
    expect(b1s2, 'B1 shock 2 net delta is masked').toBeTruthy()
    expect(b1s2!['data-heat']).toBe('0')
    // The masked span carries the classification in its accessible label.
    expect(html).toContain('aria-label="Masked by policy: confidential data"')
    // Exactly one masked net delta in this fixture, and its clear neighbours keep their exact text.
    expect(cells.filter((c) => c['data-delta'] === 'masked')).toHaveLength(1)
    expect(cells.map((c) => c['data-delta'])).toEqual(['-10', 'masked', '-25', '-5', '-3', '-8', '0', '0', '0'])
    expect(attrsOf(html, 'comparison-delta').filter((a) => a['data-masked'] === 'true')).toHaveLength(1)
  })

  it('keeps a masked cumulative delta masked in every later bucket, exactly as the server sent it', () => {
    const cells = attrsOf(renderTable('cumulative_gap'), 'comparison-cell').filter((c) => c['data-subset'] === '2')
    expect(cells.map((c) => c['data-delta'])).toEqual(['masked', 'masked', 'masked'])
  })

  it('never lets a masked delta set or enter the heat scale, and grades clear ones exactly (BigInt, no float)', () => {
    expect(heatScale(INR, 'net_gap')).toBe('-25')
    expect(heatScale({ rows: [row('B1', 1, [cell([1], false, { net_gap: fig(M, M, M) })], null)] }, 'net_gap')).toBeNull()
    expect(heatLevel(M, '-25')).toBe(0)
    expect(heatLevel('0', '-25')).toBe(0)
    expect(heatLevel('-25', '-25')).toBe(4)
    expect(heatLevel('-6.25', '-25')).toBe(1)
    expect(heatLevel('-6.26', '-25')).toBe(2)
    expect(heatLevel('12.5', '-25.00')).toBe(2)
    // Beyond float precision: these two differ only in the last cent.
    expect(compareMagnitude('-12345678901234567890.02', '12345678901234567890.01')).toBe(1)
    expect(compareMagnitude(M, '0')).toBe(-1)
    expect(toneOf(M)).toBe('masked')
    expect(toneOf('-0.00')).toBe('zero')
    expect(deltaText('-1000.50')).toBe('-1,000.50')
    expect(deltaText('1234567.1')).toBe('+1,234,567.1')
    expect(deltaText('0')).toBe('0')
    expect(deltaText(M)).toBeNull()
  })

  it('shows masked inflows and outflows on expand, and masked drill-down amounts with clear days, buckets and templates', () => {
    const expanded = renderToStaticMarkup(createElement(ComparisonTable, { currency: INR, buckets: BUCKETS, metric: 'net_gap', defaultExpanded: ['B1'] }))
    expect(attrsOf(expanded, 'comparison-row-detail')).toHaveLength(1)
    const figures = attrsOf(expanded, 'comparison-figure').map((f) => f['data-figure'])
    expect(figures).toEqual(['inflows', 'outflows', 'net_gap', 'cumulative_gap'])
    expect(attrsOf(expanded, 'figure-delta').filter((a) => a['data-masked'] === 'true').length).toBeGreaterThanOrEqual(3)

    const part: ScenarioContribution = {
      scenario_contribution_id: RUN(500),
      calculation_run_id: RUN(3),
      shock_subset: [1, 2],
      cashflow_event_id: RUN(501),
      part_path: '1:moved',
      part_id: RUN(502),
      instrument_business_key: M,
      shock_sequences: [1],
      templates: ['DELAYED_INFLOWS'],
      change: 'MOVED',
      direction: 'INFLOW',
      origin: 'CONTRACTUAL',
      amount: M,
      currency: 'INR',
      classification: 'confidential',
      days_before: 3,
      days_after: 17,
      bucket_code_before: 'B1',
      bucket_code_after: 'B2',
      lineage: { cashflow_event_id: RUN(501), canonical_record_id: RUN(503), source_record_id: RUN(504), import_id: RUN(505), source_ordinal: 7 },
    }
    const html = renderToStaticMarkup(createElement('table', null, createElement('tbody', null, createElement(PartRow, { part }))))
    expect(attrsOf(html, 'comparison-part')[0]['data-masked']).toBe('true')
    expect(attrsOf(html, 'part-amount')[0]['data-masked']).toBe('true')
    expect(html).toContain('Day 3 → day 17 · bucket B1 → B2')
    expect(html).toContain('#1 DELAYED_INFLOWS')
    const link = attrsOf(html, 'part-source-link')[0]
    expect(link.href).toBe(`/ingestion/imports/${RUN(505)}`)
    expect(link['data-source-ordinal']).toBe('7')
    expect(html).toContain(RUN(504))
    expect(partMove({ ...part, days_after: 3, bucket_code_after: 'B1' })).toBe('Day 3 · bucket B1')
  })
})

describe('null flags (FE-25.2 Done-when)', () => {
  it('maps a null flag to "unknown", never to "no"', () => {
    expect(flagState(null)).toBe('unknown')
    expect(flagState(undefined)).toBe('unknown')
    expect(flagState(false)).toBe('no')
    expect(flagState(true)).toBe('yes')
    expect(turnsNegative(INR.rows[0].cells[1], 'net_gap')).toBe('unknown')
    expect(turnsNegative(INR.rows[1].cells[1], 'net_gap')).toBe('no')
    expect(turnsNegative(INR.rows[1].cells[1], 'cumulative_gap')).toBe('unknown')
  })

  it('renders a null turns-negative flag as "masked", and a true one as the turns-negative marker', () => {
    const cells = attrsOf(renderTable('net_gap'), 'comparison-cell')
    expect(cells.map((c) => c['data-turns-negative'])).toEqual(['no', 'unknown', 'yes', 'no', 'no', 'yes', 'no', 'no', 'no'])
    const one = (c: ComparisonCell) => renderToStaticMarkup(createElement('table', null, createElement('tbody', null, createElement('tr', null, createElement(DeltaCell, { cell: c, metric: 'net_gap', max: '-25' })))))
    const unknown = one(INR.rows[0].cells[1])
    expect(unknown).toContain('data-testid="turns-negative-unknown"')
    expect(unknown).not.toContain('data-testid="turns-negative"')
    const yes = one(INR.rows[0].cells[2])
    expect(yes).toContain('data-testid="turns-negative"')
    expect(yes).toContain('Turns negative')
    expect(one(INR.rows[0].cells[0])).not.toMatch(/data-testid="turns-negative(-unknown)?"/)
  })

  it('renders a null combined-only flag as masked or not assessable, and nothing for one shock (Q12)', () => {
    const html = renderTable('net_gap')
    const rows = attrsOf(html, 'comparison-row')
    expect(rows.map((r) => r['data-combined-only'])).toEqual(['null', 'true', 'false'])
    // B1 reads shock 2's masked stressed net gap: masked.
    expect(attrsOf(html, 'combined-only-unknown').map((u) => u['data-reason'])).toEqual(['masked'])
    expect(html).toContain('combined-only: masked')
    expect(unknownCombinedOnly(INR)).toEqual(['B1'])
    expect(INR.rows.map(combinedOnlyUnknown)).toEqual(['masked', null, null])
    expect(rows[0].class ?? '').not.toContain('outline-warning')

    // Two shocks, but only shock 1's single run was compared (shock 2 failed): not assessable, nothing masked.
    const partial: ComparisonCurrency = {
      ...INR,
      rows: [row('B1', 1, [cell([1], false, { net_gap: fig('20', '10', '-10') }), cell([1, 2], true, { net_gap: fig('20', '-5', '-25') })], null)],
    }
    expect(combinedOnlyUnknown(partial.rows[0])).toBe('not_assessable')
    const partialHtml = renderTable('net_gap', partial)
    expect(attrsOf(partialHtml, 'combined-only-unknown').map((u) => u['data-reason'])).toEqual(['not_assessable'])
    expect(partialHtml).toContain('combined-only: not assessable (needs every single-shock run)')
    expect(partialHtml).not.toContain('combined-only: masked')

    // A one-shock scenario: the flag is always null and the row shows nothing, even when masked.
    const single: ComparisonCurrency = {
      ...INR,
      rows: [row('B1', 1, [cell([1], true, { net_gap: fig('20', M, M), turns_negative_net: null })], null), row('B2', 2, [cell([1], true)], null)],
    }
    expect(single.rows.map(combinedOnlyUnknown)).toEqual(['one_shock', 'one_shock'])
    const singleHtml = renderTable('net_gap', single)
    expect(attrsOf(singleHtml, 'comparison-row').map((r) => r['data-combined-only'])).toEqual(['null', 'null'])
    expect(attrsOf(singleHtml, 'combined-only-unknown')).toHaveLength(0)
    expect(singleHtml).not.toContain('combined-only:')
  })

  it('reads a masked or absent first-negative bucket as masked or "never negative"', () => {
    expect(firstNegativeText(null)).toBe('never negative')
    expect(firstNegativeText(M)).toBeNull()
    expect(firstNegativeText('B1')).toBe('B1')
  })
})

describe('the combined-only highlight (FE-25.2 Done-when)', () => {
  it('highlights a bucket that breaks only when combined, distinctly from turns-negative', () => {
    const html = renderTable('net_gap')
    const rows = attrsOf(html, 'comparison-row')
    const b2 = rows.find((r) => r['data-bucket'] === 'B2')!
    expect(b2['data-combined-only']).toBe('true')
    expect(b2.class).toContain('outline-warning')
    expect(b2.class).toContain('bg-warning/10')
    expect(attrsOf(html, 'combined-only')).toHaveLength(1)
    expect(html).toContain('Breaks only when combined')
    // Turns-negative is the destructive badge; the combined-only highlight is the warning one.
    expect(attrsOf(html, 'turns-negative').every((a) => a.class.includes('bg-destructive'))).toBe(true)
    expect(attrsOf(html, 'combined-only')[0].class).toContain('--warning')
    expect(combinedOnlyBuckets(INR)).toEqual(['B2'])
  })

  it('heads a column per shock alone and one for the combined run, in the server order', () => {
    const html = renderTable('net_gap')
    expect(attrsOf(html, 'comparison-column').map((c) => [c['data-subset'], c['data-combined']])).toEqual([
      ['1', 'false'],
      ['2', 'false'],
      ['1+2', 'true'],
    ])
    expect(html).toContain('Shock 1 alone')
    expect(html).toContain('Combined (shocks 1 + 2)')
    expect(columnLabel({ combined: true, shock_subset: [1] })).toBe('Shock 1')
    expect(columnsOf({ currencies: [INR] }).map((c) => c.calculation_run_id)).toEqual([RUN(1), RUN(2), RUN(3)])
    expect(columnsOf({ currencies: [] })).toEqual([])
  })
})

describe('the drill-down and the "what if" (FE-25.2)', () => {
  it('asks for one member run by its shock subset, currency and bucket', async () => {
    const get = vi.spyOn(api, 'GET').mockResolvedValue({ data: { items: [], total: 0, limit: 100, offset: 0, scenario_run_id: SCENARIO_RUN, labels: { statement: 's' } }, error: undefined, response: new Response(null, { status: 200 }) } as never)
    await fetchParts(SCENARIO, SCENARIO_RUN, { currency: 'INR', bucketCode: 'B2', shockSubset: [1, 2] }, 0)(new AbortController().signal)
    expect(get).toHaveBeenCalledTimes(1)
    const [path, init] = get.mock.calls[0] as unknown as [string, { params: { path: unknown; query: unknown } }]
    expect(path).toBe('/api/v1/scenarios/{scenario_id}/comparison/contributions')
    expect(init.params.path).toEqual({ scenario_id: SCENARIO })
    expect(init.params.query).toEqual({ scenario_run_id: SCENARIO_RUN, currency: 'INR', bucket_code: 'B2', shock_subset: [1, 2], limit: 100, offset: 0 })
    expect(scenarioRunsHref(SCENARIO)).toBe(`/advanced/stress-explorer/${SCENARIO}/runs`)
    expect(comparisonHref(SCENARIO, SCENARIO_RUN)).toBe(`/advanced/stress-explorer/${SCENARIO}/runs/${SCENARIO_RUN}`)
  })

  const run = (id: string, version: number, baseline: string, created_at: string) => ({ scenario_run_id: id, version, baseline_run_id: baseline, created_at }) as ScenarioRunSummary

  it('picks the parent version\'s newest run on the same baseline only', () => {
    const runs = [run('a', 1, 'base', '2026-10-01T00:00:00Z'), run('b', 1, 'base', '2026-10-02T00:00:00Z'), run('c', 1, 'other', '2026-10-03T00:00:00Z'), run('d', 2, 'base', '2026-10-04T00:00:00Z')]
    expect(parentRunOf(runs, 1, 'base')?.scenario_run_id).toBe('b')
    expect(parentRunOf(runs, 3, 'base')).toBeNull()
    expect(parentRunOf(runs, null, 'base')).toBeNull()
    // Q30: the chosen parent run is shown (ID, created time, link); no picker; an empty state when none ran on this baseline.
    const parent = { version: 1, currencies: [INR], approval_state: 'EXPLORATION', approval_label: 'Exploration, not approved', reporting_date: '2026-09-30' } as ScenarioComparison
    const child = { version: 2, currencies: [INR] }
    const ready = renderToStaticMarkup(createElement(WhatIfBody, { scenarioId: SCENARIO, comparison: child, state: { kind: 'ready', value: { kind: 'ready', parentVersion: 1, run: parentRunOf(runs, 1, 'base')!, comparison: parent } } }))
    expect(attrsOf(ready, 'what-if')[0]['data-parent-scenario-run-id']).toBe('b')
    expect(attrsOf(ready, 'what-if-parent-link')[0].href).toBe(comparisonHref(SCENARIO, 'b'))
    expect(ready).toContain('created 2 Oct 2026, 00:00 UTC')
    expect(ready).not.toMatch(/<select/)
    const none = renderToStaticMarkup(createElement(WhatIfBody, { scenarioId: SCENARIO, comparison: child, state: { kind: 'ready', value: { kind: 'no_parent_run', parentVersion: 1 } } }))
    expect(attrsOf(none, 'what-if-no-run')[0]['data-parent-version']).toBe('1')
    expect(none).toContain('No run of parent v1 on this baseline')
    expect(none).not.toContain('what-if-table')
    expect(none).not.toContain('what-if-truncated')
    // Q18: the search read one capped page of scenario runs; when the server cut it, the empty state says so.
    const truncation = { shown: 200, total: 250, text: 'Showing the first 200 of 250 scenario runs on this baseline; the rest are not listed here.' }
    const cut = renderToStaticMarkup(createElement(WhatIfBody, { scenarioId: SCENARIO, comparison: child, state: { kind: 'ready', value: { kind: 'no_parent_run', parentVersion: 1, truncation } } }))
    expect(attrsOf(cut, 'what-if-truncated')[0]['data-total']).toBe('250')
    expect(cut).toContain(truncation.text)
  })

  it('sets the combined deltas side by side, masked ones as "masked"', () => {
    const parent: Pick<ScenarioComparison, 'currencies'> = {
      currencies: [{ ...INR, rows: INR.rows.map((r) => ({ ...r, cells: r.cells.map((c) => (c.combined ? { ...c, net_gap: fig('20', M, M), turns_negative_net: null } : c)) })) }],
    }
    const rows = whatIfRows({ currencies: [INR] }, parent)
    expect(rows.map((r) => r.bucketCode)).toEqual(['B1', 'B2', 'B3'])
    expect(rows[0].child?.net_gap.delta).toBe('-25')
    const html = renderToStaticMarkup(createElement(WhatIfTable, { rows, parentVersion: 1, version: 2 }))
    expect(attrsOf(html, 'what-if-parent-net').every((a) => a['data-masked'] === 'true')).toBe(true)
    expect(attrsOf(html, 'what-if-child-net').map((a) => a['data-value'])).toEqual(['-25', '-8', '0'])
    expect(attrsOf(html, 'what-if-row').map((r) => r['data-parent-turns-negative'])).toEqual(['unknown', 'unknown', 'unknown'])
    expect(html).toContain('v1 Δ net')
  })
})

describe('NEXT_PUBLIC_STRESS_COMPARE_UI (FE-25.2 Done-when, D-02)', () => {
  const notFound = vi.fn(() => {
    throw new Error('NEXT_NOT_FOUND')
  })
  const gate = async () => {
    vi.doMock('next/navigation', () => ({ notFound }))
    vi.resetModules()
    const { CompareGate } = await import('@/components/advanced/CompareGate')
    return () => renderToStaticMarkup(createElement(CompareGate, null, createElement('p', null, 'comparison')))
  }
  const section = async () => {
    vi.resetModules()
    const { ScenarioRunsSection } = await import('@/components/advanced/ScenarioRunsList')
    return renderToStaticMarkup(createElement(ScenarioRunsSection, { scenarioId: SCENARIO }))
  }

  it('renders the comparison routes and the runs section while on', async () => {
    notFound.mockClear()
    expect((await gate())()).toBe('<p>comparison</p>')
    expect(notFound).not.toHaveBeenCalled()
    expect(await section()).toContain('Runs against a baseline')
  })

  it('off: the comparison routes are a 404 and the runs section renders nothing, so the Phase 24 explorer is restored', async () => {
    notFound.mockClear()
    vi.stubEnv('NEXT_PUBLIC_STRESS_COMPARE_UI', 'off')
    expect(await gate()).toThrow('NEXT_NOT_FOUND')
    expect(notFound).toHaveBeenCalledTimes(1)
    expect(await section()).toBe('')
    vi.unstubAllEnvs()
    // The explorer off means the comparison off too.
    notFound.mockClear()
    vi.stubEnv('NEXT_PUBLIC_STRESS_EXPLORER_UI', 'off')
    expect(await gate()).toThrow('NEXT_NOT_FOUND')
    expect(await section()).toBe('')
  })

  it('puts every comparison route under the gated layout', () => {
    const root = fileURLToPath(new URL('../../../', import.meta.url))
    expect(readFileSync(`${root}app/(platform)/advanced/stress-explorer/[scenarioId]/runs/layout.tsx`, 'utf8')).toContain('<CompareGate>')
  })
})

describe('comparison code hygiene (FE-25.2)', () => {
  const root = fileURLToPath(new URL('../../../', import.meta.url))
  const files = [
    'components/advanced/comparison.ts',
    'components/advanced/ComparisonView.tsx',
    'components/advanced/ComparisonDrilldown.tsx',
    'components/advanced/WhatIfComparison.tsx',
    'components/advanced/DeltaValue.tsx',
    'components/advanced/ScenarioRunsList.tsx',
  ]
  it.each(files)('%s never parses a figure as a float and hand-writes no payload type', (file) => {
    const text = readFileSync(`${root}${file}`, 'utf8')
    for (const p of [/parseFloat/, /\bNumber\s*\(/, /\bNumber\./, /\.toFixed\s*\(/, /NumberFormat/, /^\s*(export\s+)?interface\s+\w*(Out|Page)\b/m]) expect(text, `${file} ${p}`).not.toMatch(p)
  })
})
