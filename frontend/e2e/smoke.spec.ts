import { expect, test, type Page, type Route } from '@playwright/test'

/** FE-6.2 smoke: sign-in per role, the 403 page, schema compare and the proposal flow. */

const PERSONAS = ['Platform administrator', 'Data steward', 'Risk analyst', 'Approver', 'Auditor']

async function signIn(page: Page, persona: string) {
  await page.goto('/login')
  await page.getByRole('button', { name: new RegExp(persona) }).click()
  await page.waitForURL('**/dashboard')
}

test.describe.configure({ mode: 'serial' })

for (const persona of PERSONAS) {
  test(`sign-in as ${persona}`, async ({ page }) => {
    await signIn(page, persona)
    await expect(page.getByText(`Dev ${persona}`)).toBeVisible()
    // FE-6.1: the environment banner is always visible outside production.
    await expect(page.getByTestId('environment-banner')).toHaveText(/· SYNTHETIC DATA$/)
  })
}

test('a page the role may not use shows the 403 state', async ({ page }) => {
  await signIn(page, 'Risk analyst')
  await page.goto('/access/users')
  await expect(page.getByText("You don't have access to this")).toBeVisible()
  await page.goto('/audit')
  await expect(page.getByText("You don't have access to this")).toBeVisible()
})

test('schema compare shows every change with its badge', async ({ page }) => {
  await signIn(page, 'Risk analyst')
  await page.goto('/schemas')
  await page.getByTestId('schema-row').filter({ hasText: 'instrument' }).getByRole('link').click()
  const changes = page.getByTestId('diff-change')
  await expect(changes).toHaveCount(5)
  const badges = await changes.evaluateAll((els) =>
    els.map((e) => `${e.getAttribute('data-field')}:${e.querySelector('[data-testid="compatibility-badge"]')?.getAttribute('data-compatibility')}`),
  )
  expect(badges.sort()).toEqual(
    [
      'interest_rate:COMPATIBLE',
      'legacy_book_code:BREAKING',
      'maturity_date:BREAKING',
      'product_family:BREAKING',
      'rating_category:ADDITIVE',
    ].sort(),
  )
  await expect(page.getByTestId('diff-overall').getByTestId('compatibility-badge')).toHaveAttribute('data-compatibility', 'BREAKING')
})

/**
 * FE-14.3: GET /schemas answers 500 once (the real response, status and body replaced); the
 * page shows "Try again" instead of loading forever, and one click renders the list.
 */
async function failSchemaListOnce(page: Page) {
  const forced = { count: 0 }
  await page.route('**/api/v1/schemas', async (route: Route) => {
    if (route.request().method() !== 'GET' || forced.count > 0) return route.fallback()
    forced.count += 1
    const response = await route.fetch()
    await route.fulfill({
      response,
      status: 500,
      json: { code: 'internal_error', message: 'Forced by the e2e test.', details: null, correlation_id: 'e2e-fe-14-3', retryable: false },
    })
  })
  return forced
}

test('FE-14.3: /schemas recovers from a failed load with one "Try again"', async ({ page }) => {
  await signIn(page, 'Risk analyst')
  const forced = await failSchemaListOnce(page)
  await page.goto('/schemas')
  const retry = page.getByTestId('load-retry')
  await expect(retry).toHaveText('Try again')
  await expect(page.getByText('The schemas could not be loaded')).toBeVisible()
  await expect(page.getByText('The request did not complete. Correlation ID: e2e-fe-14-3')).toBeVisible()
  await expect(page.getByTestId('schema-row')).toHaveCount(0)
  expect(forced.count).toBe(1)

  await retry.click()
  await expect(page.getByTestId('schema-row').filter({ hasText: 'instrument' })).toBeVisible()
  await expect(retry).toHaveCount(0)
  expect(forced.count).toBe(1)
})

test('FE-14.3: /schemas/proposals/new recovers from a failed load with one "Try again"', async ({ page }) => {
  await signIn(page, 'Data steward')
  const forced = await failSchemaListOnce(page)
  await page.goto('/schemas/proposals/new')
  const retry = page.getByTestId('load-retry')
  await expect(retry).toHaveText('Try again')
  await expect(page.getByText('The schemas could not be loaded')).toBeVisible()
  await expect(page.getByLabel('Schema', { exact: true })).toHaveCount(0)

  await retry.click()
  await expect(page.getByLabel('Schema', { exact: true }).locator('option', { hasText: '(instrument)' })).toHaveCount(1)
  await expect(page.getByTestId('field-row').first()).toBeVisible()
  await expect(retry).toHaveCount(0)
  expect(forced.count).toBe(1)
})

test('proposal flow: steward proposes, approver approves', async ({ browser }) => {
  const steward = await browser.newPage()
  await signIn(steward, 'Data steward')
  await steward.goto('/schemas')
  await steward.getByTestId('schema-row').filter({ hasText: 'instrument' }).getByRole('link').click()
  await steward.getByRole('link', { name: 'Propose change' }).click()
  await steward.getByTestId('field-row').first().waitFor()
  await steward.getByLabel('Title').fill('E2E: drop counterparty_id')
  await steward.getByRole('button', { name: 'Remove counterparty_id' }).click()
  await steward.getByRole('button', { name: 'Create proposal' }).click()
  await steward.waitForURL('**/schemas/proposals/**')

  await expect(steward.getByTestId('submit-proposal')).toBeDisabled()
  await steward.getByRole('button', { name: 'Run impact analysis' }).click()
  await expect(steward.getByTestId('impact-overall').getByTestId('compatibility-badge')).toHaveAttribute(
    'data-compatibility',
    'BREAKING',
  )
  await expect(steward.getByTestId('impact-dependent').first()).toBeVisible()
  await steward.getByTestId('submit-proposal').click()
  await expect(steward.getByTestId('awaiting-approver')).toContainText('You cannot approve your own proposal')
  const proposalUrl = steward.url()

  const approver = await browser.newPage()
  await signIn(approver, 'Approver')
  await approver.goto(proposalUrl)
  await approver.getByTestId('approve-proposal').click()
  await expect(approver.locator('[data-testid="proposal-status"][data-status="APPLIED"]')).toBeVisible()
})
