import { expect, test } from '@playwright/test'
import type { components } from '../src/api/schema'
import { sameDecimal, sumDecimals } from './calc-support'
import { API, bearer, cashflowImportReady, ensureActiveMapping, registerInstrumentSource, signIn, uploadSample, validateAndProject } from './support'

/**
 * Feature 05 Phase 25 browser check against the real stack (backend ENV=test, synthetic).
 * FE-25.2: the baseline-versus-stress comparison, as data_steward (calc:read, cleared for the
 * amounts under D-33):
 *   1. the scenario's runs list the risk analyst's exploration run, labelled
 *      "Exploration, not approved", with a link to its comparison;
 *   2. the comparison shows the server's illustrative statement, the approval label, the
 *      scenario run ID, the reporting date and the baseline, and a column per shock alone and one
 *      combined;
 *   3. one bucket's combined net delta equals the stressed run's ladder row minus the baseline's
 *      ladder row, exactly (both read from GET …/maturity-ladder);
 *   4. that cell's drill-down lists the changed parts, and a part's source-row link leads to its
 *      import, the row the part came from.
 *
 * Every payload type is the generated one (src/api/schema.d.ts). Needs a backend with
 * SCENARIOS_ENABLED and CALCULATIONS_ENABLED on (their defaults), the seeded maturity_ladder v1
 * and ACTIVE cashflow_direction rule (LOAN an inflow, DEPOSIT an outflow), a frontend built with
 * NEXT_PUBLIC_STRESS_EXPLORER_UI and NEXT_PUBLIC_STRESS_COMPARE_UI on (their defaults) and a
 * disposable database (D-03). The switch-off restore is a vitest (comparison.test.ts): one build
 * cannot serve both.
 *
 * Ordering: this file sorts before smoke.spec, whose applied proposal moves the `instrument`
 * schema past v1, which projector v1 refuses. It registers its own sources and mappings and
 * creates and runs its own scenario through the API (not under test; FE-25.1's spec covers the
 * run panel).
 */

type Schemas = components['schemas']

test.describe.configure({ mode: 'serial' })

const EXPLORER = '/advanced/stress-explorer'
const EXPLORATION = 'Exploration, not approved'
const REPORTING_DATE = '2026-12-31'
const MODULE = 'maturity_ladder'
const SUCCESS = ['SUCCEEDED', 'SUCCEEDED_WITH_WARNINGS']

let baseline: Schemas['RunDetail'] | null = null
let scenario: Schemas['ScenarioDetail'] | null = null
let scenarioRun: Schemas['ScenarioRunOut'] | null = null

const negate = (v: string) => (v.startsWith('-') ? v.slice(1) : `-${v}`)
const minus = (a: string, b: string) => sumDecimals([a, negate(b)])
const clear = (v: Schemas['ComparisonFigure']['delta'] | Schemas['LadderRow']['net_gap']): v is string => typeof v === 'string'

test('setup: a baseline maturity_ladder run and an exploration scenario run on it (API, not under test)', async ({ request }) => {
  const analyst = await bearer(request, 'risk_analyst')
  const steward = await bearer(request, 'data_steward')
  const { sourceId, contractId } = await registerInstrumentSource(request, steward)
  const instruments: Schemas['ImportDetail'] = await uploadSample(request, steward, sourceId, 'instrument.csv')
  expect(instruments.schema_version, 'projector v1 supports instrument v1 only: run before smoke.spec').toBe(1)
  await ensureActiveMapping(request, contractId, sourceId, instruments.id, `e2e_cmp_instr_${Date.now()}`)
  await validateAndProject(request, steward, instruments.id)
  const cashflows = await cashflowImportReady(request, steward)
  await validateAndProject(request, steward, cashflows)
  const snapshotBody: Schemas['SnapshotCreate'] = { import_ids: [instruments.id, cashflows] }
  const built = await request.post(`${API}/api/v1/dataset-snapshots`, { headers: analyst, data: snapshotBody })
  expect([200, 201], await built.text()).toContain(built.status())
  const snapshot: Schemas['SnapshotOut'] = await built.json()

  const modules: Schemas['ModulePage'] = await (await request.get(`${API}/api/v1/calculation-modules`, { headers: analyst })).json()
  const ladder = modules.items.find((m) => m.code === MODULE && m.implementation_status === 'AVAILABLE')
  expect(ladder?.runnable, 'maturity_ladder v1 is seeded AVAILABLE with every rule ACTIVE').toBe(true)
  const runBody: Schemas['RunCreate'] = { dataset_snapshot_id: snapshot.snapshot_id, module_version_id: ladder!.module_version_id, reporting_date: REPORTING_DATE }
  const ran = await request.post(`${API}/api/v1/calculation-runs`, { headers: { ...analyst, 'Idempotency-Key': `e2e-cmp-baseline-${Date.now()}` }, data: runBody })
  expect(ran.status(), await ran.text()).toBe(201)
  baseline = await ran.json()
  expect(SUCCESS).toContain(baseline!.status)

  // The FE-24.2 scenario's shocks; confirmed by its author for exploration only.
  const create: Schemas['ScenarioCreate'] = {
    name: `Two-week loan repayment delay (FE-25.2 e2e ${Date.now()})`,
    shocks: [
      { template: 'DELAYED_INFLOWS', parameters: { cashflow_type: 'LOAN', affected_fraction: '0.2000', delay_days: 14, delay_calendar: 'CALENDAR_DAYS' } },
      { template: 'DEPOSIT_WITHDRAWAL', parameters: { cashflow_type: 'DEPOSIT', increase_fraction: '0.1500' } },
    ],
  }
  const created = await request.post(`${API}/api/v1/scenarios`, { headers: analyst, data: create })
  expect(created.status(), await created.text()).toBe(201)
  scenario = await created.json()
  const confirm: Schemas['ScenarioVersionConfirm'] = { acknowledge_ai_origin: false }
  const confirmed = await request.post(`${API}/api/v1/scenarios/${scenario!.scenario_id}/versions/1/confirm`, { headers: analyst, data: confirm })
  expect(confirmed.status(), await confirmed.text()).toBe(200)

  const body: Schemas['ScenarioRunCreate'] = { version: 1, baseline_run_id: baseline!.run_id }
  const run = await request.post(`${API}/api/v1/scenarios/${scenario!.scenario_id}/run`, { headers: { ...analyst, 'Idempotency-Key': `e2e-cmp-run-${Date.now()}` }, data: body })
  expect(run.status(), await run.text()).toBe(201)
  scenarioRun = await run.json()
  expect(scenarioRun!.approval_label).toBe(EXPLORATION)
  for (const m of scenarioRun!.members) expect(SUCCESS, `member ${m.shock_subset.join('+')} ended ${m.status}`).toContain(m.status)
})

test('FE-25.2: as data_steward, a bucket\'s net delta equals the difference of the two ladder rows, and a drill-down row leads to its source row', async ({ page, request }) => {
  expect(baseline && scenario && scenarioRun, 'the setup test ran').toBeTruthy()
  const scenarioId = scenario!.scenario_id
  const steward = await bearer(request, 'data_steward')
  await signIn(page, 'Data steward')

  // 1. The scenario's runs, with the exploration label and the comparison link.
  await page.goto(`${EXPLORER}/${scenarioId}/runs`)
  const row = page.locator(`[data-testid="scenario-run-row"][data-id="${scenarioRun!.scenario_run_id}"]`)
  await expect(row).toHaveAttribute('data-approval-state', 'EXPLORATION')
  await expect(row.getByTestId('scenario-run-label')).toHaveText(EXPLORATION)
  const compared = page.waitForResponse((r) => r.request().method() === 'GET' && r.url().startsWith(`${API}/api/v1/scenarios/${scenarioId}/comparison?`))
  await row.getByTestId('scenario-run-compare').click()
  await page.waitForURL(`**${EXPLORER}/${scenarioId}/runs/${scenarioRun!.scenario_run_id}`)
  const response = await compared
  expect(response.status(), await response.text()).toBe(200)
  const comparison: Schemas['ScenarioComparisonOut'] = await response.json()

  // 2. Labels and identity.
  const view = page.getByTestId('comparison-view')
  await expect(view).toHaveAttribute('data-approval-state', 'EXPLORATION')
  await expect(page.getByTestId('comparison-approval')).toHaveText(EXPLORATION)
  await expect(page.getByTestId('stress-explorer-statement').first()).toHaveText(comparison.labels.statement)
  await expect(page.getByTestId('comparison-scenario-run-id')).toHaveText(scenarioRun!.scenario_run_id)
  await expect(page.getByTestId('comparison-reporting-date')).toHaveText(REPORTING_DATE)
  await expect(page.getByTestId('comparison-baseline-link')).toHaveAttribute('href', `/calculations/runs/${baseline!.run_id}`)
  await expect(page.getByTestId('summary-not-ai')).toBeVisible()
  for (const m of comparison.members) await expect(page.locator(`[data-testid="comparison-member"][data-run-id="${m.calculation_run_id}"]`)).toHaveAttribute('data-status', m.status)

  // 3. A combined cell whose net delta is clear and non-zero, checked against both ladders.
  const combined = comparison.members.find((m) => m.combined)!
  const ladderOf = async (runId: string): Promise<Schemas['MaturityLadderOut']> => {
    const r = await request.get(`${API}/api/v1/calculation-runs/${runId}/maturity-ladder`, { headers: steward })
    expect(r.status(), await r.text()).toBe(200)
    return r.json()
  }
  const [baseLadder, stressLadder] = await Promise.all([ladderOf(baseline!.run_id), ladderOf(combined.calculation_run_id)])
  const ladderRow = (l: Schemas['MaturityLadderOut'], currency: string, bucket: string) =>
    l.currencies.find((c) => c.currency === currency)?.rows.find((r) => r.bucket_code === bucket)
  const candidates = comparison.currencies.flatMap((cur) =>
    cur.rows.flatMap((r) => {
      const cell = r.cells.find((c) => c.combined)
      const b = ladderRow(baseLadder, cur.currency, r.bucket_code)
      const s = ladderRow(stressLadder, cur.currency, r.bucket_code)
      if (!cell || !b || !s || !clear(cell.net_gap.delta) || !clear(b.net_gap) || !clear(s.net_gap) || sameDecimal(cell.net_gap.delta, '0') || cell.changed_flow_count === 0) return []
      return [{ currency: cur.currency, bucket: r.bucket_code, cell, base: b.net_gap, stressed: s.net_gap }]
    }),
  )
  expect(candidates.length, 'a bucket with a clear, non-zero combined net delta for data_steward').toBeGreaterThan(0)
  const target = candidates[0]
  expect(sameDecimal(target.cell.net_gap.delta as string, minus(target.stressed, target.base)), `${target.stressed} - ${target.base} = ${target.cell.net_gap.delta}`).toBe(true)

  const cell = page.locator(`[data-testid="comparison-row"][data-currency="${target.currency}"][data-bucket="${target.bucket}"] [data-testid="comparison-cell"][data-combined="true"]`)
  await expect(cell).toHaveAttribute('data-metric', 'net_gap')
  const shown = await cell.getAttribute('data-delta')
  expect(shown).not.toBe('masked')
  expect(sameDecimal(shown!, minus(target.stressed, target.base)), `shown ${shown} = ${target.stressed} - ${target.base}`).toBe(true)

  // 4. Drill down to the changed parts, then to a source row.
  const parts = page.waitForResponse((r) => r.request().method() === 'GET' && r.url().startsWith(`${API}/api/v1/scenarios/${scenarioId}/comparison/contributions?`))
  await cell.getByTestId('comparison-drill').click()
  const partsResponse = await parts
  expect(partsResponse.status(), await partsResponse.text()).toBe(200)
  const url = new URL(partsResponse.url())
  expect(url.searchParams.get('currency')).toBe(target.currency)
  expect(url.searchParams.get('bucket_code')).toBe(target.bucket)
  expect(url.searchParams.getAll('shock_subset')).toEqual(combined.shock_subset.map(String))
  const page1: Schemas['ScenarioContributionPage'] = await partsResponse.json()
  expect(page1.items.length, 'the changed bucket has changed parts').toBeGreaterThan(0)
  const drill = page.getByTestId('comparison-drilldown')
  await expect(drill).toHaveAttribute('data-bucket', target.bucket)
  const first = page1.items[0]
  const part = drill.locator(`[data-testid="comparison-part"][data-id="${first.scenario_contribution_id}"]`)
  await expect(part).toHaveAttribute('data-change', first.change)
  // data_steward is cleared for the amounts (D-46 §4): shown, not masked.
  await expect(part).toHaveAttribute('data-masked', String(typeof first.amount !== 'string'))
  await expect(part.getByTestId('part-source-record')).toHaveText(first.lineage.source_record_id)
  const link = part.getByTestId('part-source-link')
  await expect(link).toContainText(`row ${first.lineage.source_ordinal}`)
  await link.click()
  await page.waitForURL(`**/ingestion/imports/${first.lineage.import_id}`)
  await expect(page.getByTestId('import-id')).toHaveText(first.lineage.import_id)
})
