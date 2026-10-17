import { join } from 'node:path'
import { expect, test } from '@playwright/test'
import { API, SAMPLES, bearer, coreBanking, ensureActiveMapping, signIn, uploadSample } from './support'

/**
 * Feature 02 Phase 11 browser checks against the real stack (backend ENV=test, synthetic).
 * FE-11.1: data_steward uploads the mixed-errors fixture (instrument__mixed_errors.csv),
 *          validates it from the Validation tab; the counts and issue codes on the page equal
 *          the API's, and accepted + quarantined + rejected = the row count.
 * FE-11.2: the steward moves a held item OPEN → UNDER_REVIEW → RETRY_READY, uploads the
 *          corrected file with "Retry with corrected file", validates the retry and the item
 *          is RESOLVED by it. risk_analyst sees no Quarantine tab and the API refuses (403).
 */

test.describe.configure({ mode: 'serial' })

let importId = ''
let batchId = ''

test('FE-11.1: validating the mixed-errors fixture shows the API counts and issue codes; the counts add up', async ({ page, request }) => {
  // Setup through the API: an ACTIVE mapping for the core_banking contract (needs a dry run on an import).
  const steward = await bearer(request, 'data_steward')
  const { sourceId, contractId } = await coreBanking(request, steward)
  const clean = await uploadSample(request, steward, sourceId, 'instrument.csv')
  await ensureActiveMapping(request, contractId, sourceId, clean.id)

  // The mixed-errors fixture through the wizard.
  await signIn(page, 'Data steward')
  await page.goto(`/ingestion/imports/new?source=${sourceId}`)
  await page.getByTestId('import-continue').click()
  await page.getByTestId('import-file').setInputFiles(join(SAMPLES, 'instrument__mixed_errors.csv'))
  await page.getByTestId('synthetic-attested').click()
  const uploaded = page.waitForResponse((r) => r.request().method() === 'POST' && r.url() === `${API}/api/v1/imports`)
  await page.getByTestId('import-upload').click()
  importId = (await (await uploaded).json()).id
  await page.waitForURL(`**/ingestion/imports/${importId}**`)

  await page.getByTestId('import-tab-validation').click()
  await expect(page.getByTestId('validation-not-run')).toBeVisible()
  const validated = page.waitForResponse((r) => r.request().method() === 'POST' && r.url() === `${API}/api/v1/imports/${importId}/validate`)
  await page.getByTestId('import-validate').click()
  const response = await validated
  expect(response.status()).toBe(200)
  const job = await response.json()
  expect(job.status).toBe('QUARANTINED')
  const q = job.manifest.quality
  batchId = job.batches[0].id

  // Counts equal the API's and add up to the row count.
  await expect(page.getByTestId('count-accepted')).toHaveText(String(q.accepted_records))
  await expect(page.getByTestId('count-quarantined')).toHaveText(String(q.quarantined_records))
  await expect(page.getByTestId('count-rejected')).toHaveText(String(q.rejected_records))
  await expect(page.getByTestId('count-warnings')).toHaveText(String(q.warnings))
  expect(q.accepted_records + q.quarantined_records + q.rejected_records).toBe(job.row_count)
  await expect(page.getByTestId('count-sum')).toHaveAttribute('data-adds-up', 'true')
  await expect(page.getByTestId('count-sum')).toContainText(`= ${job.row_count} rows`)
  await expect(page.getByTestId('validation-row-count')).toHaveText(String(job.row_count))
  await expect(page.getByTestId('manifest-id')).toHaveText(job.manifest.manifest_id)
  await expect(page.getByTestId('manifest-mapping')).toContainText(job.manifest.mapping_version_id)
  await expect(page.getByTestId('import-validate')).toHaveCount(0) // once only (D-21)

  // Issue codes equal the API's (row, code) list.
  const api = await request.get(`${API}/api/v1/imports/${importId}/issues?limit=500`, { headers: steward })
  const issues = await api.json()
  const expected = issues.items.map((i: { row: number | null; code: string }) => `${i.row ?? ''}:${i.code}`)
  expect(expected.length).toBeGreaterThan(0)
  expect(issues.total).toBeLessThanOrEqual(50) // one page in the UI
  const rows = page.getByTestId('issue-row')
  await expect(rows).toHaveCount(issues.total)
  const shown = await rows.evaluateAll((els) => els.map((e) => `${e.getAttribute('data-row')}:${e.getAttribute('data-code')}`))
  expect(shown).toEqual(expected)
  for (const s of ['INFO', 'WARNING', 'ERROR', 'BLOCKER'])
    await expect(page.locator(`[data-testid="issue-severity-counts"] [data-severity="${s}"][data-count]`)).toHaveAttribute('data-count', String(issues.severity_counts[s] ?? 0))

  // A server-side code filter, and the field-level detail.
  const code = issues.items[0].code
  const filtered = page.waitForResponse((r) => r.url().startsWith(`${API}/api/v1/imports/${importId}/issues`) && r.url().includes(`code=${code}`))
  await page.getByTestId('issues-code').selectOption(code)
  const page2 = await (await filtered).json()
  await expect(rows).toHaveCount(page2.total)
  await rows.first().getByTestId('issue-expand').click()
  await expect(page.getByTestId('issue-detail')).toContainText(page2.items[0].id)
  if (process.env.VALIDATION_SCREENSHOT) await page.screenshot({ path: `${process.env.VALIDATION_SCREENSHOT}/validation.png`, fullPage: true })
})

test('FE-11.2: steward moves an item OPEN → UNDER_REVIEW → RETRY_READY → RESOLVED via a retry upload; risk_analyst sees no Quarantine tab and gets 403', async ({ browser, request }) => {
  expect(importId).not.toBe('')
  const page = await browser.newPage()
  await signIn(page, 'Data steward')
  await page.goto(`/ingestion/imports/${importId}?tab=quarantine`)
  const item = page.locator('[data-testid="quarantine-item"][data-status="OPEN"]').first()
  await expect(item).toBeVisible()
  const itemId = (await item.getAttribute('data-id'))!
  const row = page.locator(`[data-testid="quarantine-item"][data-id="${itemId}"]`)
  // Only the D-20 manual move is offered.
  await expect(row.getByTestId('quarantine-move')).toHaveCount(1)
  await row.locator('[data-testid="quarantine-move"][data-to="UNDER_REVIEW"]').click()
  await expect(row).toHaveAttribute('data-status', 'UNDER_REVIEW')
  await row.locator('[data-testid="quarantine-move"][data-to="RETRY_READY"]').click()
  await expect(row).toHaveAttribute('data-status', 'RETRY_READY')
  await expect(row.getByTestId('quarantine-move')).toHaveCount(0) // CORRECTED/RESOLVED come from a retry only
  if (process.env.VALIDATION_SCREENSHOT) await page.screenshot({ path: `${process.env.VALIDATION_SCREENSHOT}/quarantine.png`, fullPage: true })

  // "Retry with corrected file" → the wizard, pre-filled; upload the corrected (clean) file.
  await page.getByTestId('quarantine-retry').click()
  await page.waitForURL(`**/ingestion/imports/new?**retry_of=${importId}**`)
  await expect(page.getByTestId('import-retry-of')).toHaveAttribute('data-import-id', importId)
  await page.getByTestId('import-file').setInputFiles(join(SAMPLES, 'instrument.csv'))
  await page.getByTestId('synthetic-attested').click()
  const uploaded = page.waitForResponse((r) => r.request().method() === 'POST' && r.url() === `${API}/api/v1/imports`)
  await page.getByTestId('import-upload').click()
  const retry = await (await uploaded).json()
  expect(retry.retry_of_import_id).toBe(importId)
  await page.waitForURL(`**/ingestion/imports/${retry.id}**`)

  await page.getByTestId('import-tab-validation').click()
  const validated = page.waitForResponse((r) => r.request().method() === 'POST' && r.url() === `${API}/api/v1/imports/${retry.id}/validate`)
  await page.getByTestId('import-validate').click()
  expect((await validated).status()).toBe(200)
  await expect(page.getByTestId('count-sum')).toHaveAttribute('data-adds-up', 'true')

  // Back on the original: the item is RESOLVED by the retry import.
  await page.goto(`/ingestion/imports/${importId}?tab=quarantine`)
  await expect(row).toHaveAttribute('data-status', 'RESOLVED')
  await expect(row.getByTestId('quarantine-resolved-by')).toContainText(retry.id.slice(0, 8))
  await expect(row.getByTestId('quarantine-move')).toHaveCount(0)

  // risk_analyst: no Quarantine tab (not even via ?tab=), and the backend refuses directly.
  const analyst = await browser.newPage()
  await signIn(analyst, 'Risk analyst')
  await analyst.goto(`/ingestion/imports/${importId}?tab=quarantine`)
  await expect(analyst.getByTestId('import-tab-validation')).toBeVisible()
  await expect(analyst.getByTestId('import-tab-quarantine')).toHaveCount(0)
  await expect(analyst.getByTestId('quarantine-tab')).toHaveCount(0)
  const headers = await bearer(request, 'risk_analyst')
  const read = await request.get(`${API}/api/v1/quarantine/${batchId}`, { headers })
  expect(read.status()).toBe(403)
  const move = await request.post(`${API}/api/v1/quarantine/${batchId}/items/${itemId}/disposition`, { headers, data: { status: 'UNDER_REVIEW' } })
  expect(move.status()).toBe(403)
})
