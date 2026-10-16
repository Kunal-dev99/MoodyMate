import { expect, test, type Page } from '@playwright/test'

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
