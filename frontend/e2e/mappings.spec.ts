import { join } from 'node:path'
import { expect, test, type Page } from '@playwright/test'

/**
 * Feature 02 Phase 10 browser checks against the real stack (backend ENV=test, synthetic).
 * FE-10.1: data_steward uploads the synthetic instrument CSV to the seeded core_banking
 *          source (it stops at MAPPING_REQUIRED), opens "Map this import", creates a mapping
 *          whose v1 preserves every column, and the dry run shows the missing-required
 *          BLOCKER; mapping the columns in the grid and saving v2 makes the dry run pass.
 * FE-10.2: a forced early submit (v1, whose dry run FAILED, with Submit enabled by a test-only
 *          response rewrite) shows the backend's 409 envelope; the author submits v2 and sees
 *          "You cannot approve your own mapping"; the approver approves and the ACTIVE badge shows.
 */

const API = 'http://localhost:8000'
const SAMPLE = join(__dirname, '../../backend/samples/synthetic/instrument.csv')

async function signIn(page: Page, persona: string) {
  await page.goto('/login')
  await page.getByRole('button', { name: new RegExp(persona) }).click()
  await page.waitForURL('**/dashboard')
}

test.describe.configure({ mode: 'serial' })

let mappingId = ''
let importId = ''

async function validate(page: Page) {
  const answered = page.waitForResponse((r) => r.request().method() === 'POST' && r.url() === `${API}/api/v1/imports/${importId}/mappings/validate`)
  await page.getByTestId('mapping-validate').click()
  const response = await answered
  expect(response.status()).toBe(201)
  return response.json()
}

test('FE-10.1: steward maps the synthetic CSV; the dry run shows the required-field BLOCKER until it is mapped, then passes', async ({ page }) => {
  await signIn(page, 'Data steward')

  // Upload the sample to the seeded core_banking source: with no mapping it stops at MAPPING_REQUIRED.
  await page.goto('/ingestion')
  await page.getByTestId('new-import').click()
  const select = page.locator('#import-source')
  const value = await select.locator('option', { hasText: '(core_banking)' }).getAttribute('value')
  await select.selectOption(value!)
  await page.getByTestId('import-continue').click()
  await page.getByTestId('import-file').setInputFiles(SAMPLE)
  await page.getByTestId('synthetic-attested').click()
  const uploaded = page.waitForResponse((r) => r.request().method() === 'POST' && r.url() === `${API}/api/v1/imports`)
  await page.getByTestId('import-upload').click()
  const job = await (await uploaded).json()
  importId = job.id
  expect(job.status).toBe('MAPPING_REQUIRED')
  await page.waitForURL(`**/ingestion/imports/${importId}**`)

  // "Map this import" → create the mapping; v1 preserves every column (no suggestions).
  await page.getByTestId('import-map-link').click()
  await page.waitForURL(`**/ingestion/mappings/new?import=${importId}`)
  const suggestions = page.getByTestId('mapping-use-suggestions')
  await expect(suggestions).toHaveAttribute('data-state', 'checked')
  await suggestions.click()
  await expect(suggestions).toHaveAttribute('data-state', 'unchecked')
  const created = page.waitForResponse((r) => r.request().method() === 'POST' && r.url() === `${API}/api/v1/mappings`)
  await page.getByTestId('mapping-create').click()
  const mapping = await (await created).json()
  expect((await created).status()).toBe(201)
  mappingId = mapping.id
  await page.waitForURL(`**/ingestion/mappings/${mappingId}?import=${importId}`)

  const badge = page.getByTestId('mapping-version-badge')
  await expect(badge).toHaveAttribute('data-version', '1')
  await expect(badge).toHaveAttribute('data-status', 'DRAFT')
  await expect(page.getByTestId('mapping-active-version')).toHaveText('No active version')
  await expect(page.getByTestId('mapping-grid-row')).toHaveCount(9)
  await expect(page.getByTestId('mapping-unmapped-required')).toContainText('notional')

  // Dry run of v1: the unmapped required field is a BLOCKER; Submit stays locked.
  const first = await validate(page)
  expect(first.outcome).toBe('FAILED')
  const result = page.getByTestId('mapping-validation')
  await expect(result).toHaveAttribute('data-outcome', 'FAILED')
  const blocker = page.locator('[data-testid="mapping-issue"][data-code="required_field_unmapped"][data-target="notional"]')
  await expect(blocker).toHaveAttribute('data-severity', 'BLOCKER')
  await expect(page.getByTestId('mapping-rule-result')).toHaveCount(9)
  await expect(page.getByTestId('mapping-submit')).toBeDisabled()

  // Map the columns in the grid: suggested targets, then a decimal parser for notional.
  await page.getByTestId('mapping-apply-suggestions').click()
  await page.getByLabel('Transform for notional').selectOption('PARSE_DECIMAL')
  await expect(page.getByTestId('mapping-dirty')).toBeVisible()
  await expect(page.getByTestId('mapping-validate')).toBeDisabled() // a dry run checks a saved version
  await expect(page.getByTestId('mapping-unmapped-required')).toHaveCount(0)
  const saved = page.waitForResponse((r) => r.request().method() === 'POST' && r.url() === `${API}/api/v1/mappings/${mappingId}/versions`)
  await page.getByTestId('mapping-save-draft').click()
  expect((await saved).status()).toBe(201)
  await expect(badge).toHaveAttribute('data-version', '2')
  await expect(page.getByTestId('mapping-dirty')).toHaveCount(0)

  // Dry run of v2 passes: no BLOCKER, ok, per-rule counts shown.
  const second = await validate(page)
  expect(second.ok).toBe(true)
  await expect(result).toHaveAttribute('data-ok', 'true')
  await expect(page.locator('[data-testid="mapping-issue"][data-severity="BLOCKER"]')).toHaveCount(0)
  await expect(page.getByTestId('mapping-rule-result')).toHaveCount(second.rule_results.length)
  await expect(page.getByTestId('mapping-submit')).toBeEnabled()
  if (process.env.MAPPING_SCREENSHOT) await page.screenshot({ path: `${process.env.MAPPING_SCREENSHOT}/workbench.png`, fullPage: true })
})

test('FE-10.2: forced early submit shows the 409 envelope; author submits, approver approves → ACTIVE', async ({ browser }) => {
  expect(mappingId).not.toBe('')
  const steward = await browser.newPage()
  await signIn(steward, 'Data steward')

  // Test-only bypass: rewrite the mapping read so v1 (whose dry run FAILED) looks ok and
  // Submit is enabled. The backend decides on its own record and refuses.
  const url = `${API}/api/v1/mappings/${mappingId}`
  await steward.route(url, async (route) => {
    const response = await route.fetch()
    const body = await response.json()
    for (const v of body.versions) if (v.version === 1 && v.last_validation) Object.assign(v.last_validation, { ok: true, outcome: 'OK' })
    await route.fulfill({ response, json: body })
  })
  await steward.goto(`/ingestion/mappings/${mappingId}?import=${importId}&version=1`)
  await expect(steward.getByTestId('mapping-version-badge')).toHaveAttribute('data-version', '1')
  const refused = steward.waitForResponse((r) => r.request().method() === 'POST' && r.url() === `${url}/versions/1/submit`)
  await steward.getByTestId('mapping-submit').click()
  const envelope = await (await refused).json()
  expect((await refused).status()).toBe(409)
  expect(envelope.code).toBe('mapping_validation_required')
  const notice = steward.getByTestId('mapping-refusal')
  await expect(notice).toHaveAttribute('data-code', 'mapping_validation_required')
  await expect(notice).toContainText('HTTP 409')
  await expect(notice).toContainText(envelope.message)
  await expect(steward.getByTestId('mapping-refusal-cid')).toHaveText(envelope.correlation_id)
  await steward.unroute(url)

  // The author submits v2 (its dry run passed) and cannot approve it.
  await steward.goto(`/ingestion/mappings/${mappingId}?import=${importId}&version=2`)
  await expect(steward.getByTestId('mapping-version-badge')).toHaveAttribute('data-version', '2')
  await steward.getByTestId('mapping-submit').click()
  await expect(steward.getByTestId('mapping-version-badge')).toHaveAttribute('data-status', 'IN_REVIEW')
  await expect(steward.getByTestId('mapping-awaiting-approver')).toContainText('You cannot approve your own mapping')
  await expect(steward.getByTestId('mapping-approve')).toHaveCount(0)

  // The approver gets the decision panel and activates v2.
  const approver = await browser.newPage()
  await signIn(approver, 'Approver')
  await approver.goto(`/ingestion/mappings/${mappingId}?version=2`)
  await expect(approver.getByTestId('mapping-decision-panel')).toBeVisible()
  await approver.getByTestId('mapping-approve').click()
  await expect(approver.getByTestId('mapping-active-version')).toHaveText('Active v2')
  await expect(approver.getByTestId('mapping-version-badge')).toHaveAttribute('data-status', 'ACTIVE')
  await expect(approver.locator('[data-testid="mapping-version-row"][data-version="2"]')).toHaveAttribute('data-status', 'ACTIVE')
  if (process.env.MAPPING_SCREENSHOT) await approver.screenshot({ path: `${process.env.MAPPING_SCREENSHOT}/approved.png`, fullPage: true })
})
