import { expect, test, type Page } from '@playwright/test'
import type { components } from '../src/api/schema'
import { projectedSyntheticImports, sameDecimal, sumDecimals } from './calc-support'
import { API, bearer, groupDecimal, signIn } from './support'

/**
 * Feature 03 Phase 17 browser checks against the real stack (backend ENV=test, synthetic).
 * FE-17.1: risk_analyst builds a snapshot of the projected synthetic instrument, position and
 *          cash-flow imports, runs lcr_hqla v1 from the New-run panel, and the run detail
 *          shows the liquidity view (not the ladder). The approver (cleared for the asset rows,
 *          D-33) sees every currency as the server computed it: HQLA by level with its asset
 *          rows (included, or excluded with the reason), outflows, inflows before and after
 *          the applied cap, and the ratio or "not computable" with LCR_DENOMINATOR_ZERO; the
 *          displayed HQLA total equals the sum of its displayed included rows. risk_analyst
 *          sees the asset rows "masked" and every figure exactly as the server sent it.
 * FE-32.1: the LCR run shows "Per-currency results only; FX consolidation is blocked ..." and
 *          the context's `consolidation: "BLOCKED"`.
 *
 * Every payload type is the generated one (src/api/schema.d.ts); none is written here.
 * Decimal text is compared exactly (scaled integers), never as floats.
 *
 * Ordering: this file sorts before smoke.spec, whose applied proposal moves the `instrument`
 * schema past v1, which projector v1 refuses. It registers its own sources and mappings, so
 * core_banking stays unmapped for mappings.spec. Run it on a fresh database (CI does; locally a
 * disposable copy, D-03).
 */

type Schemas = components['schemas']

test.describe.configure({ mode: 'serial' })

// A LOAN cash flow of the synthetic sample (2026-12-28) falls within the 30-day inflow horizon.
const REPORTING_DATE = '2026-12-20'
const MODULE = 'lcr_hqla'

let run: Schemas['RunDetail'] | null = null

const isPost = (page: Page, url: string) => page.waitForResponse((r) => r.request().method() === 'POST' && r.url() === url)
// Money figures are shown grouped by thousands (P-7); the ratio is not money and is not grouped.
const shown = (v: unknown): string => (typeof v === 'string' ? groupDecimal(v) : v === null ? '' : 'masked')

/** A currency's figures and where the liquidity view shows each (FE-17.1). */
const figures = (c: Schemas['LiquidityCurrency']) =>
  [
    ['lcr-hqla', c.hqla_total],
    ['lcr-outflow-balance', c.outflow_balance],
    ['lcr-outflows', c.outflows],
    ['lcr-inflows-gross', c.inflows_gross],
    ['lcr-inflows-before-cap', c.inflows_before_cap],
    ['lcr-inflow-cap', c.inflow_cap_amount],
    ['lcr-inflows', c.inflows_after_cap],
    ['lcr-net-outflows', c.net_outflows],
  ] as const

test('FE-17.1: risk_analyst runs lcr_hqla on the synthetic snapshot; the run detail shows the liquidity view', async ({ page, request }) => {
  // Setup: the three synthetic samples, projected (not under test).
  const imports = await projectedSyntheticImports(request, 'e2e_lcr')

  const analystApi = await bearer(request, 'risk_analyst')
  const modules: Schemas['ModulePage'] = await (await request.get(`${API}/api/v1/calculation-modules`, { headers: analystApi })).json()
  const lcr = modules.items.find((m) => m.code === MODULE && m.implementation_status === 'AVAILABLE')
  expect(lcr?.runnable, 'lcr_hqla v1 is seeded AVAILABLE with every rule ACTIVE (BE-17.1)').toBe(true)
  expect(lcr!.illustrative).toBe(true)

  await signIn(page, 'Risk analyst')
  await page.goto('/calculations/new')
  for (const [code, id] of Object.entries(imports)) {
    const pick = page.getByTestId(`snapshot-pick-${code}`)
    await expect(pick.locator(`option[value="${id}"]`)).toHaveCount(1)
    await pick.selectOption(id)
  }
  const built = isPost(page, `${API}/api/v1/dataset-snapshots`)
  await page.getByTestId('snapshot-build').click()
  const snapshotResponse = await built
  expect([200, 201], await snapshotResponse.text()).toContain(snapshotResponse.status())
  const snapshot: Schemas['SnapshotOut'] = await snapshotResponse.json()
  expect(snapshot.members.map((m) => m.import_id).sort()).toEqual(Object.values(imports).sort())

  await page.locator(`[data-testid="module-option"][data-module-version-id="${lcr!.module_version_id}"]`).getByTestId('module-radio').check()
  await page.getByTestId('reporting-date').fill(REPORTING_DATE)
  const pins = page.getByTestId('pins-preview')
  await expect(pins.locator('[data-testid="pin-row"][data-missing="true"]')).toHaveCount(0)
  for (const rule of lcr!.rules)
    await expect(pins.locator(`[data-testid="pin-row"][data-kind="RULE_VERSION"][data-id="${rule.active_version!.rule_version_id}"]`)).toContainText(rule.code)

  const submitted = isPost(page, `${API}/api/v1/calculation-runs`)
  await page.getByTestId('run-submit').click()
  const runResponse = await submitted
  expect(runResponse.status(), await runResponse.text()).toBe(201)
  run = await runResponse.json()
  expect(run!.module.code).toBe(MODULE)
  expect(['SUCCEEDED', 'SUCCEEDED_WITH_WARNINGS']).toContain(run!.status)
  await page.waitForURL(`**/calculations/runs/${run!.run_id}`)
  await expect(page.getByTestId('run-detail-status')).toHaveAttribute('data-status', run!.status)
  // The liquidity view takes the ladder's place, with the run's identity and the prototype label.
  const view = page.getByTestId('liquidity')
  await expect(view).toBeVisible()
  await expect(page.getByTestId('maturity-ladder')).toHaveCount(0)
  await expect(view.getByTestId('identity-run-id')).toHaveText(run!.run_id)
  await expect(view.getByTestId('identity-reporting-date')).toHaveText(REPORTING_DATE)
  await expect(view.getByTestId('lcr-prototype-banner')).toContainText('not a regulatory LCR')
  await expect(page.getByTestId('illustrative-banner')).toContainText('Illustrative prototype — not regulatory')
})

test('FE-17.1: the approver sees HQLA components, outflows, capped inflows and the ratio; the HQLA total equals its rows', async ({ page, request }) => {
  expect(run).not.toBeNull()
  const runId = run!.run_id
  await signIn(page, 'Approver')
  const url = `${API}/api/v1/calculation-runs/${runId}/liquidity`
  const read = page.waitForResponse((r) => r.request().method() === 'GET' && r.url() === url)
  await page.goto(`/calculations/runs/${runId}`)
  const liquidity: Schemas['LiquidityOut'] = await (await read).json()
  expect(liquidity.currencies.length).toBeGreaterThan(0)
  const view = page.getByTestId('liquidity')
  await expect(view.getByTestId('lcr-currency')).toHaveCount(liquidity.currencies.length)
  await expect(view.getByTestId('lcr-min-aggregate-count')).toHaveText(String(liquidity.min_aggregate_count))
  // FE-30.1 (D-56): the LCR read's own context block, with the ratio unit, and no mismatch with the run's pins.
  const lcrContext = view.locator('[data-testid="result-context"][data-source="liquidity"]')
  await expect(lcrContext.getByTestId('context-as-of-date')).toHaveText(liquidity.context.as_of_date)
  await expect(lcrContext.getByTestId('context-validation-status')).toHaveText('Illustrative — not validated')
  if (liquidity.context.units.ratio !== null) await expect(lcrContext.getByTestId('context-units-ratio')).toHaveText(liquidity.context.units.ratio)
  await expect(view.getByTestId('context-mismatch')).toHaveCount(0)
  // FE-32.1 (D-61 §6): per-currency results only; FX consolidation is blocked, as the LCR read's context says.
  expect(liquidity.context.consolidation).toBe('BLOCKED')
  const fx = view.getByTestId('fx-consolidation-notice')
  await expect(fx).toHaveText('Per-currency results only; FX consolidation is blocked until an approved rate source and conversion policy exist')
  await expect(fx).toHaveAttribute('data-consolidation', 'BLOCKED')
  await expect(lcrContext.getByTestId('context-consolidation')).toContainText('BLOCKED')

  let checkedTotals = 0
  for (const c of liquidity.currencies) {
    const block = view.locator(`[data-testid="lcr-currency"][data-currency="${c.currency}"]`)
    // The approver (clearance confidential) is cleared for every figure and asset row (D-33).
    await expect(block.locator('[data-masked="true"]')).toHaveCount(0)
    // Every figure exactly as the server sent it.
    for (const [testId, value] of figures(c)) await expect(block.getByTestId(testId), `${c.currency} ${testId}`).toHaveText(shown(value))
    await expect(block.getByTestId('lcr-cap-rate')).toHaveText(c.inflow_cap_rate)
    // The applied cap: inflows after the cap, and whether it binds, as the server said.
    await expect(block.getByTestId('lcr-cap-applied')).toHaveAttribute('data-applied', String(c.inflow_cap_applied))
    // The ratio, or "not computable" with the warning when the server computed none.
    const ratio = block.getByTestId('lcr-ratio')
    if (c.ratio === null) {
      await expect(ratio).toHaveAttribute('data-computable', 'false')
      await expect(ratio).toContainText('not computable')
      await expect(block.getByTestId('lcr-ratio-warning')).toContainText('LCR_DENOMINATOR_ZERO')
    } else await expect(ratio).toHaveText(typeof c.ratio === 'string' ? c.ratio : 'masked')

    // HQLA components: every asset row, included (by level) or excluded with its reason.
    await expect(block.getByTestId('hqla-asset')).toHaveCount(c.assets.length)
    await expect(block.getByTestId('hqla-level')).toHaveCount(c.hqla_levels.length)
    await expect(block.locator('[data-testid="hqla-asset"][data-included="false"]')).toHaveCount(c.excluded_asset_count)
    for (const a of c.assets.filter((x) => !x.included)) {
      const row = block.locator(`[data-testid="hqla-asset"][data-id="${a.hqla_asset_result_id}"]`)
      await expect(row).toHaveAttribute('data-included', 'false')
      await expect(row.getByTestId('hqla-asset-reason')).toContainText(a.exclusion_reason!)
    }
    // The displayed HQLA total equals the sum of its displayed included rows (exact decimals).
    const included = block.locator('[data-testid="hqla-asset"][data-included="true"]').getByTestId('hqla-asset-hqla')
    const values = (await included.allTextContents()).map((t) => t.trim())
    const total = (await block.getByTestId('lcr-hqla').textContent())!.trim()
    expect(total).toBe(groupDecimal(c.hqla_total as string))
    expect(sameDecimal(sumDecimals(values), total), `${c.currency}: HQLA ${values.join(' + ')} = ${total}`).toBe(true)
    // And each level's displayed subtotal equals its displayed rows.
    for (const level of await block.getByTestId('hqla-level').all()) {
      const code = (await level.getAttribute('data-level'))!
      const rows = block.locator(`[data-testid="hqla-asset"][data-included="true"][data-level="${code}"]`).getByTestId('hqla-asset-hqla')
      const sub = (await level.getByTestId('hqla-level-total').textContent())!.trim()
      expect(sameDecimal(sumDecimals((await rows.allTextContents()).map((t) => t.trim())), sub), `${c.currency} level ${code}`).toBe(true)
    }
    checkedTotals += 1
  }
  expect(checkedTotals).toBe(liquidity.currencies.length)
  if (process.env.CALC_SCREENSHOT) await page.screenshot({ path: `${process.env.CALC_SCREENSHOT}/liquidity.png`, fullPage: true })
})

test('FE-17.1: risk_analyst sees the asset rows masked (D-33) and every figure as the server sent it', async ({ page }) => {
  expect(run).not.toBeNull()
  const runId = run!.run_id
  await signIn(page, 'Risk analyst')
  const url = `${API}/api/v1/calculation-runs/${runId}/liquidity`
  const read = page.waitForResponse((r) => r.request().method() === 'GET' && r.url() === url)
  await page.goto(`/calculations/runs/${runId}`)
  const liquidity: Schemas['LiquidityOut'] = await (await read).json()
  const view = page.getByTestId('liquidity')
  let maskedRows = 0
  for (const c of liquidity.currencies) {
    const block = view.locator(`[data-testid="lcr-currency"][data-currency="${c.currency}"]`)
    // Every figure exactly as the server sent it: the decimal text, or "masked" with why.
    for (const [testId, value] of figures(c)) {
      const cell = block.getByTestId(testId)
      if (typeof value === 'string') {
        await expect(cell).toHaveText(groupDecimal(value))
        await expect(cell).not.toHaveAttribute('data-masked', 'true')
      } else {
        await expect(cell).toHaveAttribute('data-masked', 'true')
        await expect(cell).toHaveText('masked')
        await expect(block.getByTestId(`${testId}-hint`)).toContainText(`fewer than ${liquidity.min_aggregate_count} contributions`)
      }
    }
    if (c.ratio === null) await expect(block.getByTestId('lcr-ratio')).toContainText('not computable')
    else if (typeof c.ratio === 'string') await expect(block.getByTestId('lcr-ratio')).toHaveText(c.ratio)
    else await expect(block.getByTestId('lcr-ratio')).toHaveAttribute('data-masked', 'true')
    for (const a of c.assets) {
      if (typeof a.balance === 'string') continue
      maskedRows += 1
      await expect(block.locator(`[data-testid="hqla-asset"][data-id="${a.hqla_asset_result_id}"]`)).toHaveAttribute('data-masked', 'true')
    }
  }
  expect(maskedRows, 'asset rows read the confidential balance (D-33)').toBeGreaterThan(0)
})
