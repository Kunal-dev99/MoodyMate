import { expect, test, type Page } from '@playwright/test'

/**
 * Feature 02 Phase 7 browser checks against the real stack.
 * FE-7.1: the Ingestion Workbench is live; only data_steward sees "Register source"; the
 *         auditor gets the 403 state on the form and a 403 from POST /sources.
 * FE-7.2: register a source, see it listed, add contract v2, see v1 Superseded and v2
 *         Provisional, and see both contract events (with correlation IDs) in its timeline.
 */

const API = 'http://localhost:8000'

async function signIn(page: Page, persona: string) {
  await page.goto('/login')
  await page.getByRole('button', { name: new RegExp(persona) }).click()
  await page.waitForURL('**/dashboard')
}

/** The bearer token the app holds for this tab (sessionStorage, FE-2.1). */
async function tokenOf(page: Page): Promise<string> {
  const raw = await page.evaluate(() => sessionStorage.getItem('moodymate_session'))
  return JSON.parse(raw ?? '{}').state.token
}

test.describe.configure({ mode: 'serial' })

test('FE-7.1: data_steward sees the live workbench and the Register button', async ({ page }) => {
  await signIn(page, 'Data steward')
  await page.getByRole('link', { name: 'Ingestion Workbench' }).click()
  await page.waitForURL('**/ingestion')
  await expect(page.getByRole('heading', { name: 'Ingestion Workbench' })).toBeVisible()
  await expect(page.getByText('This workspace is planned, not built')).toHaveCount(0)
  await expect(page.getByTestId('register-source')).toBeVisible()
  // The seeded illustrative source (BE-7.5) is listed as provisional.
  await expect(page.getByTestId('source-row').filter({ hasText: 'core_banking' })).toContainText('Provisional')
  await page.getByRole('tab', { name: 'Imports' }).click()
  await expect(page.getByText('No imports yet')).toBeVisible()
})

test('FE-7.1: auditor has no Register button, gets the 403 state and a backend 403', async ({ page }) => {
  await signIn(page, 'Auditor')
  await page.goto('/ingestion')
  await expect(page.getByTestId('source-row').first()).toBeVisible()
  await expect(page.getByTestId('register-source')).toHaveCount(0)

  await page.goto('/ingestion/sources/new')
  await expect(page.getByText("You don't have access to this")).toBeVisible()
  await expect(page.getByText('source:manage')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Register source' })).toHaveCount(0)

  // The server decides (D-04): the same request the form would send is refused.
  const response = await page.request.post(`${API}/api/v1/sources`, {
    headers: { Authorization: `Bearer ${await tokenOf(page)}` },
    data: {
      code: 'auditor_attempt',
      name: 'Should not exist',
      kind: 'FILE_DROP',
      profile: { format: 'CSV', owner: 'auditor', default_classification: 'internal' },
      contract: {
        schema_id: '00000000-0000-4000-8000-000000000000',
        expected_columns: [{ name: 'id', required: true }],
        business_key_columns: ['id'],
      },
    },
  })
  expect(response.status()).toBe(403)
  expect((await response.json()).code).toBe('forbidden')
})

test('FE-7.2: register a source, add contract v2, see the versions and the timeline', async ({ browser }) => {
  const code = `e2e_loans_${Date.now()}`
  const steward = await browser.newPage()
  await signIn(steward, 'Data steward')
  await steward.goto('/ingestion')
  await steward.getByTestId('register-source').click()
  await steward.waitForURL('**/ingestion/sources/new')

  await steward.getByLabel('Code').fill(code)
  await steward.getByLabel('Name', { exact: true }).fill('E2E loan book')
  await steward.getByLabel('Cadence').fill('daily')
  const target = steward.locator('#target-schema')
  const instrument = await target.locator('option', { hasText: '(instrument)' }).getAttribute('value')
  await target.selectOption(instrument!)
  await steward.getByLabel('Column 1 name').fill('loan_id')
  await steward.getByLabel('Column 1 business key').check()
  await steward.getByRole('button', { name: 'Add column' }).click()
  await steward.getByLabel('Column 2 name').fill('balance')
  await steward.getByLabel('Column 2 control total').check()
  await expect(steward.getByTestId('draft-problems')).toHaveCount(0)
  await steward.getByRole('button', { name: 'Register source' }).click()
  await steward.waitForURL(/\/ingestion\/sources\/[0-9a-f-]{36}$/)
  const detailUrl = steward.url()
  await expect(steward.getByTestId('contract-row')).toHaveCount(1)
  await expect(steward.locator('[data-testid="contract-row"][data-version="1"]').getByTestId('contract-status')).toHaveText('Provisional')

  // It appears in the list.
  await steward.goto('/ingestion')
  const row = steward.locator(`[data-testid="source-row"][data-code="${code}"]`)
  await expect(row).toContainText('E2E loan book')
  await expect(row).toContainText('v1')
  await row.getByRole('link').click()
  await steward.waitForURL(detailUrl)

  // Contract v2: one more expected column.
  await steward.getByTestId('add-contract-version').click()
  await expect(steward.getByLabel('Column 2 name')).toHaveValue('balance')
  await steward.getByRole('button', { name: 'Add column' }).click()
  await steward.getByLabel('Column 3 name').fill('currency')
  await steward.getByLabel('Change summary').fill('Files now carry a currency column')
  await steward.getByTestId('create-contract-version').click()

  const v1 = steward.locator('[data-testid="contract-row"][data-version="1"]')
  const v2 = steward.locator('[data-testid="contract-row"][data-version="2"]')
  await expect(v1.getByTestId('contract-status')).toHaveText('Superseded')
  await expect(v1).toHaveAttribute('data-status', 'SUPERSEDED')
  await expect(v2.getByTestId('contract-status')).toHaveText('Provisional')
  await expect(v2).toHaveAttribute('data-status', 'PROVISIONAL')
  // The steward has no audit:read: the timeline says so instead of failing.
  await expect(steward.getByTestId('audit-unavailable')).toBeVisible()

  // A role with source:read and audit:read sees the resource-scoped timeline.
  const auditor = await browser.newPage()
  await signIn(auditor, 'Auditor')
  await auditor.goto(detailUrl)
  await expect(auditor.locator('[data-testid="contract-row"][data-version="1"]')).toHaveAttribute('data-status', 'SUPERSEDED')
  await expect(auditor.locator('[data-testid="contract-row"][data-version="2"]')).toHaveAttribute('data-status', 'PROVISIONAL')
  const events = auditor.getByTestId('audit-event')
  await expect(events).toHaveCount(3)
  for (const action of ['source.create', 'source_contract.create', 'source_contract.supersede']) {
    const event = events.filter({ hasText: action })
    await expect(event).toHaveCount(1)
    await expect(event).toContainText('SUCCESS')
    await expect(event.getByTestId('correlation-id')).toHaveText(/^[0-9a-f-]{8,}/i)
  }
})
