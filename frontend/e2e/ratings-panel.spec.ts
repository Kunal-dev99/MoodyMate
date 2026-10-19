import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { expect, test, type APIRequestContext } from '@playwright/test'
import type { components } from '../src/api/schema'
import { API, SAMPLES, actorTitle, bearer, signIn, uploadSample } from './support'

/**
 * Feature 04 Phase 20 browser check against the real stack (backend ENV=test, synthetic).
 * FE-20.1: risk_analyst opens a counterparty, proposes a rating from the Ratings panel (grade
 *          from the credit vocabularies, effective date, required rationale with its counter),
 *          and the panel shows the server's IN_REVIEW version in the history. An approver, who
 *          lacks rating:propose, sees no "Propose rating" button (cosmetic; D-04).
 *
 * Setup (not under test) goes through the API, as counterparties.spec.ts does: a CSV source whose
 * contract is the counterparty sample's columns, a mapping of every column made ACTIVE, the
 * import validated and projected. A new source each run gives a new counterparty identity, so the
 * history starts empty. Every payload type is the generated one (src/api/schema.d.ts). Needs a
 * backend with RATING_CHANGE_ENABLED and CREDIT_PROJECTION_ENABLED on (their defaults), a
 * frontend built with NEXT_PUBLIC_RATINGS_UI and NEXT_PUBLIC_CREDIT_DOMAIN_UI on (their defaults)
 * and a disposable database (D-03).
 */

type Schemas = components['schemas']

test.describe.configure({ mode: 'serial' })

const FILE = 'counterparty.csv'

/** The header row of the sample (the file's columns, not an API payload); `#` lines are the D-24 marker. */
const sampleColumns = (): string[] =>
  readFileSync(join(SAMPLES, FILE), 'utf8')
    .split(/\r?\n/)
    .find((line) => line.trim() !== '' && !line.startsWith('#'))!
    .split(',')
    .map((c) => c.trim())

/** A projected synthetic counterparty import on its own new source and ACTIVE mapping. */
async function projectedCounterparties(request: APIRequestContext, steward: Record<string, string>) {
  const stamp = `e2e_rating_${Date.now()}`
  const schemas: Schemas['SchemaList'] = await (await request.get(`${API}/api/v1/schemas`, { headers: steward })).json()
  const schema = schemas.items.find((s) => s.code === 'counterparty')
  expect(schema, 'migration 0022 seeds the counterparty schema').toBeTruthy()
  const columns = sampleColumns()
  const body: Schemas['SourceCreate'] = {
    code: stamp,
    name: 'Ratings panel counterparties (FE-20.1 e2e)',
    kind: 'FILE_DROP',
    profile: { format: 'CSV', encoding: 'UTF-8', owner: 'Credit data team (e2e)', default_classification: 'confidential' },
    contract: {
      schema_id: schema!.id,
      expected_columns: columns.map((name) => ({ name, required: true })),
      business_key_columns: ['counterparty_id'],
      control_total_columns: [],
    },
  }
  const created = await request.post(`${API}/api/v1/sources`, { headers: steward, data: body })
  expect(created.status(), await created.text()).toBe(201)
  const source: Schemas['SourceDetail'] = await created.json()
  const uploaded: Schemas['ImportDetail'] = await uploadSample(request, steward, source.id, FILE)

  const versions: Schemas['SchemaVersionList'] = await (await request.get(`${API}/api/v1/schemas/${schema!.id}/versions`, { headers: steward })).json()
  const pinned = versions.items.find((v) => v.id === uploaded.schema_version_id)
  expect(pinned, 'the import pins a listed schema version').toBeTruthy()
  const types = new Map(pinned!.fields.map((f) => [f.name, f.data_type]))
  const rules: Schemas['RuleIn'][] = columns.map((column) => {
    const type = types.get(column)
    expect(type, `counterparty v${pinned!.version} has a field ${column}`).toBeTruthy()
    let transform: Schemas['RuleIn']['transform'] = { code: 'COPY', params: {} }
    if (type === 'DECIMAL') transform = { code: 'PARSE_DECIMAL' }
    else if (type === 'DATE') transform = { code: 'PARSE_DATE', params: { format: 'YYYY-MM-DD' } }
    return { source_column: column, target_field: column, column_policy: 'MAP', null_policy: 'ALLOW_NULL', transform }
  })
  const mapping: Schemas['MappingCreate'] = { code: stamp, name: 'Ratings panel counterparties (FE-20.1 e2e)', source_contract_id: source.current_contract!.id, rules }
  const m = await request.post(`${API}/api/v1/mappings`, { headers: steward, data: mapping })
  expect(m.status(), await m.text()).toBe(201)
  const version: Schemas['MappingVersionOut'] = (await m.json()).versions[0]
  const dry = await request.post(`${API}/api/v1/imports/${uploaded.id}/mappings/validate`, { headers: steward, data: { mapping_version_id: version.id } })
  expect(dry.status(), await dry.text()).toBe(201)
  const base = `${API}/api/v1/mappings/${version.mapping_id}/versions/${version.version}`
  expect((await request.post(`${base}/submit`, { headers: steward, data: {} })).status()).toBe(200)
  const approved = await request.post(`${base}/approval`, { headers: await bearer(request, 'approver'), data: { decision: 'APPROVE' } })
  expect(approved.status(), await approved.text()).toBe(200)

  const validated = await request.post(`${API}/api/v1/imports/${uploaded.id}/validate`, { headers: steward })
  expect(validated.status(), await validated.text()).toBe(200)
  const job: Schemas['ImportDetail'] = await validated.json()
  expect(job.status).toBe('COMPLETED')
  const projected = await request.post(`${API}/api/v1/imports/${job.id}/projection`, { headers: steward })
  expect(projected.status(), await projected.text()).toBe(201)

  const listed = await request.get(`${API}/api/v1/counterparties?import_id=${job.id}`, { headers: steward })
  expect(listed.status()).toBe(200)
  const page: Schemas['CounterpartyPage'] = await listed.json()
  expect(page.items.length).toBeGreaterThan(0)
  return page.items[0]
}

test('FE-20.1: risk_analyst proposes a rating from the counterparty Ratings panel, and the panel shows IN_REVIEW', async ({ page, browser, request }) => {
  const steward = await bearer(request, 'data_steward')
  const counterparty = await projectedCounterparties(request, steward)
  const detailUrl = `${API}/api/v1/counterparties/${counterparty.counterparty_id}`

  // 1. risk_analyst opens the counterparty: the Ratings panel, with an empty history.
  await signIn(page, 'Risk analyst')
  const read = page.waitForResponse((r) => r.request().method() === 'GET' && r.url() === detailUrl)
  await page.goto(`/domain/counterparties/${counterparty.counterparty_id}`)
  const before: Schemas['CounterpartyDetail'] = await (await read).json()
  expect(before.ratings).toEqual([])
  const panel = page.getByTestId('ratings-panel')
  await expect(panel).toHaveAttribute('data-counterparty-id', counterparty.counterparty_id)
  await expect(panel.getByTestId('no-ratings')).toBeVisible()

  // 2. The dialog: grades come from GET /credit-vocabularies; submitting empty shows the problems (nothing is sent).
  const vocabRead = page.waitForResponse((r) => r.request().method() === 'GET' && r.url() === `${API}/api/v1/credit-vocabularies`)
  await page.getByTestId('propose-rating-open').click()
  const vocab: Schemas['CreditVocabularyList'] = await (await vocabRead).json()
  const grades = vocab.items.filter((i) => i.vocabulary === 'rating_grade')
  const grade = grades.find((g) => g.code === 'G05') ?? grades[0]
  expect(grade, 'migration 0022 seeds rating grades').toBeTruthy()
  const dialog = page.getByTestId('propose-rating-dialog')
  await expect(dialog.getByTestId('rating-grade').locator('option')).toHaveCount(grades.length + 1)
  await dialog.getByTestId('propose-rating-submit').click()
  await expect(dialog.getByTestId('rating-grade-problem')).toHaveText('Choose a grade.')
  await expect(dialog.getByTestId('rating-effective-from-problem')).toHaveText('An effective date is required.')
  await expect(dialog.getByTestId('rating-rationale-problem')).toHaveText('A rationale is required.')
  await expect(dialog.getByTestId('propose-rating-submit')).toBeDisabled()

  // 3. Filled in: the counter follows the rationale; the server writes the proposal IN_REVIEW (201).
  const rationale = 'FE-20.1 e2e: two quarters of covenant breaches and a downgrade of the parent.'
  await dialog.getByTestId('rating-grade').selectOption(grade!.code)
  await dialog.getByTestId('rating-effective-from').fill('2026-09-30')
  await dialog.getByTestId('rating-rationale').fill(rationale)
  await expect(dialog.getByTestId('rating-rationale-counter')).toHaveText(`${rationale.length}/2000`)
  const posted = page.waitForResponse((r) => r.request().method() === 'POST' && r.url() === `${detailUrl}/ratings`)
  const reread = page.waitForResponse((r) => r.request().method() === 'GET' && r.url() === detailUrl)
  await dialog.getByTestId('propose-rating-submit').click()
  const createdResponse = await posted
  expect(createdResponse.status(), await createdResponse.text()).toBe(201)
  const created: Schemas['ObligorRatingOut'] = await createdResponse.json()
  expect(created.status).toBe('IN_REVIEW')
  expect(created.grade).toBe(grade!.code)
  expect(created.effective_from).toBe('2026-09-30')
  await expect(dialog).toBeHidden()

  // 4. The panel shows the server's version IN_REVIEW, re-read from the counterparty detail.
  const after: Schemas['CounterpartyDetail'] = await (await reread).json()
  expect(after.ratings.map((r) => [r.rating_id, r.status])).toEqual([[created.rating_id, 'IN_REVIEW']])
  await expect(panel.getByTestId('rating-proposed-status')).toHaveAttribute('data-status', 'IN_REVIEW')
  const row = panel.locator(`[data-testid="rating-row"][data-id="${created.rating_id}"]`)
  await expect(row).toHaveAttribute('data-status', 'IN_REVIEW')
  await expect(row.getByTestId('rating-status')).toHaveText('In review')
  await expect(row.getByTestId('rating-effective-from-cell')).toHaveText('2026-09-30')
  // The proposer is the signed-in user: "You", the raw actor in the tooltip (P-1).
  await expect(row.getByTestId('rating-proposer')).toContainText('You')
  await expect(row.getByTestId('rating-proposer').locator('[data-actor-kind]')).toHaveAttribute('title', actorTitle(created.proposed_by))
  await expect(row.getByTestId('rating-decider')).toHaveText('—')
  await expect(panel.getByTestId('rating-row')).toHaveCount(1)
  if (process.env.DOMAIN_SCREENSHOT) await page.screenshot({ path: `${process.env.DOMAIN_SCREENSHOT}/ratings-panel.png`, fullPage: true })

  // 5. The approver lacks rating:propose: the same history, and no "Propose rating" button (cosmetic).
  const approver = await browser.newPage()
  await signIn(approver, 'Approver')
  await approver.goto(`/domain/counterparties/${counterparty.counterparty_id}`)
  await expect(approver.locator(`[data-testid="rating-row"][data-id="${created.rating_id}"]`)).toHaveAttribute('data-status', 'IN_REVIEW')
  await expect(approver.getByTestId('propose-rating-open')).toHaveCount(0)
})
