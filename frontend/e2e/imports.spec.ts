import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { expect, test, type Page, type Route } from '@playwright/test'

/**
 * Feature 02 Phase 8 browser checks against the real stack (backend ENV=test, synthetic).
 * FE-8.1: data_steward uploads the synthetic instrument CSV with the attestation and lands
 *         on the import's page showing its status, the row count and the SHA-256 from the API.
 *         Since BE-9.2 every upload is profiled on receipt and, with no mapping before
 *         Phase 10, ends MAPPING_REQUIRED (D-21); that is the status shown.
 *         With the client-side size check bypassed, the backend's 413 toast shows its
 *         correlation ID.
 * FE-8.2: the imports list shows the uploaded import with its ImportStatusPill.
 *
 * The bypass is done in the test only (no switch in the app): the wizard uploads a small
 * valid file, so its pre-check passes, and Playwright swaps the request body for an
 * oversize multipart body with the same boundary on the way out. The backend then refuses
 * it on its own (413 payload_too_large), exactly as it would a client without the check.
 */

const API = 'http://localhost:8000'
const MIB = 1024 * 1024
const SAMPLE = join(__dirname, '../../backend/samples/synthetic/instrument.csv')
const SAMPLE_SHA256 = createHash('sha256').update(readFileSync(SAMPLE)).digest('hex')
// Data rows in the sample: every line but the marker comment and the header.
const SAMPLE_ROWS = 25

async function signIn(page: Page, persona: string) {
  await page.goto('/login')
  await page.getByRole('button', { name: new RegExp(persona) }).click()
  await page.waitForURL('**/dashboard')
}

async function tokenOf(page: Page): Promise<string> {
  const raw = await page.evaluate(() => sessionStorage.getItem('moodymate_session'))
  return JSON.parse(raw ?? '{}').state.token
}

async function importTotal(page: Page): Promise<number> {
  const r = await page.request.get(`${API}/api/v1/imports?limit=1`, { headers: { Authorization: `Bearer ${await tokenOf(page)}` } })
  expect(r.status()).toBe(200)
  return (await r.json()).total
}

/** Opens the wizard from the workbench and chooses the seeded core_banking source. */
async function startWizard(page: Page) {
  await page.goto('/ingestion')
  await page.getByTestId('new-import').click()
  await page.waitForURL('**/ingestion/imports/new')
  const select = page.locator('#import-source')
  const value = await select.locator('option', { hasText: '(core_banking)' }).getAttribute('value')
  await select.selectOption(value!)
  await expect(page.getByTestId('import-source-format')).toHaveText('CSV')
  await page.getByTestId('import-continue').click()
  return value!
}

test.describe.configure({ mode: 'serial' })

let importId = ''

test('FE-8.1: data_steward uploads the synthetic CSV and lands on its status page', async ({ page }) => {
  await signIn(page, 'Data steward')
  await startWizard(page)

  // X-Data-Class is synthetic here: the attestation is asked for and required.
  await page.getByTestId('import-file').setInputFiles(SAMPLE)
  await expect(page.getByTestId('import-file-size')).toContainText('instrument.csv')
  await expect(page.getByTestId('synthetic-attested')).toBeVisible()
  await expect(page.getByTestId('import-upload')).toBeDisabled()
  await expect(page.getByTestId('import-problems')).toContainText('Confirm that the file holds synthetic data only.')
  await page.getByTestId('synthetic-attested').click()
  await page.getByLabel('Expected row count').fill(String(SAMPLE_ROWS))
  await expect(page.getByTestId('import-problems')).toHaveCount(0)

  const posted = page.waitForRequest((r) => r.method() === 'POST' && r.url() === `${API}/api/v1/imports`)
  const answered = page.waitForResponse((r) => r.request().method() === 'POST' && r.url() === `${API}/api/v1/imports`)
  await page.getByTestId('import-upload').click()
  const request = await posted
  const response = await answered

  // Multipart with an Idempotency-Key. (Chromium does not expose a file-backed body to
  // the test; the attestation and file name are checked in the API's answer below.)
  const headers = await request.allHeaders()
  expect(headers['content-type']).toMatch(/^multipart\/form-data; boundary=/)
  expect(headers['idempotency-key']).toMatch(/^[\x21-\x7e]{1,255}$/)

  expect(response.status()).toBe(201)
  const created = await response.json()
  expect(created.status).toBe('MAPPING_REQUIRED')
  expect(created.row_count).toBe(SAMPLE_ROWS)
  expect(created.artifact.sha256).toBe(SAMPLE_SHA256)
  expect(created.synthetic_attested).toBe(true)
  expect(created.data_class).toBe('synthetic')
  expect(created.expected_row_count).toBe(SAMPLE_ROWS)
  expect(created.artifact.original_filename).toBe('instrument.csv')
  importId = created.id

  // The status page shows what the API returned.
  await page.waitForURL(`**/ingestion/imports/${importId}`)
  const summary = page.getByTestId('import-summary')
  await expect(summary.getByTestId('import-status')).toHaveText('Mapping required')
  await expect(summary.getByTestId('import-status')).toHaveAttribute('data-status', 'MAPPING_REQUIRED')
  await expect(page.getByTestId('import-row-count')).toHaveText(String(created.row_count))
  await expect(page.getByTestId('import-sha256')).toHaveText(created.artifact.sha256)
  await expect(page.getByTestId('import-id')).toHaveText(importId)
  await expect(page.getByTestId('import-schema-version')).toContainText(`${created.schema_code} v${created.schema_version}`)
  await expect(page.getByTestId('import-replayed')).toHaveCount(0)
  // The steward has no audit:read: the timeline says so instead of failing.
  await expect(page.getByTestId('audit-unavailable')).toBeVisible()

  // A reload reads it back from GET /imports/{id}.
  await page.reload()
  await expect(page.getByTestId('import-sha256')).toHaveText(SAMPLE_SHA256)
  await expect(page.getByTestId('import-row-count')).toHaveText(String(SAMPLE_ROWS))
})

test('FE-8.2: the imports list shows the uploaded import with its status pill', async ({ page }) => {
  expect(importId).not.toBe('')
  await signIn(page, 'Data steward')
  await page.goto('/ingestion')
  await page.getByRole('tab', { name: 'Imports' }).click()
  await page.waitForURL('**/ingestion?tab=imports')
  const row = page.locator(`[data-testid="import-row"][data-id="${importId}"]`)
  await expect(row).toBeVisible()
  await expect(row).toHaveAttribute('data-status', 'MAPPING_REQUIRED')
  await expect(row.getByTestId('import-status')).toHaveText('Mapping required')
  await expect(row).toContainText('core_banking')
  await expect(row).toContainText('instrument.csv')
  await expect(row).toContainText(SAMPLE_SHA256.slice(0, 12))
  // Newest first: the upload is at the top.
  await expect(page.getByTestId('import-row').first()).toHaveAttribute('data-id', importId)
  await row.getByRole('link').click()
  await page.waitForURL(`**/ingestion/imports/${importId}`)
  await expect(page.getByTestId('import-sha256')).toHaveText(SAMPLE_SHA256)
})

test('FE-8.1: an oversize file is stopped client-side; bypassed, the backend 413 toast shows its correlation ID', async ({ page }) => {
  await signIn(page, 'Data steward')
  const before = await importTotal(page)
  const sourceId = await startWizard(page)
  await page.getByTestId('synthetic-attested').click()

  // 1. The client-side pre-check: 10 MiB + 1 byte never leaves the browser.
  let posts = 0
  page.on('request', (r) => {
    if (r.method() === 'POST' && r.url() === `${API}/api/v1/imports`) posts += 1
  })
  await page.getByTestId('import-file').setInputFiles({ name: 'oversize.csv', mimeType: 'text/csv', buffer: Buffer.alloc(10 * MIB + 1, 'a') })
  await expect(page.getByTestId('import-file-size')).toHaveText(/the limit is 10\.00 MiB/)
  await expect(page.getByTestId('import-upload')).toBeDisabled()
  expect(posts).toBe(0)

  // 2. Bypassed: a valid small file passes the pre-check, and the request body is swapped
  //    on the wire for an oversize one (same multipart boundary, same headers).
  const oversize = Buffer.concat([Buffer.from('instrument_id\n'), Buffer.alloc(10 * MIB + 1 - 14, 'a')])
  expect(oversize.length).toBe(10 * MIB + 1)
  await page.route(`${API}/api/v1/imports`, async (route: Route) => {
    const req = route.request()
    if (req.method() !== 'POST') return route.continue()
    const boundary = /boundary=(.+)$/.exec(req.headers()['content-type'])![1]
    const part = (name: string, value: string) =>
      Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="${name}"\r\n\r\n${value}\r\n`)
    const postData = Buffer.concat([
      part('source_id', sourceId),
      part('synthetic_attested', 'true'),
      Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="oversize.csv"\r\nContent-Type: text/csv\r\n\r\n`),
      oversize,
      Buffer.from(`\r\n--${boundary}--\r\n`),
    ])
    await route.continue({ postData })
  })
  await page.getByTestId('import-file').setInputFiles(SAMPLE)
  await expect(page.getByTestId('import-problems')).toHaveCount(0)

  const answered = page.waitForResponse((r) => r.request().method() === 'POST' && r.url() === `${API}/api/v1/imports`)
  await page.getByTestId('import-upload').click()
  const response = await answered
  expect(response.status()).toBe(413)
  const envelope = await response.json()
  expect(envelope.code).toBe('payload_too_large')
  expect(envelope.details).toEqual({ limit: 10 * MIB })
  const cid = envelope.correlation_id as string
  expect(cid).toMatch(/^[0-9a-f-]{8,}/i)
  expect(response.headers()['x-correlation-id']).toBe(cid)

  // The backend's refusal reaches the user as a toast carrying its correlation ID.
  const toast = page.locator('[data-state="open"]').filter({ hasText: 'The file is too large' })
  await expect(toast).toBeVisible()
  await expect(toast).toContainText(`Correlation ID: ${cid}`)
  // Nothing was created, and the user stays on the wizard.
  expect(page.url()).toContain('/ingestion/imports/new')
  expect(await importTotal(page)).toBe(before)
})

test('FE-8.1: auditor sees the import and its audit timeline, and cannot upload', async ({ page }) => {
  expect(importId).not.toBe('')
  await signIn(page, 'Auditor')
  await page.goto('/ingestion')
  await expect(page.getByTestId('source-row').first()).toBeVisible()
  await expect(page.getByTestId('new-import')).toHaveCount(0)

  await page.goto(`/ingestion/imports/${importId}`)
  await expect(page.getByTestId('import-summary').getByTestId('import-status')).toHaveAttribute('data-status', 'MAPPING_REQUIRED')
  await expect(page.getByTestId('import-sha256')).toHaveText(SAMPLE_SHA256)
  const event = page.getByTestId('audit-event').filter({ hasText: 'import.create' })
  await expect(event).toHaveCount(1)
  await expect(event).toContainText('SUCCESS')
  await expect(event).toContainText(`import_job ${importId}`)
  await expect(event.getByTestId('correlation-id')).toHaveText(/^[0-9a-f-]{8,}/i)

  // The wizard is refused in the UI, and the server refuses the upload itself (D-04).
  await page.goto('/ingestion/imports/new')
  await expect(page.getByText("You don't have access to this")).toBeVisible()
  await expect(page.getByText('import:write')).toBeVisible()
  await expect(page.getByTestId('import-upload')).toHaveCount(0)
  const r = await page.request.post(`${API}/api/v1/imports`, {
    headers: { Authorization: `Bearer ${await tokenOf(page)}`, 'Idempotency-Key': `auditor-${Date.now()}` },
    multipart: { source_id: '00000000-0000-4000-8000-000000000000', synthetic_attested: 'true', file: { name: 'x.csv', mimeType: 'text/csv', buffer: Buffer.from('a\n1\n') } },
  })
  expect(r.status()).toBe(403)
  expect((await r.json()).code).toBe('forbidden')
})
