import { expect, test, type Page } from '@playwright/test'
import type { components } from '../src/api/schema'
import { projectedSyntheticImports, settledRun } from './calc-support'
import { API, bearer, signIn } from './support'

/**
 * FE-33.1 browser check against the real stack (backend ENV=test, synthetic data): "Submit run"
 * sends `Prefer: respond-async`, the server answers 202 with the run RUNNING (D-59 §2), and the
 * New-run panel follows the run by polling GET /calculation-runs/{run_id} until the server
 * reports a terminal status; only then does it open the run page, in that status.
 *
 * The first poll's response is held back for a moment (the request still reaches the real
 * backend and its answer is passed through unchanged), so the RUNNING state and the polling
 * notice are on screen long enough to be seen; nothing is mocked.
 *
 * Every payload type is the generated one (src/api/schema.d.ts); none is written here. Ordering:
 * this file sorts before smoke.spec (projector v1 needs instrument schema v1) and registers its
 * own sources and mappings. Run it on a fresh database (CI does; locally a disposable copy, D-03).
 */

type Schemas = components['schemas']

const REPORTING_DATE = '2026-12-31'
const MODULE = 'maturity_ladder'
const SUCCEEDED: Schemas['CalculationRunStatus'][] = ['SUCCEEDED', 'SUCCEEDED_WITH_WARNINGS']

const isPost = (page: Page, url: string) => page.waitForResponse((r) => r.request().method() === 'POST' && r.url() === url)

test('FE-33.1: a background run is accepted RUNNING and the browser polls it until the server says it SUCCEEDED', async ({ page, request }) => {
  test.setTimeout(180_000)
  // Setup (not under test): projected synthetic instrument and cash-flow imports.
  const imports = await projectedSyntheticImports(request, 'bgrun')
  const analystApi = await bearer(request, 'risk_analyst')
  const modules: Schemas['ModulePage'] = await (await request.get(`${API}/api/v1/calculation-modules`, { headers: analystApi })).json()
  const ladder = modules.items.find((m) => m.code === MODULE && m.implementation_status === 'AVAILABLE')
  expect(ladder?.runnable, 'maturity_ladder v1 is seeded AVAILABLE with every rule ACTIVE').toBe(true)

  await signIn(page, 'Risk analyst')
  await page.goto('/calculations/new')
  for (const code of ['instrument', 'cashflow_event'] as const) {
    const pick = page.getByTestId(`snapshot-pick-${code}`)
    await expect(pick.locator(`option[value="${imports[code]}"]`)).toHaveCount(1)
    await pick.selectOption(imports[code])
  }
  const built = isPost(page, `${API}/api/v1/dataset-snapshots`)
  await page.getByTestId('snapshot-build').click()
  const snapshotResponse = await built
  expect([200, 201], await snapshotResponse.text()).toContain(snapshotResponse.status())
  await page.locator(`[data-testid="module-option"][data-module-version-id="${ladder!.module_version_id}"]`).getByTestId('module-radio').check()
  await page.getByTestId('reporting-date').fill(REPORTING_DATE)
  await expect(page.getByTestId('pins-preview').locator('[data-testid="pin-row"][data-missing="true"]')).toHaveCount(0)

  // Count the polls of the run, and hold the first one back by 2 s (passed through unchanged).
  const reads: string[] = []
  let held = false
  await page.route(`${API}/api/v1/calculation-runs/*`, async (route) => {
    const url = new URL(route.request().url())
    if (route.request().method() !== 'GET' || url.pathname.split('/').length !== 5) return route.fallback()
    reads.push(url.pathname)
    if (!held) {
      held = true
      await new Promise((resolve) => setTimeout(resolve, 2_000))
    }
    return route.fallback()
  })

  // Submit: 202, RUNNING, Preference-Applied; the panel shows the server's RUNNING and polls.
  const submitted = isPost(page, `${API}/api/v1/calculation-runs`)
  await page.getByTestId('run-submit').click()
  const response = await submitted
  expect(response.status(), await response.text()).toBe(202)
  expect(response.request().headers()['prefer']).toBe('respond-async')
  expect(response.headers()['preference-applied']).toBe('respond-async')
  const accepted: Schemas['RunDetail'] = await response.json()
  expect(accepted.status).toBe('RUNNING')
  expect(accepted.outcome, 'no outcome before the run ends').toBeNull()
  const ended = settledRun(page, response)

  const line = page.getByTestId('run-submitted')
  await expect(line).toHaveAttribute('data-run-id', accepted.run_id)
  await expect(line.getByTestId('run-status')).toHaveAttribute('data-status', 'RUNNING')
  await expect(page.getByTestId('new-run-polling')).toHaveAttribute('data-phase', 'waiting')
  await expect(page.getByTestId('new-run-polling')).toContainText('Checking again in')

  // The server ends the run; the browser learns it only by polling, then opens the run page.
  const run = await ended
  expect(SUCCEEDED).toContain(run.status)
  expect(run.run_id).toBe(accepted.run_id)
  expect(reads.filter((p) => p.endsWith(`/${accepted.run_id}`)).length, 'at least one poll after the 202').toBeGreaterThanOrEqual(1)
  await page.waitForURL(`**/calculations/runs/${accepted.run_id}`)
  await expect(page.getByTestId('run-detail-status')).toHaveAttribute('data-status', run.status)
  await expect(page.getByTestId('run-id')).toHaveText(accepted.run_id)
  await expect(page.getByTestId('run-reporting-date')).toHaveText(REPORTING_DATE)
  // A terminal run is not polled: the notice is gone.
  await expect(page.getByTestId('run-polling')).toHaveCount(0)
  await page.unrouteAll({ behavior: 'ignoreErrors' })

  // The run list shows it in the server's status, with "Showing n of total" (FE-33.1 list helper).
  await page.goto('/calculations')
  await expect(page.locator(`[data-testid="run-row"][data-run-id="${accepted.run_id}"]`)).toHaveAttribute('data-status', run.status)
  await expect(page.getByTestId('run-pager-summary')).toContainText(/^Showing \d+ of \d+/)
})
