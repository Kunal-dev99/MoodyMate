import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { expect, test, type APIRequestContext } from '@playwright/test'
import type { components } from '../src/api/schema'
import { API, SAMPLES, bearer, signIn, uploadSample } from './support'

/**
 * Feature 04 Phase 19 browser check against the real stack (backend ENV=test, synthetic).
 * FE-19.2: data_steward projects the validated synthetic `credit_exposure` import from its page
 *          ("Project to domain", FE-14.2, now offering the two D-37 codes from the OpenAPI enum);
 *          the summary counts equal the manifest's accepted count. The auditor sees the summary
 *          with no button, and a direct POST is 403 (D-04).
 *
 * Setup (not under test) goes through the API, as the backend's `credit_kit.validated_credit`
 * does: a CSV source whose contract is the sample's columns, a mapping of every column to the
 * same-named `credit_exposure` v1 field made ACTIVE, the import validated. Every payload type
 * is the generated one (src/api/schema.d.ts); none is written here. Needs a backend with
 * CREDIT_PROJECTION_ENABLED on (its default) and a disposable database (D-03).
 */

type Schemas = components['schemas']

const FILE = 'credit_exposure.csv'
const projectionUrl = (id: string) => `${API}/api/v1/imports/${id}/projection`

/** The header row of the sample (the file's columns, not an API payload); `#` lines are the D-24 marker. */
const sampleColumns = (): string[] =>
  readFileSync(join(SAMPLES, FILE), 'utf8')
    .split(/\r?\n/)
    .find((line) => line.trim() !== '' && !line.startsWith('#'))!
    .split(',')
    .map((c) => c.trim())

/** A validated synthetic credit_exposure import on its own new source and ACTIVE mapping. */
async function validatedCreditExposure(request: APIRequestContext, steward: Record<string, string>) {
  const code = 'credit_exposure'
  const stamp = `e2e_credit_projection_${Date.now()}`
  const schemas: Schemas['SchemaList'] = await (await request.get(`${API}/api/v1/schemas`, { headers: steward })).json()
  const schema = schemas.items.find((s) => s.code === code)
  expect(schema, 'migration 0022 seeds the credit_exposure schema').toBeTruthy()
  const columns = sampleColumns()
  const body: Schemas['SourceCreate'] = {
    code: stamp,
    name: 'Credit exposures (FE-19.2 e2e)',
    kind: 'FILE_DROP',
    profile: { format: 'CSV', encoding: 'UTF-8', owner: 'Credit data team (e2e)', default_classification: 'confidential' },
    contract: {
      schema_id: schema!.id,
      expected_columns: columns.map((name) => ({ name, required: true })),
      business_key_columns: ['exposure_id'],
      control_total_columns: [],
    },
  }
  const created = await request.post(`${API}/api/v1/sources`, { headers: steward, data: body })
  expect(created.status(), await created.text()).toBe(201)
  const source: Schemas['SourceDetail'] = await created.json()
  const uploaded: Schemas['ImportDetail'] = await uploadSample(request, steward, source.id, FILE)
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
  const mapping: Schemas['MappingCreate'] = { code: stamp, name: 'Credit exposures (FE-19.2 e2e)', source_contract_id: source.current_contract!.id, rules }
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

test('FE-19.2: the steward projects the credit_exposure import; the summary equals the manifest; the auditor cannot', async ({
  page,
  browser,
  request,
}) => {
  const steward = await bearer(request, 'data_steward')
  const job = await validatedCreditExposure(request, steward)
  const importId = job.id
  const accepted = job.manifest!.quality.accepted_records
  expect(accepted).toBeGreaterThan(0)

  // 1. The steward projects in the browser: 201, and the page shows the server's counts.
  await signIn(page, 'Data steward')
  await page.goto(`/ingestion/imports/${importId}`)
  await expect(page.getByTestId('import-id')).toHaveText(importId)
  await expect(page.getByTestId('projection-not-projected')).toBeVisible()
  const posted = page.waitForResponse((r) => r.request().method() === 'POST' && r.url() === projectionUrl(importId))
  await page.getByTestId('domain-project').click()
  const created = await posted
  expect(created.status(), await created.text()).toBe(201)
  const projection: Schemas['ProjectionOut'] = await created.json()
  expect(projection.schema_code).toBe('credit_exposure')
  expect(projection.accepted_count).toBe(accepted)
  expect(projection.projected_count).toBe(accepted)

  const summary = page.getByTestId('projection-summary')
  await expect(summary).toHaveAttribute('data-projection-id', projection.projection_id)
  await expect(summary).toHaveAttribute('data-origin', 'created')
  await expect(page.getByTestId('projection-id')).toHaveText(projection.projection_id)
  await expect(page.getByTestId('projection-schema')).toContainText('credit_exposure v1')
  await expect(page.getByTestId('projection-credit-schema')).toBeVisible()
  await expect(page.getByTestId('projection-accepted')).toHaveText(String(accepted))
  await expect(page.getByTestId('projection-projected')).toHaveText(String(accepted))
  await expect(page.getByTestId('projection-rows')).toHaveText(String(projection.summary.rows_projected))
  await expect(page.getByTestId('projection-conserves')).toHaveAttribute('data-conserves', 'true')
  if (process.env.DOMAIN_SCREENSHOT) await page.screenshot({ path: `${process.env.DOMAIN_SCREENSHOT}/credit-projection.png`, fullPage: true })

  // 2. The auditor (domain:read, no domain:project) sees the summary but no button; the server
  //    refuses a direct POST (D-04).
  const auditor = await browser.newPage()
  await signIn(auditor, 'Auditor')
  const seen = auditor.waitForResponse((r) => r.request().method() === 'GET' && r.url() === projectionUrl(importId))
  await auditor.goto(`/ingestion/imports/${importId}`)
  expect((await seen).status()).toBe(200)
  await expect(auditor.getByTestId('projection-summary')).toHaveAttribute('data-projection-id', projection.projection_id)
  await expect(auditor.getByTestId('projection-projected')).toHaveText(String(accepted))
  await expect(auditor.getByTestId('domain-project')).toHaveCount(0)
  const refused = await request.post(projectionUrl(importId), { headers: await bearer(request, 'auditor') })
  expect(refused.status()).toBe(403)
  const envelope: Schemas['ErrorEnvelope'] = await refused.json()
  expect(envelope.code).toBe('forbidden')
})
