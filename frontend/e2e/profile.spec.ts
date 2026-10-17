import { join } from 'node:path'
import { expect, test, type Page } from '@playwright/test'
import { API } from './support'

/**
 * Feature 02 Phase 9 browser checks against the real stack (backend ENV=test, synthetic).
 * FE-9.1: data_steward uploads the extra-column instrument fixture to the seeded
 *         core_banking source; the import's Profile tab shows the inferred types labelled
 *         "Suggested" and flags the extra column `legacy_flag` "Not in contract". The tab
 *         sends no mutating request. An auditor (internal clearance) sees the masked values
 *         as "masked", as many as the API masked for them.
 */

const FIXTURE = join(__dirname, '../../backend/samples/synthetic/instrument__unknown_extra_column.csv')
const EXTRA_COLUMN = 'legacy_flag'

async function signIn(page: Page, persona: string) {
  await page.goto('/login')
  await page.getByRole('button', { name: new RegExp(persona) }).click()
  await page.waitForURL('**/dashboard')
}

async function tokenOf(page: Page): Promise<string> {
  const raw = await page.evaluate(() => sessionStorage.getItem('moodymate_session'))
  return JSON.parse(raw ?? '{}').state.token
}

test.describe.configure({ mode: 'serial' })

let importId = ''

test('FE-9.1: data_steward uploads the extra-column fixture; the Profile tab shows Suggested types and the flagged column', async ({
  page,
}) => {
  await signIn(page, 'Data steward')
  await page.goto('/ingestion')
  await page.getByTestId('new-import').click()
  await page.waitForURL('**/ingestion/imports/new')
  const select = page.locator('#import-source')
  const value = await select.locator('option', { hasText: '(core_banking)' }).getAttribute('value')
  await select.selectOption(value!)
  await page.getByTestId('import-continue').click()

  await page.getByTestId('import-file').setInputFiles(FIXTURE)
  await page.getByTestId('synthetic-attested').click()
  const answered = page.waitForResponse((r) => r.request().method() === 'POST' && r.url() === `${API}/api/v1/imports`)
  await page.getByTestId('import-upload').click()
  const response = await answered
  expect(response.status()).toBe(201)
  importId = (await response.json()).id
  await page.waitForURL(`**/ingestion/imports/${importId}`)

  // From here on the page may only read.
  const mutating: string[] = []
  page.on('request', (r) => {
    if (r.url().startsWith(API) && r.method() !== 'GET' && r.method() !== 'OPTIONS') mutating.push(`${r.method()} ${r.url()}`)
  })

  // The status tab is the default; the profile loads when its tab is opened.
  await expect(page.getByTestId('import-summary')).toBeVisible()
  const profileLoaded = page.waitForResponse((r) => r.url() === `${API}/api/v1/imports/${importId}/profile`)
  await page.getByTestId('import-tab-profile').click()
  await page.waitForURL(`**/ingestion/imports/${importId}?tab=profile`)
  const profile = await (await profileLoaded).json()
  expect(profile.import_id).toBe(importId)

  // Every column is listed in file order, its inferred type labelled "Suggested".
  const rows = page.getByTestId('profile-column')
  await expect(rows).toHaveCount(profile.columns.length)
  for (const column of profile.columns) {
    const row = page.locator(`[data-testid="profile-column"][data-column="${column.name}"]`)
    await expect(row.getByTestId('profile-suggested-type')).toHaveText(
      column.suggested_type ? `Suggested: ${column.suggested_type}` : 'No type suggested',
    )
    await expect(row.getByTestId('profile-null-rate')).toBeVisible()
  }
  await expect(page.getByTestId('profile-suggested-type').filter({ hasText: /^Suggested: / }).first()).toBeVisible()

  // The extra column, and only it, is flagged "Not in contract".
  const extra = page.locator(`[data-testid="profile-column"][data-column="${EXTRA_COLUMN}"]`)
  await expect(extra.getByTestId('profile-not-in-contract')).toHaveText('Not in contract')
  await expect(page.getByTestId('profile-not-in-contract')).toHaveCount(1)
  await expect(page.getByTestId('profile-flag-count')).toContainText('1 column is not in the source contract')

  // The only schema action is the link to the Phase 5 "New proposal" page.
  const link = page.getByTestId('profile-new-proposal')
  await expect(link).toHaveAttribute('href', /^\/schemas\/proposals\/new(\?schema=[0-9a-f-]+)?$/)

  // A reload opens the Profile tab again from ?tab=profile.
  await page.reload()
  await expect(page.locator(`[data-testid="profile-column"][data-column="${EXTRA_COLUMN}"]`).getByTestId('profile-not-in-contract')).toBeVisible()

  expect(mutating).toEqual([])
  if (process.env.PROFILE_SCREENSHOT) await page.screenshot({ path: `${process.env.PROFILE_SCREENSHOT}/steward.png`, fullPage: true })
})

test('FE-9.1: an auditor sees masked profile values as "masked"', async ({ page }) => {
  expect(importId).not.toBe('')
  await signIn(page, 'Auditor')
  const r = await page.request.get(`${API}/api/v1/imports/${importId}/profile`, {
    headers: { Authorization: `Bearer ${await tokenOf(page)}` },
  })
  expect(r.status()).toBe(200)
  const body = await r.json()
  const isMasked = (v: unknown) => typeof v === 'object' && v !== null && (v as { masked?: boolean }).masked === true
  let masked = 0
  for (const c of body.columns) {
    masked += [c.min, c.max].filter(isMasked).length
    masked += c.top_values.filter((t: { value: unknown }) => isMasked(t.value)).length
  }

  // The seeded source's columns are above the auditor's internal clearance: values are masked.
  expect(masked).toBeGreaterThan(0)

  await page.goto(`/ingestion/imports/${importId}?tab=profile`)
  await expect(page.getByTestId('profile-column')).toHaveCount(body.columns.length)
  await expect(page.getByTestId('masked-value')).toHaveCount(masked)
  if (process.env.PROFILE_SCREENSHOT) await page.screenshot({ path: `${process.env.PROFILE_SCREENSHOT}/auditor.png`, fullPage: true })
  // The auditor holds no schema:propose: the proposal link is hidden (the server would refuse anyway).
  await expect(page.getByTestId('profile-new-proposal')).toHaveCount(0)
})
