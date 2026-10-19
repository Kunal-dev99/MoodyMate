import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { expect, test, type APIRequestContext, type Page } from '@playwright/test'
import type { components } from '../src/api/schema'
import { API, SAMPLES, bearer, signIn, uploadSample } from './support'

/**
 * Feature 04 Phase 19 browser check against the real stack (backend ENV=test, synthetic).
 * FE-19.1: data_steward projects the validated synthetic `counterparty` and `credit_exposure`
 *          imports from their import pages, then browses the Financial Domain's Counterparties
 *          tab: the catalogue (filtered by the server to the counterparty import) → a
 *          counterparty (versions, lineage, the exposures naming it) → an exposure (versions,
 *          source row, canonical record) → its import. risk_analyst sees `drawn_amount` (and
 *          `counterparty_name`) as "masked" (D-43 §1).
 *
 * Setup (not under test) goes through the API, as the backend's `credit_kit.validated_credit`
 * does: per sample, a CSV source whose contract is the file's columns, a mapping of every column
 * to the same-named v1 field made ACTIVE, the import validated. Every payload type is the
 * generated one (src/api/schema.d.ts); none is written here. Needs a backend with
 * CREDIT_PROJECTION_ENABLED on (its default), a frontend built with NEXT_PUBLIC_CREDIT_DOMAIN_UI
 * on (its default) and a disposable database (D-03).
 */

type Schemas = components['schemas']
type CreditCode = Extract<Schemas['DomainSchemaCode'], 'counterparty' | 'credit_exposure'>

const SAMPLE: Record<CreditCode, { file: string; key: string }> = {
  counterparty: { file: 'counterparty.csv', key: 'counterparty_id' },
  credit_exposure: { file: 'credit_exposure.csv', key: 'exposure_id' },
}
const projectionUrl = (id: string) => `${API}/api/v1/imports/${id}/projection`

test.describe.configure({ mode: 'serial' })

/** The header row of a sample (the file's columns, not an API payload); `#` lines are the D-24 marker. */
const sampleColumns = (file: string): string[] =>
  readFileSync(join(SAMPLES, file), 'utf8')
    .split(/\r?\n/)
    .find((line) => line.trim() !== '' && !line.startsWith('#'))!
    .split(',')
    .map((c) => c.trim())

/** A validated synthetic credit import of `code` on its own new source and ACTIVE mapping. */
async function validatedCredit(request: APIRequestContext, steward: Record<string, string>, code: CreditCode) {
  const { file, key } = SAMPLE[code]
  const stamp = `e2e_cp_${code}_${Date.now()}`
  const schemas: Schemas['SchemaList'] = await (await request.get(`${API}/api/v1/schemas`, { headers: steward })).json()
  const schema = schemas.items.find((s) => s.code === code)
  expect(schema, `migration 0022 seeds the ${code} schema`).toBeTruthy()
  const columns = sampleColumns(file)
  const body: Schemas['SourceCreate'] = {
    code: stamp,
    name: `Counterparties tab ${code} (FE-19.1 e2e)`,
    kind: 'FILE_DROP',
    profile: { format: 'CSV', encoding: 'UTF-8', owner: 'Credit data team (e2e)', default_classification: 'confidential' },
    contract: {
      schema_id: schema!.id,
      expected_columns: columns.map((name) => ({ name, required: true })),
      business_key_columns: [key],
      control_total_columns: [],
    },
  }
  const created = await request.post(`${API}/api/v1/sources`, { headers: steward, data: body })
  expect(created.status(), await created.text()).toBe(201)
  const source: Schemas['SourceDetail'] = await created.json()
  const uploaded: Schemas['ImportDetail'] = await uploadSample(request, steward, source.id, file)
  expect(uploaded.schema_code).toBe(code)

  const versions: Schemas['SchemaVersionList'] = await (await request.get(`${API}/api/v1/schemas/${schema!.id}/versions`, { headers: steward })).json()
  const pinned = versions.items.find((v) => v.id === uploaded.schema_version_id)
  expect(pinned, 'the import pins a listed schema version').toBeTruthy()
  const types = new Map(pinned!.fields.map((f) => [f.name, f.data_type]))
  const rules: Schemas['RuleIn'][] = columns.map((column) => {
    const type = types.get(column)
    expect(type, `${code} v${pinned!.version} has a field ${column}`).toBeTruthy()
    let transform: Schemas['RuleIn']['transform'] = { code: 'COPY', params: {} }
    if (type === 'DECIMAL') transform = { code: 'PARSE_DECIMAL' }
    else if (type === 'DATE') transform = { code: 'PARSE_DATE', params: { format: 'YYYY-MM-DD' } }
    return { source_column: column, target_field: column, column_policy: 'MAP', null_policy: 'ALLOW_NULL', transform }
  })
  const mapping: Schemas['MappingCreate'] = { code: stamp, name: `Counterparties tab ${code} (FE-19.1 e2e)`, source_contract_id: source.current_contract!.id, rules }
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
  return job
}

/** Projects an import from its page ("Project to domain", FE-14.2/19.2): 201, every accepted row. */
async function projectInBrowser(page: Page, job: Schemas['ImportDetail']) {
  await page.goto(`/ingestion/imports/${job.id}`)
  await expect(page.getByTestId('import-id')).toHaveText(job.id)
  const posted = page.waitForResponse((r) => r.request().method() === 'POST' && r.url() === projectionUrl(job.id))
  await page.getByTestId('domain-project').click()
  const created = await posted
  expect(created.status(), await created.text()).toBe(201)
  const projection: Schemas['ProjectionOut'] = await created.json()
  expect(projection.schema_code).toBe(job.schema_code)
  expect(projection.projected_count).toBe(job.manifest!.quality.accepted_records)
  await expect(page.getByTestId('projection-projected')).toHaveText(String(projection.projected_count))
}

test('FE-19.1: the steward projects both credit imports and browses counterparty → exposure → import; risk_analyst sees drawn_amount as "masked"', async ({
  page,
  browser,
  request,
}) => {
  const steward = await bearer(request, 'data_steward')
  const counterparties = await validatedCredit(request, steward, 'counterparty')
  const exposures = await validatedCredit(request, steward, 'credit_exposure')

  // 1. Both imports projected in the browser.
  await signIn(page, 'Data steward')
  await projectInBrowser(page, counterparties)
  await projectInBrowser(page, exposures)

  // 2. The Counterparties tab: the catalogue filtered by the server to the counterparty import,
  //    and the vocabularies badged "Provisional / illustrative".
  await page.goto('/domain')
  await page.getByTestId('domain-tab-counterparties').click()
  await page.waitForURL((url) => url.pathname === '/domain/counterparties')
  await expect(page.getByTestId('credit-vocabularies')).toBeVisible()
  await expect(
    page.locator('[data-testid="credit-vocabulary-entry"][data-provisional="true"]').first().getByTestId('provisional-badge'),
  ).toHaveText('Provisional / illustrative')
  const filtered = page.waitForResponse(
    (r) => r.request().method() === 'GET' && r.url().startsWith(`${API}/api/v1/counterparties?`) && r.url().includes(`import_id=${counterparties.id}`),
  )
  await page.getByTestId('cp-filter-import').fill(counterparties.id)
  await page.getByTestId('cp-filter-apply').click()
  const listedResponse = await filtered
  expect(listedResponse.status()).toBe(200)
  const listed: Schemas['CounterpartyPage'] = await listedResponse.json()
  expect(listed.total).toBe(counterparties.manifest!.quality.accepted_records)
  await expect(page.getByTestId('counterparty-table')).toHaveAttribute('data-total', String(listed.total))
  await expect(page.getByTestId('counterparty-row')).toHaveCount(listed.items.length)

  // 3. A counterparty with exposures (CP-0001 in the sample): versions, lineage, the exposures naming it.
  const target = listed.items.find((c) => c.business_key === 'CP-0001')
  expect(target, 'the sample has CP-0001').toBeTruthy()
  const detailRead = page.waitForResponse((r) => r.request().method() === 'GET' && r.url() === `${API}/api/v1/counterparties/${target!.counterparty_id}`)
  await page.locator(`[data-testid="counterparty-row"][data-id="${target!.counterparty_id}"]`).getByTestId('counterparty-link').click()
  await page.waitForURL(`**/domain/counterparties/${target!.counterparty_id}`)
  const detail: Schemas['CounterpartyDetail'] = await (await detailRead).json()
  await expect(page.getByTestId('counterparty-id')).toHaveText(detail.counterparty_id)
  await expect(page.getByTestId('counterparty-version')).toHaveCount(detail.versions.length)
  const cpVersion = detail.versions.find((v) => v.import_id === counterparties.id)
  expect(cpVersion).toBeTruthy()
  const cpCard = page.locator(`[data-testid="counterparty-version"][data-version-id="${cpVersion!.version_id}"]`)
  await expect(cpCard.getByTestId('version-source-ordinal')).toHaveText(String(cpVersion!.source_ordinal))
  await expect(cpCard.getByTestId('version-canonical-record')).toHaveText(cpVersion!.canonical_record_id)
  expect(typeof cpVersion!.counterparty_name).toBe('string')
  await expect(cpCard.getByTestId('version-name')).toHaveText(cpVersion!.counterparty_name as string)
  const exposureVersion = detail.exposure_versions.find((x) => x.import_id === exposures.id)
  expect(exposureVersion, "an exposure of this spec's import names CP-0001").toBeTruthy()
  const exposureRow = page.locator(`[data-testid="counterparty-exposure"][data-id="${exposureVersion!.credit_exposure_id}"]`)
  await expect(exposureRow).toBeVisible()
  if (process.env.DOMAIN_SCREENSHOT) await page.screenshot({ path: `${process.env.DOMAIN_SCREENSHOT}/counterparty.png`, fullPage: true })

  // 4. The exposure: its versions and lineage, as the API has them; the steward sees drawn_amount in clear.
  const exposureRead = page.waitForResponse(
    (r) => r.request().method() === 'GET' && r.url() === `${API}/api/v1/credit-exposures/${exposureVersion!.credit_exposure_id}`,
  )
  await exposureRow.getByTestId('counterparty-exposure-link').click()
  await page.waitForURL(`**/domain/counterparties/exposures/${exposureVersion!.credit_exposure_id}`)
  const exposure: Schemas['CreditExposureDetail'] = await (await exposureRead).json()
  await expect(page.getByTestId('exposure-id')).toHaveText(exposure.credit_exposure_id)
  await expect(page.getByTestId('exposure-version')).toHaveCount(exposure.versions.length)
  const version = exposure.versions.find((v) => v.import_id === exposures.id)
  expect(version).toBeTruthy()
  const card = page.locator(`[data-testid="exposure-version"][data-version-id="${version!.version_id}"]`)
  await expect(card).toHaveAttribute('data-import-id', exposures.id)
  await expect(card.getByTestId('version-source-ordinal')).toHaveText(String(version!.source_ordinal))
  await expect(card.getByTestId('version-canonical-record')).toHaveText(version!.canonical_record_id)
  await expect(card.getByTestId('version-source-record')).toHaveText(version!.source_record_id)
  expect(typeof version!.drawn_amount).toBe('string')
  await expect(card.getByTestId('version-drawn')).toHaveText(version!.drawn_amount as string)
  if (process.env.DOMAIN_SCREENSHOT) await page.screenshot({ path: `${process.env.DOMAIN_SCREENSHOT}/exposure.png`, fullPage: true })

  // 5. Lineage: the version's import link (import and source row) opens that import.
  const importLink = card.getByTestId('version-import-link')
  await expect(importLink).toContainText(`row ${version!.source_ordinal}`)
  await importLink.click()
  await page.waitForURL(`**/ingestion/imports/${exposures.id}`)
  await expect(page.getByTestId('import-id')).toHaveText(exposures.id)

  // 6. risk_analyst: the same exposure version, with drawn_amount masked by the server and shown as "masked".
  const analyst = await browser.newPage()
  await signIn(analyst, 'Risk analyst')
  const loaded = analyst.waitForResponse((r) => r.request().method() === 'GET' && r.url() === `${API}/api/v1/credit-exposures/${exposure.credit_exposure_id}`)
  await analyst.goto(`/domain/counterparties/exposures/${exposure.credit_exposure_id}`)
  const seen: Schemas['CreditExposureDetail'] = await (await loaded).json()
  const masked = seen.versions.find((v) => v.version_id === version!.version_id)
  expect(masked?.drawn_amount).toEqual({ masked: true, classification: expect.any(String) })
  const drawn = analyst.locator(`[data-testid="exposure-version"][data-version-id="${version!.version_id}"]`).getByTestId('version-drawn')
  await expect(drawn).toHaveAttribute('data-masked', 'true')
  await expect(drawn).toHaveText('masked')
  if (process.env.DOMAIN_SCREENSHOT) await analyst.screenshot({ path: `${process.env.DOMAIN_SCREENSHOT}/exposure-analyst.png`, fullPage: true })

  // ... and the counterparty's name in the catalogue (confidential, D-43 §1).
  await analyst.goto(`/domain/counterparties?import_id=${counterparties.id}`)
  const name = analyst.locator(`[data-testid="counterparty-row"][data-id="${target!.counterparty_id}"]`).getByTestId('counterparty-name')
  await expect(name).toHaveAttribute('data-masked', 'true')
  await expect(name).toHaveText('masked')
})
