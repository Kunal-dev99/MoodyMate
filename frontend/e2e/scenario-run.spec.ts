import { expect, test, type Page } from '@playwright/test'
import type { components } from '../src/api/schema'
import { API, bearer, cashflowImportReady, ensureActiveMapping, registerInstrumentSource, signIn, uploadSample, validateAndProject } from './support'

/**
 * Feature 05 Phase 25 browser check against the real stack (backend ENV=test, synthetic).
 * FE-25.1: risk_analyst runs the FE-24.2 exploration scenario (DELAYED_INFLOWS on LOAN, 20%,
 * 14 days, plus DEPOSIT_WITHDRAWAL on DEPOSIT, +15%, confirmed for exploration) against a
 * baseline maturity_ladder run from the "Run against baseline" panel:
 *   1. the picker lists the baseline (a succeeded, non-stressed maturity_ladder run);
 *   2. the dry run shows both shocks matching at least one flow, the pins the runs reuse, and
 *      Run enabled;
 *   3. Run sends an Idempotency-Key; the server writes three runs (shock 1 alone, shock 2
 *      alone, combined) that each end in a success status, labelled "Exploration, not approved";
 *   4. the calculation run list shows the exploration badge on each, with its baseline and
 *      scenario links, and the run detail shows it too;
 *   5. a stressed run is never offered as a baseline.
 *
 * Every payload type is the generated one (src/api/schema.d.ts). Needs a backend with
 * SCENARIOS_ENABLED and CALCULATIONS_ENABLED on (their defaults), the seeded maturity_ladder v1
 * and ACTIVE cashflow_direction rule (LOAN an inflow, DEPOSIT an outflow), a frontend built with
 * NEXT_PUBLIC_STRESS_EXPLORER_UI and NEXT_PUBLIC_STRESS_COMPARE_UI on (their defaults) and a
 * disposable database (D-03).
 *
 * Ordering: this file sorts before smoke.spec, whose applied proposal moves the `instrument`
 * schema past v1, which projector v1 refuses. It registers its own sources and mappings and
 * creates its own scenario (the FE-24.2 spec approves its copy, so it is no longer an
 * exploration).
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

const versionCard = (page: Page) => page.locator('[data-testid="scenario-version"][data-version="1"]')

test('setup: a baseline maturity_ladder run and the FE-24.2 scenario, confirmed for exploration (API, not under test)', async ({ request }) => {
  const analyst = await bearer(request, 'risk_analyst')
  const steward = await bearer(request, 'data_steward')
  const { sourceId, contractId } = await registerInstrumentSource(request, steward)
  const instruments: Schemas['ImportDetail'] = await uploadSample(request, steward, sourceId, 'instrument.csv')
  expect(instruments.schema_version, 'projector v1 supports instrument v1 only: run before smoke.spec').toBe(1)
  await ensureActiveMapping(request, contractId, sourceId, instruments.id, `e2e_scn_instr_${Date.now()}`)
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
  const ran = await request.post(`${API}/api/v1/calculation-runs`, {
    headers: { ...analyst, 'Idempotency-Key': `e2e-scn-baseline-${Date.now()}` },
    data: runBody,
  })
  expect(ran.status(), await ran.text()).toBe(201)
  baseline = await ran.json()
  expect(SUCCESS).toContain(baseline!.status)
  expect(baseline!.scenario ?? null).toBeNull()

  // The FE-24.2 scenario's shocks, exactly; confirmed by its author for exploration only.
  const create: Schemas['ScenarioCreate'] = {
    name: `Two-week loan repayment delay (FE-25.1 e2e ${Date.now()})`,
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
  const version: Schemas['ScenarioVersionOut'] = await confirmed.json()
  expect(version.status).toBe('CONFIRMED_FOR_EXPLORATION')
  expect(version.approval_label).toBe(EXPLORATION)
})

test('FE-25.1: risk_analyst runs the exploration on the baseline; three runs succeed and the run list shows the exploration badge', async ({ page }) => {
  expect(baseline && scenario, 'the setup test ran').toBeTruthy()
  const scenarioId = scenario!.scenario_id
  await signIn(page, 'Risk analyst')
  await page.goto(`${EXPLORER}/${scenarioId}`)
  const card = versionCard(page)
  await expect(card).toHaveAttribute('data-status', 'CONFIRMED_FOR_EXPLORATION')

  // 1. The picker: the baseline is listed; no stressed run is.
  await card.getByTestId('run-against-baseline').click()
  const picker = card.getByTestId('baseline-picker')
  await expect(picker.locator(`option[value="${baseline!.run_id}"]`)).toHaveCount(1)

  // 2. The dry run and the pins that will be reused.
  const validated = page.waitForResponse(
    (r) => r.request().method() === 'GET' && r.url().startsWith(`${API}/api/v1/scenarios/${scenarioId}/versions/1/validation?`),
  )
  await picker.selectOption(baseline!.run_id)
  const validationResponse = await validated
  expect(validationResponse.status(), await validationResponse.text()).toBe(200)
  expect(new URL(validationResponse.url()).searchParams.get('baseline_run_id')).toBe(baseline!.run_id)
  const validation: Schemas['ScenarioValidationOut'] = await validationResponse.json()
  expect(validation.runnable, JSON.stringify(validation)).toBe(true)
  expect(validation.run_count).toBe(3)
  expect(validation.approval_label).toBe(EXPLORATION)
  expect(validation.shocks.map((s) => s.template)).toEqual(['DELAYED_INFLOWS', 'DEPOSIT_WITHDRAWAL'])
  for (const s of validation.shocks) expect(s.matched_flows, `shock ${s.shock_index} matches the synthetic flows`).toBeGreaterThan(0)
  const dryRun = card.getByTestId('run-validation')
  await expect(dryRun).toHaveAttribute('data-runnable', 'true')
  await expect(dryRun.locator('[data-testid="validation-shock"]')).toHaveCount(2)
  for (const s of validation.shocks)
    await expect(dryRun.locator(`[data-testid="validation-shock"][data-shock-index="${s.shock_index}"]`)).toHaveAttribute('data-matched', String(s.matched_flows))
  await expect(card.getByTestId('validation-zero-match')).toHaveCount(0)
  await expect(card.getByTestId('validation-approval-label')).toHaveText(EXPLORATION)
  const pins = card.getByTestId('baseline-pins')
  await expect(pins).toHaveAttribute('data-run-id', baseline!.run_id)
  await expect(pins.getByTestId('identity-reporting-date')).toHaveText(REPORTING_DATE)
  await expect(pins.locator('[data-testid="baseline-pin"]')).toHaveCount(baseline!.input_refs.length)

  // 3. Run, with an Idempotency-Key.
  const submit = card.getByTestId('scenario-run-submit')
  await expect(submit).toBeEnabled()
  const posted = page.waitForResponse((r) => r.request().method() === 'POST' && r.url() === `${API}/api/v1/scenarios/${scenarioId}/run`)
  await submit.click()
  const response = await posted
  expect(response.status(), await response.text()).toBe(201)
  expect(response.request().headers()['idempotency-key']).toMatch(/^scenario-run-/)
  expect(response.request().postDataJSON()).toEqual({ version: 1, baseline_run_id: baseline!.run_id })
  scenarioRun = await response.json()
  expect(scenarioRun!.approval_state).toBe('EXPLORATION')
  expect(scenarioRun!.approval_label).toBe(EXPLORATION)
  expect(scenarioRun!.baseline_run_id).toBe(baseline!.run_id)
  expect(scenarioRun!.members.map((m) => [m.shock_subset, m.combined])).toEqual([
    [[1], false],
    [[2], false],
    [[1, 2], true],
  ])
  for (const m of scenarioRun!.members) expect(SUCCESS, `member ${m.shock_subset.join('+')} ended ${m.status}`).toContain(m.status)

  const result = card.getByTestId('scenario-run-result')
  await expect(result).toHaveAttribute('data-scenario-run-id', scenarioRun!.scenario_run_id)
  await expect(result).toHaveAttribute('data-replayed', 'false')
  await expect(result.getByTestId('scenario-run-approval')).toHaveText(EXPLORATION)
  for (const m of scenarioRun!.members)
    await expect(result.locator(`[data-testid="scenario-run-member"][data-run-id="${m.calculation_run_id}"]`)).toHaveAttribute('data-status', m.status)

  // 4. The calculation run list labels each stressed run, with its baseline and scenario.
  await page.goto(`/calculations?module_code=${MODULE}`)
  for (const m of scenarioRun!.members) {
    const row = page.locator(`[data-testid="run-row"][data-run-id="${m.calculation_run_id}"]`)
    await expect(row).toHaveAttribute('data-status', m.status)
    const badge = row.getByTestId('stressed-run')
    await expect(badge).toHaveAttribute('data-approval-state', 'EXPLORATION')
    await expect(badge.getByTestId('stressed-run-approval')).toHaveText(EXPLORATION)
    await expect(badge.getByTestId('stressed-run-baseline')).toHaveAttribute('href', `/calculations/runs/${baseline!.run_id}`)
    await expect(badge.getByTestId('stressed-run-scenario')).toHaveAttribute('href', `${EXPLORER}/${scenarioId}`)
  }
  // The baseline itself is not stressed: no badge.
  await expect(page.locator(`[data-testid="run-row"][data-run-id="${baseline!.run_id}"]`).getByTestId('stressed-run')).toHaveCount(0)

  // ... and the run detail of the combined run.
  const combined = scenarioRun!.members.find((m) => m.combined)!
  await page.locator(`[data-testid="run-row"][data-run-id="${combined.calculation_run_id}"]`).getByTestId('run-link').click()
  await page.waitForURL(`**/calculations/runs/${combined.calculation_run_id}`)
  await expect(page.getByTestId('run-detail')).toHaveAttribute('data-status', combined.status)
  await expect(page.getByTestId('run-scenario').getByTestId('stressed-run-approval')).toHaveText(EXPLORATION)
  await expect(page.getByTestId('run-baseline')).toHaveText(baseline!.run_id)
  await expect(page.getByTestId('run-scenario-run')).toContainText(scenarioRun!.scenario_run_id)
})

test('FE-25.1: a stressed run is never offered as a baseline', async ({ page }) => {
  expect(scenarioRun, 'the run test ran').toBeTruthy()
  await signIn(page, 'Risk analyst')
  await page.goto(`${EXPLORER}/${scenario!.scenario_id}`)
  await versionCard(page).getByTestId('run-against-baseline').click()
  const picker = versionCard(page).getByTestId('baseline-picker')
  await expect(picker.locator(`option[value="${baseline!.run_id}"]`)).toHaveCount(1)
  for (const m of scenarioRun!.members) await expect(picker.locator(`option[value="${m.calculation_run_id}"]`)).toHaveCount(0)
})
