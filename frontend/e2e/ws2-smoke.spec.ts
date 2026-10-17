import { expect, test, type Page } from '@playwright/test'
import { API, bearer, shownFigure, signIn, uploadInBrowser, validateInBrowser, type ReconItem } from './support'

/**
 * Feature 02 Phase 13, FE-13.1: the WS2 smoke, one serial journey against the real stack
 * (backend ENV=test, synthetic samples only):
 *   1. data_steward registers a new source (its contract mirrors instrument.csv) and uploads;
 *   2. the import's profile is shown;
 *   3. the steward maps the import and the dry run passes, then submits;
 *   4. the approver activates the mapping;
 *   5. the steward validates the clean import (it becomes the source's drift baseline);
 *   6. the quarantine fixture is validated: an item is moved through the manual disposition
 *      states and the Reconciliation tab shows the API's figures;
 *   7. the extra-column upload raises a COLUMN_ADDED drift alert, which the steward reviews.
 *
 * The journey registers its own source, so it depends on no other spec's data: the drift
 * baseline is this source's clean import, and the figures are read from the API, never
 * hard-coded. Setup is in the browser throughout; the API is only read to compare.
 */

test.describe.configure({ mode: 'serial' })

/** The seeded core_banking contract v1 (migration 0011), entered through the form. */
const COLUMNS: { name: string; required: boolean; key?: boolean; total?: boolean }[] = [
  { name: 'instrument_id', required: true, key: true },
  { name: 'instrument_type', required: true },
  { name: 'currency', required: true },
  { name: 'counterparty_id', required: false },
  { name: 'product_family', required: false },
  { name: 'notional', required: true, total: true },
  { name: 'interest_rate', required: false },
  { name: 'maturity_date', required: false },
  { name: 'book_code', required: false },
]

const COMPLETED = ['COMPLETED', 'COMPLETED_WITH_WARNINGS']

/** Open the Reconciliation tab of the open import; returns the run the page loaded. */
async function openReconciliation(page: Page, importId: string) {
  const loaded = page.waitForResponse((r) => r.request().method() === 'GET' && r.url() === `${API}/api/v1/imports/${importId}/reconciliation`)
  await page.getByTestId('import-tab-reconciliation').click()
  const response = await loaded
  expect(response.status()).toBe(200)
  return response.json()
}

/** Every reconciliation figure on the page equals the API's string (or "masked"). */
async function expectReconciliationShown(page: Page, run: { reconciliation_id: string; outcome: string; items: ReconItem[] }) {
  await expect(page.getByTestId('recon-id')).toHaveText(run.reconciliation_id)
  await expect(page.getByTestId('recon-summary').getByTestId('recon-outcome')).toHaveAttribute('data-outcome', run.outcome)
  await expect(page.getByTestId('recon-item')).toHaveCount(run.items.length)
  for (const item of run.items) {
    const row = page.locator(`[data-testid="recon-item"][data-sequence="${item.sequence}"]`)
    await expect(row).toHaveAttribute('data-check', item.check)
    await expect(row).toHaveAttribute('data-status', item.status)
    for (const key of ['source', 'target', 'held', 'variance', 'tolerance'] as const) await expect(row.getByTestId(`recon-${key}`)).toHaveText(shownFigure(item[key]))
  }
}

test('FE-13.1: WS2 smoke: register → upload → profile → map → approve → validate → quarantine and reconcile → drift review', async ({ browser, request }) => {
  test.setTimeout(240_000)
  const steward = await browser.newPage()
  await signIn(steward, 'Data steward')
  const stewardApi = await bearer(request, 'data_steward')
  const me = await (await request.get(`${API}/api/v1/me`, { headers: stewardApi })).json()

  const code = `e2e_ws2_${Date.now()}`
  let sourceId = ''
  let cleanId = ''
  let mappingId = ''

  await test.step('1. the steward registers a source and uploads the clean sample', async () => {
    await steward.goto('/ingestion')
    await steward.getByTestId('register-source').click()
    await steward.waitForURL('**/ingestion/sources/new')
    await steward.getByLabel('Code').fill(code)
    await steward.getByLabel('Name', { exact: true }).fill('WS2 smoke instruments (e2e)')
    await steward.getByLabel('Cadence').fill('daily')
    const target = steward.locator('#target-schema')
    await target.selectOption((await target.locator('option', { hasText: '(instrument)' }).getAttribute('value'))!)
    for (const [i, c] of COLUMNS.entries()) {
      if (i > 0) await steward.getByRole('button', { name: 'Add column' }).click()
      await steward.getByLabel(`Column ${i + 1} name`).fill(c.name)
      await steward.getByLabel(`Column ${i + 1} required`).setChecked(c.required)
      if (c.key) await steward.getByLabel(`Column ${i + 1} business key`).check()
      if (c.total) await steward.getByLabel(`Column ${i + 1} control total`).check()
    }
    await expect(steward.getByTestId('draft-problems')).toHaveCount(0)
    const created = steward.waitForResponse((r) => r.request().method() === 'POST' && r.url() === `${API}/api/v1/sources`)
    await steward.getByRole('button', { name: 'Register source' }).click()
    const response = await created
    expect(response.status(), await response.text()).toBe(201)
    sourceId = (await response.json()).id
    await steward.waitForURL(`**/ingestion/sources/${sourceId}`)
    await expect(steward.locator('[data-testid="contract-row"][data-version="1"]')).toHaveAttribute('data-status', 'PROVISIONAL')

    // The source page's "New import" opens the wizard for this source.
    await steward.getByTestId('source-new-import').click()
    await steward.waitForURL(`**/ingestion/imports/new?source=${sourceId}`)
    const job = await uploadInBrowser(steward, sourceId, 'instrument.csv')
    cleanId = job.id
    // No mapping yet: the import stops at MAPPING_REQUIRED, and the page says so.
    expect(job.status).toBe('MAPPING_REQUIRED')
    await expect(steward.getByTestId('import-summary').getByTestId('import-status')).toHaveAttribute('data-status', 'MAPPING_REQUIRED')
    await expect(steward.getByTestId('import-row-count')).toHaveText(String(job.row_count))
    await expect(steward.getByTestId('import-id')).toHaveText(cleanId)
  })

  await test.step('2. the profile is shown', async () => {
    const loaded = steward.waitForResponse((r) => r.request().method() === 'GET' && r.url() === `${API}/api/v1/imports/${cleanId}/profile`)
    await steward.getByTestId('import-tab-profile').click()
    const profile = await (await loaded).json()
    expect(profile.import_id).toBe(cleanId)
    expect(profile.columns.map((c: { name: string }) => c.name)).toEqual(COLUMNS.map((c) => c.name))
    await expect(steward.getByTestId('profile-column')).toHaveCount(profile.columns.length)
    for (const column of profile.columns) {
      const row = steward.locator(`[data-testid="profile-column"][data-column="${column.name}"]`)
      await expect(row.getByTestId('profile-suggested-type')).toHaveText(column.suggested_type ? `Suggested: ${column.suggested_type}` : 'No type suggested')
    }
    // The clean sample matches the contract: no column is flagged.
    await expect(steward.getByTestId('profile-not-in-contract')).toHaveCount(0)
  })

  await test.step('3. the steward maps the import, the dry run passes, and submits', async () => {
    await steward.getByTestId('import-map-link').click()
    await steward.waitForURL(`**/ingestion/mappings/new?import=${cleanId}`)
    await expect(steward.getByTestId('mapping-use-suggestions')).toHaveAttribute('data-state', 'checked')
    const created = steward.waitForResponse((r) => r.request().method() === 'POST' && r.url() === `${API}/api/v1/mappings`)
    await steward.getByTestId('mapping-create').click()
    const response = await created
    expect(response.status(), await response.text()).toBe(201)
    mappingId = (await response.json()).id
    await steward.waitForURL(`**/ingestion/mappings/${mappingId}?import=${cleanId}`)
    const badge = steward.getByTestId('mapping-version-badge')
    await expect(badge).toHaveAttribute('data-status', 'DRAFT')
    await expect(steward.getByTestId('mapping-grid-row')).toHaveCount(COLUMNS.length)

    // The suggestions copy names only: notional needs a decimal parser. Save it as v2.
    await steward.getByLabel('Transform for notional').selectOption('PARSE_DECIMAL')
    await expect(steward.getByTestId('mapping-unmapped-required')).toHaveCount(0)
    const saved = steward.waitForResponse((r) => r.request().method() === 'POST' && r.url() === `${API}/api/v1/mappings/${mappingId}/versions`)
    await steward.getByTestId('mapping-save-draft').click()
    expect((await saved).status()).toBe(201)
    await expect(badge).toHaveAttribute('data-version', '2')

    // The dry run against the import passes: no BLOCKER, Submit unlocks.
    const validated = steward.waitForResponse((r) => r.request().method() === 'POST' && r.url() === `${API}/api/v1/imports/${cleanId}/mappings/validate`)
    await steward.getByTestId('mapping-validate').click()
    const dry = await validated
    expect(dry.status()).toBe(201)
    const result = await dry.json()
    expect(result.ok).toBe(true)
    await expect(steward.getByTestId('mapping-validation')).toHaveAttribute('data-ok', 'true')
    await expect(steward.locator('[data-testid="mapping-issue"][data-severity="BLOCKER"]')).toHaveCount(0)
    await expect(steward.getByTestId('mapping-rule-result')).toHaveCount(result.rule_results.length)

    await steward.getByTestId('mapping-submit').click()
    await expect(badge).toHaveAttribute('data-status', 'IN_REVIEW')
    await expect(steward.getByTestId('mapping-awaiting-approver')).toContainText('You cannot approve your own mapping')
    await expect(steward.getByTestId('mapping-approve')).toHaveCount(0)
  })

  await test.step('4. the approver activates the mapping', async () => {
    const approver = await browser.newPage()
    await signIn(approver, 'Approver')
    await approver.goto(`/ingestion/mappings/${mappingId}?version=2`)
    await expect(approver.getByTestId('mapping-decision-panel')).toBeVisible()
    const decided = approver.waitForResponse((r) => r.request().method() === 'POST' && r.url() === `${API}/api/v1/mappings/${mappingId}/versions/2/approval`)
    await approver.getByTestId('mapping-approve').click()
    const response = await decided
    expect(response.status(), await response.text()).toBe(200)
    expect((await response.json()).status).toBe('ACTIVE')
    await expect(approver.getByTestId('mapping-active-version')).toHaveText('Active v2')
    await expect(approver.getByTestId('mapping-version-badge')).toHaveAttribute('data-status', 'ACTIVE')
    await approver.close()
  })

  await test.step('5. the steward validates the clean import', async () => {
    await steward.goto(`/ingestion/imports/${cleanId}`)
    const job = await validateInBrowser(steward, cleanId)
    expect(COMPLETED).toContain(job.status)
    const q = job.manifest.quality
    expect(q.accepted_records + q.quarantined_records + q.rejected_records).toBe(job.row_count)
    await expect(steward.getByTestId('count-accepted')).toHaveText(String(q.accepted_records))
    await expect(steward.getByTestId('count-quarantined')).toHaveText(String(q.quarantined_records))
    await expect(steward.getByTestId('count-rejected')).toHaveText(String(q.rejected_records))
    await expect(steward.getByTestId('count-sum')).toHaveAttribute('data-adds-up', 'true')
    await expect(steward.getByTestId('manifest-id')).toHaveText(job.manifest.manifest_id)
    await expect(steward.getByTestId('manifest-mapping')).toContainText(job.manifest.mapping_version_id)
    await expect(steward.getByTestId('validation-manifest').getByTestId('import-status')).toHaveAttribute('data-status', job.status)
    await expect(steward.getByTestId('import-validate')).toHaveCount(0)

    // Validation reconciled the clean import in the same transaction: the page shows the API's run.
    const run = await openReconciliation(steward, cleanId)
    expect(run.import_id).toBe(cleanId)
    await expectReconciliationShown(steward, run)
  })

  await test.step('6. the quarantine fixture: disposition of a held item, then reconciliation', async () => {
    const mixed = await uploadInBrowser(steward, sourceId, 'instrument__mixed_errors.csv')
    const job = await validateInBrowser(steward, mixed.id)
    expect(job.status).toBe('QUARANTINED')
    const q = job.manifest.quality
    expect(q.quarantined_records).toBeGreaterThan(0)
    expect(q.accepted_records + q.quarantined_records + q.rejected_records).toBe(job.row_count)
    await expect(steward.getByTestId('count-quarantined')).toHaveText(String(q.quarantined_records))
    await expect(steward.getByTestId('count-sum')).toHaveAttribute('data-adds-up', 'true')

    // Quarantine: the manual D-20 moves, OPEN → UNDER_REVIEW → RETRY_READY.
    await steward.getByTestId('import-tab-quarantine').click()
    const item = steward.locator('[data-testid="quarantine-item"][data-status="OPEN"]').first()
    await expect(item).toBeVisible()
    const itemId = (await item.getAttribute('data-id'))!
    const row = steward.locator(`[data-testid="quarantine-item"][data-id="${itemId}"]`)
    await row.locator('[data-testid="quarantine-move"][data-to="UNDER_REVIEW"]').click()
    await expect(row).toHaveAttribute('data-status', 'UNDER_REVIEW')
    await row.locator('[data-testid="quarantine-move"][data-to="RETRY_READY"]').click()
    await expect(row).toHaveAttribute('data-status', 'RETRY_READY')
    await expect(row.getByTestId('quarantine-move')).toHaveCount(0)
    // The server holds the same state.
    const batch = await (await request.get(`${API}/api/v1/quarantine/${job.batches[0].id}?limit=200`, { headers: stewardApi })).json()
    expect(batch.items.find((i: { id: string }) => i.id === itemId)?.status).toBe('RETRY_READY')

    // Reconciliation: held rows mean REVIEW_REQUIRED; every figure is the API's.
    const run = await openReconciliation(steward, mixed.id)
    expect(run.outcome).toBe('REVIEW_REQUIRED')
    await expect(steward.getByTestId('recon-summary').getByTestId('recon-outcome')).toHaveText('Review required')
    await expectReconciliationShown(steward, run)
    const rows = (run.items as ReconItem[]).find((i) => i.check === 'ROW_COUNT')!
    expect(rows).toMatchObject({ source: String(job.row_count), status: 'VARIANCE' })
    const notional = (run.items as ReconItem[]).find((i) => i.check === 'CONTROL_TOTAL' && i.column === 'notional')!
    expect(notional.status).toBe('VARIANCE')
    expect(notional.variance).toMatch(/^-?\d+(\.\d+)?$/)
  })

  await test.step('7. the extra-column upload raises a drift alert, which the steward reviews', async () => {
    const wider = await uploadInBrowser(steward, sourceId, 'instrument__unknown_extra_column.csv')
    const loaded = steward.waitForResponse((r) => r.request().method() === 'GET' && r.url() === `${API}/api/v1/imports/${wider.id}/drift`)
    await steward.getByTestId('import-tab-drift').click()
    const drift = await (await loaded).json()
    // The baseline is this source's clean import (the quarantined one is not COMPLETED).
    expect(drift.baseline_import_id).toBe(cleanId)
    expect(drift.items.map((e: { drift_type: string; column: string }) => `${e.drift_type}:${e.column}`)).toEqual(['COLUMN_ADDED:legacy_flag'])
    const alert = steward.locator(`[data-testid="drift-event"][data-id="${drift.items[0].id}"]`)
    await expect(steward.getByTestId('drift-event')).toHaveCount(1)
    await expect(alert).toHaveAttribute('data-status', 'OPEN')
    await expect(alert.getByTestId('drift-type')).toHaveText('Column added')
    await expect(alert.getByTestId('drift-column')).toHaveText('legacy_flag')
    await expect(alert.getByTestId('drift-baseline')).toHaveText(cleanId)

    await alert.getByTestId('drift-review-open').click()
    const dialog = steward.getByTestId('drift-review-dialog')
    await dialog.locator('[data-testid="drift-review-outcome"][data-outcome="ACKNOWLEDGED"]').check()
    const comment = `WS2 smoke: legacy_flag is an expected upstream column (${code}).`
    await dialog.getByTestId('drift-review-comment-input').fill(comment)
    const posted = steward.waitForResponse((r) => r.request().method() === 'POST' && r.url() === `${API}/api/v1/drift-events/${drift.items[0].id}/review`)
    await dialog.getByTestId('drift-review-submit').click()
    const response = await posted
    expect(response.status(), await response.text()).toBe(200)
    const reviewed = await response.json()
    expect(reviewed).toMatchObject({ status: 'ACKNOWLEDGED', reviewed_by: `user:${me.user_id}`, review_comment: comment })
    await expect(dialog).toHaveCount(0)
    await expect(alert).toHaveAttribute('data-status', 'ACKNOWLEDGED')
    await expect(alert.getByTestId('drift-reviewed-by')).toHaveText(reviewed.reviewed_by)
    await expect(alert.getByTestId('drift-review-comment')).toHaveText(comment)
    await expect(alert.getByTestId('drift-review-open')).toHaveCount(0)
    if (process.env.WS2_SMOKE_SCREENSHOT) await steward.screenshot({ path: `${process.env.WS2_SMOKE_SCREENSHOT}/drift-reviewed.png`, fullPage: true })
  })

})
