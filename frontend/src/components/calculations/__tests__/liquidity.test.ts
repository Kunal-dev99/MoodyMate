import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { LcrCurrencyView, LcrPrototypeNotice, LcrRatio } from '@/components/calculations/LiquidityView'
import {
  EXCLUSION_TEXT,
  HQLA_LEVEL_TEXT,
  LCR_MODULE,
  LCR_WARNING_TEXT,
  POST_CHECK_TEXT,
  hqlaAssetCount,
  lcrMaskHint,
  showsLiquidity,
  type HqlaAsset,
  type LcrCurrency,
} from '@/components/calculations/calculations'

/*
 * FE-17.1: the liquidity view of an lcr_hqla run. Fixtures follow the backend's engineering
 * golden case (lcr_kit.py, D-35 seed); every type is the generated one.
 */

const openapi = JSON.parse(readFileSync(fileURLToPath(new URL('../../../../../backend/openapi.json', import.meta.url)), 'utf8'))
const enumOf = (name: string): string[] => [...openapi.components.schemas[name].enum].sort()

const MASKED = { masked: true as const, classification: 'confidential' }
const ID = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`

const asset = (n: number, over: Partial<HqlaAsset> = {}): HqlaAsset => ({
  hqla_asset_result_id: ID(n),
  sequence: n,
  included: true,
  level: 'L1',
  exclusion_reason: null,
  instrument_type: 'BOND',
  balance: '200.00',
  haircut: '0',
  hqla_value: '200.00',
  classification: 'confidential',
  rule_version_ids: [ID(900)],
  lineage: {
    position_version_id: ID(100 + n),
    position_id: ID(200 + n),
    position_business_key: `LP-${n}`,
    instrument_id: ID(300 + n),
    instrument_version_id: ID(400 + n),
    instrument_business_key: `LQ-${n}`,
    canonical_record_id: ID(500 + n),
    import_id: ID(600),
    source_record_id: ID(700 + n),
    source_ordinal: n,
  },
  ...over,
})

const level = (code: LcrCurrency['hqla_levels'][number]['level'], total: string, count: number): LcrCurrency['hqla_levels'][number] => ({
  level: code,
  total,
  asset_count: count,
  classification: 'internal',
})

// USD of the golden case: HQLA 300.00, no outflows, so the cap is 0 and the denominator is 0.
const usd = (over: Partial<LcrCurrency> = {}): LcrCurrency => ({
  lcr_result_id: ID(1),
  currency: 'USD',
  classification: 'internal',
  hqla_total: '300.00',
  hqla_levels: [level('L1', '300.00', 1), level('L2A', '0', 0), level('L2B', '0', 0)],
  assets: [asset(1, { balance: '300.00', hqla_value: '300.00' })],
  excluded_asset_count: 0,
  outflow_balance: '0',
  outflows: '0',
  outflow_count: 0,
  inflows_gross: '400.00',
  inflows_before_cap: '200',
  inflow_cap_rate: '0.75',
  inflow_cap_amount: '0',
  inflows_after_cap: '0',
  inflow_cap_applied: true,
  inflow_count: 1,
  net_outflows: '0',
  ratio: null,
  warnings: ['LCR_DENOMINATOR_ZERO'],
  ...over,
})

// EUR of the golden case: three L1 bonds, one ineligible loan; the cap binds; ratio 1.2340.
const eur = (over: Partial<LcrCurrency> = {}): LcrCurrency => ({
  ...usd(),
  lcr_result_id: ID(2),
  currency: 'EUR',
  hqla_total: '246.81',
  hqla_levels: [level('L1', '246.81', 3), level('L2A', '0', 0), level('L2B', '0', 0)],
  assets: [
    asset(1),
    asset(2, { balance: '40.00', hqla_value: '40.00' }),
    asset(3, { balance: '6.81', hqla_value: '6.81' }),
    asset(4, { included: false, level: null, exclusion_reason: 'NOT_HQLA_ELIGIBLE', instrument_type: 'LOAN', balance: '5000.00', haircut: null, hqla_value: '0' }),
  ],
  excluded_asset_count: 1,
  outflow_balance: '8000.00',
  outflows: '800',
  outflow_count: 3,
  inflows_gross: '1500.00',
  inflows_before_cap: '750',
  inflow_cap_amount: '600',
  inflows_after_cap: '600',
  inflow_cap_applied: true,
  inflow_count: 3,
  net_outflows: '200',
  ratio: '1.2340',
  warnings: [],
  ...over,
})

const html = (row: LcrCurrency, min = 3) => renderToStaticMarkup(createElement(LcrCurrencyView, { row, min, horizonDays: 30 }))

describe('the LCR ratio (FE-17.1): the server’s text, masked, or "not computable" for a NULL ratio', () => {
  it('renders a NULL ratio as "not computable" with the LCR_DENOMINATOR_ZERO warning, never a number', () => {
    const out = renderToStaticMarkup(createElement(LcrRatio, { row: { ratio: null }, min: 3 }))
    expect(out).toContain('data-testid="lcr-ratio" data-computable="false"')
    expect(out).toContain('not computable')
    expect(out).toMatch(/data-testid="lcr-ratio-warning"[^>]*data-code="LCR_DENOMINATOR_ZERO"/)
    expect(out).toContain(LCR_WARNING_TEXT.LCR_DENOMINATOR_ZERO)
    expect(out).not.toMatch(/>\s*-?\d+(\.\d+)?\s*</)
    expect(out).not.toContain('Infinity')
    expect(out).not.toContain('NaN')
    expect(out).not.toContain('masked')
  })

  it('a NULL ratio inside the currency view: the zero denominator and the cap figures stay as the server sent them', () => {
    const out = html(usd())
    expect(out).toContain('data-computable="false"')
    expect(out).toContain('not computable')
    expect(out).toMatch(/data-testid="lcr-net-outflows"[^>]*>0</)
    expect(out).toMatch(/data-testid="lcr-inflow-cap"[^>]*>0</)
    expect(out).toMatch(/data-testid="lcr-inflows-before-cap"[^>]*>200</)
    // The run's per-currency warning is listed with its meaning.
    expect(out).toMatch(/data-testid="lcr-warning" data-code="LCR_DENOMINATOR_ZERO"/)
  })

  it('renders a clear ratio as its exact text (4 dp, never re-rounded) and a masked one as "masked" with why', () => {
    const exact = renderToStaticMarkup(createElement(LcrRatio, { row: { ratio: '1.2340' }, min: 3 }))
    expect(exact).toMatch(/data-testid="lcr-ratio"[^>]*>1\.2340</)
    expect(exact).not.toContain('not computable')
    const masked = renderToStaticMarkup(createElement(LcrRatio, { row: { ratio: MASKED }, min: 3 }))
    expect(masked).toContain('data-testid="lcr-ratio" data-masked="true"')
    expect(masked).toContain('data-testid="masked-value"')
    expect(masked).toMatch(/data-testid="lcr-ratio-hint"[^>]*>masked: derived from a figure with fewer than 3 contributions</)
    expect(masked).not.toContain('not computable')
  })
})

describe('HQLA components, outflows and capped inflows (FE-17.1)', () => {
  it('lists each level with its included rows and the excluded rows with their reason', () => {
    const out = html(eur())
    expect(out.match(/data-testid="hqla-asset"/g)).toHaveLength(4)
    expect(out.match(/data-testid="hqla-level"/g)).toHaveLength(3)
    expect(out.match(/data-included="true"/g)).toHaveLength(3)
    expect(out).toMatch(/data-testid="hqla-asset"[^>]*data-included="false"[^>]*data-level=""/)
    expect(out).toMatch(/data-testid="hqla-asset-reason" data-reason="NOT_HQLA_ELIGIBLE"/)
    expect(out).toContain(EXCLUSION_TEXT.NOT_HQLA_ELIGIBLE)
    expect(out).toMatch(/data-testid="hqla-excluded" data-count="1"/)
    expect(out).toMatch(/data-testid="hqla-level-total"[^>]*>246\.81</)
    expect(out).toMatch(/data-testid="lcr-hqla"[^>]*>246\.81</)
    // Each row links to its instrument and its import's source row.
    expect(out).toContain(`href="/ingestion/imports/${ID(600)}"`)
    expect(out).toContain('row 4')
  })

  it('shows the applied cap: the cap rate, the cap amount, and that it binds', () => {
    const out = html(eur())
    expect(out).toMatch(/data-testid="lcr-cap-rate"[^>]*>0\.75</)
    expect(out).toMatch(/data-testid="lcr-inflow-cap"[^>]*>600</)
    expect(out).toMatch(/data-testid="lcr-inflows"[^>]*>600</)
    expect(out).toMatch(/data-testid="lcr-cap-applied" data-applied="true"[^>]*>cap applied</)
    const loose = html(eur({ inflows_after_cap: '30', inflow_cap_applied: false }))
    expect(loose).toMatch(/data-testid="lcr-cap-applied" data-applied="false"[^>]*>cap not binding</)
    const hidden = html(eur({ inflow_cap_applied: MASKED }))
    expect(hidden).toMatch(/data-testid="lcr-cap-applied" data-applied="masked" data-masked="true"/)
  })

  it('shows exact decimal text as the server sent it (never parsed or reformatted)', () => {
    const out = html(eur({ hqla_total: '9007199254740993.000000000000000001' }))
    expect(out).toContain('>9007199254740993.000000000000000001<')
  })

  it('risk_analyst: asset rows and figures from too few contributions are masked, with why (D-33)', () => {
    const rows = eur().assets.map((a) => ({ ...a, balance: MASKED, hqla_value: MASKED }))
    const out = html(
      eur({
        assets: rows,
        hqla_total: MASKED,
        hqla_levels: [level('L1', '0', 0), level('L2A', '0', 0), level('L2B', '0', 0)].map((l, i) => (i === 0 ? { ...l, total: MASKED, asset_count: 2 } : l)),
        inflows_after_cap: MASKED,
        ratio: MASKED,
      }),
    )
    expect(out.match(/data-testid="hqla-asset"[^>]*data-masked="true"/g)).toHaveLength(4)
    expect(out).toMatch(/data-testid="lcr-hqla-hint"[^>]*>masked: fewer than 3 contributions</)
    expect(out).toMatch(/data-testid="hqla-level-total-hint"[^>]*>masked: fewer than 3 contributions</)
    expect(out).toMatch(/data-testid="lcr-inflows-hint"[^>]*>masked: derived from a figure with fewer than 3 contributions</)
    // Clear figures (built from enough contributions) stay clear, without a hint.
    expect(out).toMatch(/data-testid="lcr-outflows"[^>]*>800</)
    expect(out).not.toContain('lcr-outflows-hint')
  })

  it('explains a masked figure by its own count, or as derived; nothing for a clear one', () => {
    expect(lcrMaskHint('0', 1, 3)).toBeNull()
    expect(lcrMaskHint(MASKED, 2, 3)).toBe('masked: fewer than 3 contributions')
    expect(lcrMaskHint(MASKED, 5, 3)).toBe('masked: derived from a figure with fewer than 3 contributions')
    expect(lcrMaskHint(MASKED, null, 4)).toBe('masked: derived from a figure with fewer than 4 contributions')
    expect(hqlaAssetCount(eur())).toBe(3)
  })

  it('labels the prototype with the server’s limitations', () => {
    const out = renderToStaticMarkup(createElement(LcrPrototypeNotice, { limitations: ['No L2A/L2B caps.', 'Per currency, no FX.'] }))
    expect(out).toContain('LCR/HQLA prototype — not a regulatory LCR')
    expect(out.match(/data-testid="lcr-limitation"/g)).toHaveLength(2)
    expect(out).toContain('No L2A/L2B caps.')
  })
})

describe('liquidity enums and contract (FE-17.1, D-23)', () => {
  it('covers exactly the OpenAPI LcrWarningCode, HqlaExclusionReason and HqlaLevel enums, and every PostCheck', () => {
    expect(Object.keys(LCR_WARNING_TEXT).sort()).toEqual(enumOf('LcrWarningCode'))
    expect(Object.keys(EXCLUSION_TEXT).sort()).toEqual(enumOf('HqlaExclusionReason'))
    expect(Object.keys(HQLA_LEVEL_TEXT).sort()).toEqual(enumOf('HqlaLevel'))
    expect(Object.keys(POST_CHECK_TEXT).sort()).toEqual(enumOf('PostCheck'))
  })

  it('the API serves GET /calculation-runs/{run_id}/liquidity with a nullable ratio and the minimum', () => {
    expect(openapi.paths['/api/v1/calculation-runs/{run_id}/liquidity']?.get).toBeTruthy()
    expect(openapi.components.schemas.LiquidityOut.required).toContain('min_aggregate_count')
    expect(JSON.stringify(openapi.components.schemas.LiquidityCurrency.properties.ratio)).toContain('"null"')
  })
})

describe('the run detail mounts the liquidity view for lcr_hqla runs only, behind a switch (FE-17.1, D-02)', () => {
  afterEach(() => {
    vi.unstubAllEnvs()
    vi.resetModules()
  })

  it('an lcr_hqla run shows the liquidity view; any other module keeps the ladder', () => {
    expect(LCR_MODULE).toBe('lcr_hqla')
    expect(showsLiquidity({ module: { module_id: 'm', module_version_id: 'v', code: 'lcr_hqla', name: 'LCR', version: 1 } }, true)).toBe(true)
    expect(showsLiquidity({ module: { module_id: 'm', module_version_id: 'v', code: 'maturity_ladder', name: 'Ladder', version: 1 } }, true)).toBe(false)
    expect(showsLiquidity({ module: { module_id: 'm', module_version_id: 'v', code: 'lcr_hqla', name: 'LCR', version: 1 } }, false)).toBe(false)
  })

  it('NEXT_PUBLIC_LIQUIDITY_UI=off restores the Phase 15 run detail (the ladder section)', async () => {
    vi.stubEnv('NEXT_PUBLIC_LIQUIDITY_UI', 'off')
    vi.resetModules()
    const features = await import('@/config/features')
    expect(features.LIQUIDITY_UI_ENABLED).toBe(false)
    const calc = await import('@/components/calculations/calculations')
    expect(calc.showsLiquidity({ module: { module_id: 'm', module_version_id: 'v', code: 'lcr_hqla', name: 'LCR', version: 1 } })).toBe(false)
  })
})
