import { expect, test } from '@playwright/test'
import {
  API,
  actorTitle,
  bearer,
  coreBanking,
  ensureActiveMapping,
  shownFigure as shown,
  signIn,
  uploadInBrowser,
  uploadSample,
  validateInBrowser,
  type ReconItem as Item,
} from './support'

/**
 * Feature 02 Phase 12 browser checks against the real stack (backend ENV=test, synthetic).
 * FE-12.1: the quarantine fixture (instrument__mixed_errors.csv), validated in the browser,
 *          shows REVIEW_REQUIRED, and every figure on the Reconciliation tab (variance
 *          included) is the API's string. Before validation the tab shows "not reconciled
 *          yet"; risk_analyst sees the control totals as "masked".
 * FE-12.2: data_steward uploads a clean import (validated: the source's drift baseline), then
 *          the extra-column import, sees the COLUMN_ADDED alert on the Drift tab, acknowledges
 *          it in the review dialog, and the alert then shows the reviewer and the comment.
 *          "Action required" links to the Phase 5 proposal page with the schema prefilled and
 *          creates nothing; risk_analyst has no Review button and the API refuses (403).
 *
 * This file runs after mappings.spec (alphabetical, one worker): it relies on, or creates, an
 * ACTIVE mapping for the core_banking contract, which mappings.spec creates through the UI.
 */

test.describe.configure({ mode: 'serial' })

let mixedId = ''
let cleanId = ''

test('FE-12.1: the quarantine fixture reconciles REVIEW_REQUIRED; every figure, the variance included, is the API string', async ({ page, request }) => {
  // Setup through the API: an ACTIVE mapping (dry-run on a clean import), then the quarantine fixture.
  const steward = await bearer(request, 'data_steward')
  const { sourceId, contractId } = await coreBanking(request, steward)
  const clean = await uploadSample(request, steward, sourceId, 'instrument.csv')
  await ensureActiveMapping(request, contractId, sourceId, clean.id)
  mixedId = (await uploadSample(request, steward, sourceId, 'instrument__mixed_errors.csv')).id

  await signIn(page, 'Data steward')
  // Not validated yet: GET reconciliation is 404 reconciliation_not_found, shown as an empty state.
  const before = page.waitForResponse((r) => r.url() === `${API}/api/v1/imports/${mixedId}/reconciliation`)
  await page.goto(`/ingestion/imports/${mixedId}?tab=reconciliation`)
  const missing = await before
  expect(missing.status()).toBe(404)
  expect((await missing.json()).code).toBe('reconciliation_not_found')
  await expect(page.getByTestId('recon-not-run')).toBeVisible()
  await expect(page.getByTestId('recon-table')).toHaveCount(0)

  // Validate in the browser: validation reconciles in the same transaction.
  const job = await validateInBrowser(page, mixedId)
  expect(job.status).toBe('QUARANTINED')

  const loaded = page.waitForResponse((r) => r.request().method() === 'GET' && r.url() === `${API}/api/v1/imports/${mixedId}/reconciliation`)
  await page.getByTestId('import-tab-reconciliation').click()
  const response = await loaded
  expect(response.status()).toBe(200)
  const run = await response.json()
  expect(run.outcome).toBe('REVIEW_REQUIRED')

  // The outcome is the server's.
  const outcome = page.getByTestId('recon-summary').getByTestId('recon-outcome')
  await expect(outcome).toHaveAttribute('data-outcome', 'REVIEW_REQUIRED')
  await expect(outcome).toHaveText('Review required')
  await expect(page.getByTestId('recon-id')).toHaveText(run.reconciliation_id)
  await expect(page.getByTestId('recon-policy')).toContainText(`tolerance ${run.tolerance_policy.tolerance}`)
  await expect(page.getByTestId('recon-policy')).toContainText('Provisional')

  // Every check, every figure: the page text equals the API string.
  const rows = page.getByTestId('recon-item')
  await expect(rows).toHaveCount(run.items.length)
  for (const item of run.items as Item[]) {
    const row = page.locator(`[data-testid="recon-item"][data-sequence="${item.sequence}"]`)
    await expect(row).toHaveAttribute('data-check', item.check)
    await expect(row).toHaveAttribute('data-status', item.status)
    for (const key of ['source', 'target', 'held', 'variance', 'tolerance'] as const) await expect(row.getByTestId(`recon-${key}`)).toHaveText(shown(item[key]))
  }

  // The quarantine fixture holds rows, so the row count and the notional total differ. How many
  // rows are held depends on the ACTIVE mapping (mappings.spec may have built it), so the
  // figures come from the API: the page shows its exact decimal text.
  const byCheck = (check: string, column: string | null) => (run.items as Item[]).find((i) => i.check === check && i.column === column)!
  expect(byCheck('ROW_COUNT', null)).toMatchObject({ source: String(job.row_count), status: 'VARIANCE' })
  const notional = byCheck('CONTROL_TOTAL', 'notional')
  expect(notional.status).toBe('VARIANCE')
  expect(notional.variance).toMatch(/^-?\d+(\.\d+)?$/)
  await expect(page.locator('[data-testid="recon-item"][data-check="CONTROL_TOTAL"][data-column="notional"]').getByTestId('recon-variance')).toHaveText(
    notional.variance as string,
  )
  if (process.env.RECONCILIATION_SCREENSHOT) await page.screenshot({ path: `${process.env.RECONCILIATION_SCREENSHOT}/reconciliation.png`, fullPage: true })
})

test('FE-12.1: risk_analyst sees the counts but the control totals as "masked", never blank', async ({ browser }) => {
  expect(mixedId).not.toBe('')
  const page = await browser.newPage()
  await signIn(page, 'Risk analyst')
  const loaded = page.waitForResponse((r) => r.request().method() === 'GET' && r.url() === `${API}/api/v1/imports/${mixedId}/reconciliation`)
  await page.goto(`/ingestion/imports/${mixedId}?tab=reconciliation`)
  const run = await (await loaded).json()
  await expect(page.getByTestId('recon-summary').getByTestId('recon-outcome')).toHaveAttribute('data-outcome', run.outcome)
  const notional = page.locator('[data-testid="recon-item"][data-check="CONTROL_TOTAL"][data-column="notional"]')
  await expect(notional.getByTestId('masked-value')).toHaveCount(4) // source, target, held, variance
  await expect(notional.getByTestId('recon-variance')).toHaveText('masked')
  const counts = page.locator('[data-testid="recon-item"][data-check="ROW_COUNT"]')
  const rows = (run.items as Item[]).find((i) => i.check === 'ROW_COUNT')!
  expect(typeof rows.variance).toBe('string') // counts are public: never masked
  await expect(counts.getByTestId('recon-variance')).toHaveText(rows.variance as string)
  await expect(counts.getByTestId('masked-value')).toHaveCount(0)
})

test('FE-12.2: a clean import, then the extra-column import: data_steward acknowledges the COLUMN_ADDED alert and it shows the reviewer and comment', async ({ page, request }) => {
  const steward = await bearer(request, 'data_steward')
  const { sourceId, contractId } = await coreBanking(request, steward)
  const me = await (await request.get(`${API}/api/v1/me`, { headers: steward })).json()
  await signIn(page, 'Data steward')

  // 1. The clean import, uploaded and validated in the browser: the source's baseline.
  const clean = await uploadInBrowser(page, sourceId, 'instrument.csv')
  cleanId = clean.id
  await ensureActiveMapping(request, contractId, sourceId, clean.id) // usually kept from the FE-12.1 setup
  const done = await validateInBrowser(page, clean.id)
  expect(['COMPLETED', 'COMPLETED_WITH_WARNINGS']).toContain(done.status)

  // 2. The extra-column import, uploaded in the browser (profiled, then compared with the baseline).
  const wider = await uploadInBrowser(page, sourceId, 'instrument__unknown_extra_column.csv')

  // 3. The Drift tab shows the one COLUMN_ADDED alert against the clean baseline.
  const loaded = page.waitForResponse((r) => r.request().method() === 'GET' && r.url() === `${API}/api/v1/imports/${wider.id}/drift`)
  await page.getByTestId('import-tab-drift').click()
  const drift = await (await loaded).json()
  expect(drift.baseline_import_id).toBe(clean.id)
  expect(drift.items.map((e: { drift_type: string; column: string }) => `${e.drift_type}:${e.column}`)).toEqual(['COLUMN_ADDED:legacy_flag'])
  const alert = page.locator(`[data-testid="drift-event"][data-id="${drift.items[0].id}"]`)
  await expect(page.getByTestId('drift-event')).toHaveCount(1)
  await expect(alert).toHaveAttribute('data-drift-type', 'COLUMN_ADDED')
  await expect(alert).toHaveAttribute('data-status', 'OPEN')
  await expect(alert.getByTestId('drift-type')).toHaveText('Column added')
  await expect(alert.getByTestId('drift-column')).toHaveText('legacy_flag')
  await expect(alert.getByTestId('drift-baseline')).toHaveText(clean.id)
  await expect(alert.getByTestId('drift-awaiting-review')).toBeVisible()
  await expect(alert.getByTestId('drift-propose')).toHaveCount(0)

  // 4. Acknowledge it as data_steward: an outcome and a comment are both required.
  await alert.getByTestId('drift-review-open').click()
  const dialog = page.getByTestId('drift-review-dialog')
  const submit = dialog.getByTestId('drift-review-submit')
  await expect(submit).toBeDisabled()
  await dialog.locator('[data-testid="drift-review-outcome"][data-outcome="ACKNOWLEDGED"]').check()
  await expect(submit).toBeDisabled()
  const comment = `Expected: legacy_flag is a new upstream column (e2e ${Date.now()}).`
  await dialog.getByTestId('drift-review-comment-input').fill(comment)
  const posted = page.waitForResponse((r) => r.request().method() === 'POST' && r.url() === `${API}/api/v1/drift-events/${drift.items[0].id}/review`)
  await submit.click()
  const response = await posted
  expect(response.status(), await response.text()).toBe(200)
  const reviewed = await response.json()
  expect(reviewed).toMatchObject({ status: 'ACKNOWLEDGED', reviewed_by: `user:${me.user_id}`, review_comment: comment, proposal_link: null })
  await expect(dialog).toHaveCount(0)

  // 5. The alert now shows the reviewer and the comment, from the server, and stays reviewed.
  await expect(alert).toHaveAttribute('data-status', 'ACKNOWLEDGED')
  // The reviewer is the signed-in user: shown as "You", the raw actor kept in the tooltip (P-1).
  await expect(alert.getByTestId('drift-reviewed-by')).toHaveText('You')
  await expect(alert.getByTestId('drift-reviewed-by')).toHaveAttribute('title', actorTitle(reviewed.reviewed_by))
  await expect(alert.getByTestId('drift-review-comment')).toHaveText(comment)
  await expect(alert.getByTestId('drift-review-open')).toHaveCount(0)
  await page.reload()
  await expect(alert).toHaveAttribute('data-status', 'ACKNOWLEDGED')
  await expect(alert.getByTestId('drift-reviewed-by')).toHaveText('You')
  await expect(alert.getByTestId('drift-reviewed-by')).toHaveAttribute('title', actorTitle(`user:${me.user_id}`))
  await expect(alert.getByTestId('drift-review-comment')).toHaveText(comment)
  if (process.env.RECONCILIATION_SCREENSHOT) await page.screenshot({ path: `${process.env.RECONCILIATION_SCREENSHOT}/drift.png`, fullPage: true })
})

test('FE-12.2: "Action required" links to the Phase 5 proposal page with the schema prefilled and creates nothing; risk_analyst cannot review', async ({ browser, request }) => {
  expect(cleanId).not.toBe('')
  const steward = await bearer(request, 'data_steward')
  const { sourceId } = await coreBanking(request, steward)
  // Setup through the API: another extra-column import, so another OPEN alert on the same baseline.
  const again = await uploadSample(request, steward, sourceId, 'instrument__unknown_extra_column.csv')
  const proposals = async () => (await (await request.get(`${API}/api/v1/schema-change-proposals?limit=1`, { headers: steward })).json()).total as number
  const before = await proposals()

  // risk_analyst: the alert with its values masked, no Review button; the API refuses the review.
  const analyst = await browser.newPage()
  await signIn(analyst, 'Risk analyst')
  const seen = analyst.waitForResponse((r) => r.request().method() === 'GET' && r.url() === `${API}/api/v1/imports/${again.id}/drift`)
  await analyst.goto(`/ingestion/imports/${again.id}?tab=drift`)
  const page1 = await (await seen).json()
  const [event] = page1.items
  expect(event).toMatchObject({ drift_type: 'COLUMN_ADDED', status: 'OPEN', baseline_import_id: cleanId })
  expect(event.observed.min).toEqual({ masked: true, classification: event.classification })
  const alert1 = analyst.locator(`[data-testid="drift-event"][data-id="${event.id}"]`)
  await expect(alert1).toHaveAttribute('data-status', 'OPEN')
  await expect(alert1.getByTestId('masked-value')).toHaveCount(2) // observed min and max
  await expect(alert1.getByTestId('drift-review-open')).toHaveCount(0)
  const refused = await request.post(`${API}/api/v1/drift-events/${event.id}/review`, {
    headers: await bearer(request, 'risk_analyst'),
    data: { outcome: 'ACKNOWLEDGED', comment: 'Not allowed.' },
  })
  expect(refused.status()).toBe(403)

  // data_steward: Action required, then the proposal link.
  const page = await browser.newPage()
  await signIn(page, 'Data steward')
  await page.goto(`/ingestion/imports/${again.id}?tab=drift`)
  const alert = page.locator(`[data-testid="drift-event"][data-id="${event.id}"]`)
  await alert.getByTestId('drift-review-open').click()
  const dialog = page.getByTestId('drift-review-dialog')
  await dialog.locator('[data-testid="drift-review-outcome"][data-outcome="ACTION_REQUIRED"]').check()
  await dialog.getByTestId('drift-review-comment-input').fill('legacy_flag should become a schema field.')
  if (process.env.RECONCILIATION_SCREENSHOT) await page.screenshot({ path: `${process.env.RECONCILIATION_SCREENSHOT}/drift-review-dialog.png` })
  const posted = page.waitForResponse((r) => r.request().method() === 'POST' && r.url() === `${API}/api/v1/drift-events/${event.id}/review`)
  await dialog.getByTestId('drift-review-submit').click()
  const reviewed = await (await posted).json()
  expect(reviewed.status).toBe('ACTION_REQUIRED')
  expect(reviewed.proposal_link.href).toBe(`/schemas/proposals/new?schema=${reviewed.proposal_link.schema_id}`)
  await expect(alert).toHaveAttribute('data-status', 'ACTION_REQUIRED')
  const link = alert.getByTestId('drift-propose')
  await expect(link).toHaveAttribute('href', reviewed.proposal_link.href)
  await link.click()
  await page.waitForURL(`**/schemas/proposals/new?schema=${reviewed.proposal_link.schema_id}`)
  await expect(page.locator('#schema')).toHaveValue(reviewed.proposal_link.schema_id)
  await expect(page.locator('#title')).toHaveValue('') // the schema only is prefilled
  expect(await proposals()).toBe(before) // nothing was created
})
