import { expect, test, type APIRequestContext } from '@playwright/test'
import type { components } from '../src/api/schema'
import { API, bearer, ensureActiveMapping, groupDecimal, signIn, uploadSample } from './support'

/**
 * Feature 03 Phase 14 browser checks against the real stack (backend ENV=test, synthetic).
 * FE-14.2: data_steward projects the validated synthetic instrument import from its page; the
 *          summary counts equal the manifest's accepted count; a second click shows the same
 *          projection (200, nothing written), and so does a reload (GET). The auditor sees the
 *          summary with no button, and a direct POST is 403.
 * FE-14.1: the steward browses the Financial Domain catalogue (filtered to that import) to an
 *          instrument, sees its versions, attributes and lineage, and follows a version to its
 *          import. risk_analyst sees `notional` as "masked".
 *
 * Every payload type is the generated one (src/api/schema.d.ts); none is written here.
 *
 * Ordering: this file sorts before smoke.spec, whose applied proposal moves the `instrument`
 * schema past v1, which projector v1 refuses (`projection_unsupported`). It registers its own
 * source (a copy of core_banking's contract) and mapping, so core_banking stays unmapped for
 * mappings.spec. Run it on a fresh database (CI does; locally a disposable copy, D-03).
 */

type Schemas = components['schemas']

test.describe.configure({ mode: 'serial' })

let importId = ''
let accepted = 0

/** A new source whose contract copies the seeded core_banking one (it fits instrument.csv). */
async function registerSource(request: APIRequestContext, steward: Record<string, string>) {
  const list: Schemas['SourcePage'] = await (await request.get(`${API}/api/v1/sources?limit=200`, { headers: steward })).json()
  const core = list.items.find((s) => s.code === 'core_banking')
  expect(core?.current_contract).toBeTruthy()
  const contract = core!.current_contract!
  const profile = core!.profile
  const body: Schemas['SourceCreate'] = {
    code: `e2e_domain_${Date.now()}`,
    name: 'Domain projection (e2e)',
    kind: 'FILE_DROP',
    profile: {
      format: profile.format,
      encoding: profile.encoding,
      delimiter: profile.delimiter,
      owner: profile.owner,
      default_classification: profile.default_classification,
    },
    contract: {
      schema_id: contract.schema_id,
      expected_columns: contract.expected_columns,
      business_key_columns: contract.business_key_columns,
      control_total_columns: contract.control_total_columns,
    },
  }
  const r = await request.post(`${API}/api/v1/sources`, { headers: steward, data: body })
  expect(r.status(), await r.text()).toBe(201)
  const source: Schemas['SourceDetail'] = await r.json()
  return { sourceId: source.id, contractId: source.current_contract!.id }
}

const projectionUrl = (id: string) => `${API}/api/v1/imports/${id}/projection`

test('FE-14.2: the steward projects the instrument import; the summary equals the manifest; a second click shows the same projection; the auditor cannot', async ({
  page,
  browser,
  request,
}) => {
  // Setup through the API: a validated synthetic instrument import on this spec's own source.
  const steward = await bearer(request, 'data_steward')
  const { sourceId, contractId } = await registerSource(request, steward)
  const uploaded: Schemas['ImportDetail'] = await uploadSample(request, steward, sourceId, 'instrument.csv')
  importId = uploaded.id
  expect(uploaded.schema_code).toBe('instrument')
  expect(uploaded.schema_version, 'projector v1 supports instrument v1 only: run on a fresh database, before smoke.spec').toBe(1)
  await ensureActiveMapping(request, contractId, sourceId, importId, `e2e_domain_${Date.now()}`)
  const validated = await request.post(`${API}/api/v1/imports/${importId}/validate`, { headers: steward })
  expect(validated.status(), await validated.text()).toBe(200)
  const job: Schemas['ImportDetail'] = await validated.json()
  expect(['COMPLETED', 'COMPLETED_WITH_WARNINGS']).toContain(job.status)
  accepted = job.manifest!.quality.accepted_records
  expect(accepted).toBeGreaterThan(0)

  // 1. Project in the browser: 201, and the page shows the server's counts.
  await signIn(page, 'Data steward')
  await page.goto(`/ingestion/imports/${importId}`)
  await expect(page.getByTestId('import-id')).toHaveText(importId)
  // Not projected yet: GET /imports/{id}/projection is 404 projection_not_found, shown as such.
  await expect(page.getByTestId('projection-not-projected')).toBeVisible()
  const button = page.getByTestId('domain-project')
  const first = page.waitForResponse((r) => r.request().method() === 'POST' && r.url() === projectionUrl(importId))
  await button.click()
  const created = await first
  expect(created.status(), await created.text()).toBe(201)
  const projection: Schemas['ProjectionOut'] = await created.json()
  expect(projection.accepted_count).toBe(accepted)
  expect(projection.projected_count).toBe(accepted)

  const summary = page.getByTestId('projection-summary')
  await expect(summary).toHaveAttribute('data-projection-id', projection.projection_id)
  await expect(summary).toHaveAttribute('data-origin', 'created')
  await expect(page.getByTestId('projection-id')).toHaveText(projection.projection_id)
  await expect(page.getByTestId('projection-accepted')).toHaveText(String(accepted))
  await expect(page.getByTestId('projection-projected')).toHaveText(String(accepted))
  await expect(page.getByTestId('projection-rows')).toHaveText(String(projection.summary.rows_projected))
  expect(projection.summary.rows_projected).toBe(accepted)
  await expect(page.getByTestId('projection-conserves')).toHaveAttribute('data-conserves', 'true')
  // TYPE_NOT_PROJECTED per field, as the server counted it (instrument v1 maturity_date is free text).
  const notProjected = Object.entries(projection.summary.type_not_projected)
  expect(notProjected.map(([field]) => field)).toContain('maturity_date')
  await expect(page.getByTestId('type-not-projected')).toHaveCount(notProjected.length)
  for (const [field, count] of notProjected)
    await expect(page.locator(`[data-testid="type-not-projected"][data-field="${field}"]`)).toHaveAttribute('data-count', String(count))
  if (process.env.DOMAIN_SCREENSHOT) await page.screenshot({ path: `${process.env.DOMAIN_SCREENSHOT}/projection.png`, fullPage: true })

  // 2. A second click: 200, the same projection, nothing written.
  const second = page.waitForResponse((r) => r.request().method() === 'POST' && r.url() === projectionUrl(importId))
  await button.click()
  const replay = await second
  expect(replay.status()).toBe(200)
  const again: Schemas['ProjectionOut'] = await replay.json()
  expect(again.projection_id).toBe(projection.projection_id)
  expect(again.projected_count).toBe(accepted)
  await expect(summary).toHaveAttribute('data-origin', 'replayed')
  await expect(summary).toHaveAttribute('data-projection-id', projection.projection_id)
  await expect(page.getByTestId('projection-outcome')).toContainText('Nothing new was written')
  await expect(page.getByTestId('projection-projected')).toHaveText(String(accepted))

  // 3. A reload reads the projection (GET, domain:read) and shows the same summary.
  const read = page.waitForResponse((r) => r.request().method() === 'GET' && r.url() === projectionUrl(importId))
  await page.reload()
  expect((await read).status()).toBe(200)
  await expect(summary).toHaveAttribute('data-origin', 'existing')
  await expect(summary).toHaveAttribute('data-projection-id', projection.projection_id)
  await expect(page.getByTestId('projection-accepted')).toHaveText(String(accepted))
  await expect(page.getByTestId('projection-projected')).toHaveText(String(accepted))
  await expect(page.getByTestId('domain-project')).toBeVisible()

  // 4. The auditor (domain:read, no domain:project) sees the summary but no button; the server
  //    refuses a direct POST (D-04).
  const auditor = await browser.newPage()
  await signIn(auditor, 'Auditor')
  const seen = auditor.waitForResponse((r) => r.request().method() === 'GET' && r.url() === projectionUrl(importId))
  await auditor.goto(`/ingestion/imports/${importId}`)
  expect((await seen).status()).toBe(200)
  await expect(auditor.getByTestId('import-id')).toHaveText(importId)
  const auditorSummary = auditor.getByTestId('projection-summary')
  await expect(auditorSummary).toHaveAttribute('data-origin', 'existing')
  await expect(auditorSummary).toHaveAttribute('data-projection-id', projection.projection_id)
  await expect(auditor.getByTestId('projection-projected')).toHaveText(String(accepted))
  await expect(auditor.getByTestId('domain-project')).toHaveCount(0)
  const refused = await request.post(projectionUrl(importId), { headers: await bearer(request, 'auditor') })
  expect(refused.status()).toBe(403)
  const envelope: Schemas['ErrorEnvelope'] = await refused.json()
  expect(envelope.code).toBe('forbidden')
})

test('FE-14.1: the steward browses the catalogue to an instrument and its import; risk_analyst sees notional as "masked"', async ({
  page,
  browser,
  request,
}) => {
  expect(importId).not.toBe('')
  const steward = await bearer(request, 'data_steward')

  // 1. The catalogue, filtered by the server to this import.
  await signIn(page, 'Data steward')
  await page.goto('/domain')
  await expect(page.getByText('This workspace is planned, not built')).toHaveCount(0)
  await expect(page.getByTestId('instrument-type-vocabulary')).toBeVisible()
  await expect(page.locator('[data-testid="instrument-type"][data-provisional="true"]').first().getByTestId('provisional-badge')).toHaveText(
    'Provisional / illustrative',
  )
  const filtered = page.waitForResponse(
    (r) => r.request().method() === 'GET' && r.url().startsWith(`${API}/api/v1/instruments?`) && r.url().includes(`import_id=${importId}`),
  )
  await page.getByTestId('filter-import').fill(importId)
  await page.getByTestId('filter-apply').click()
  const response = await filtered
  expect(response.status()).toBe(200)
  const listed: Schemas['InstrumentPage'] = await response.json()
  expect(listed.total).toBeGreaterThan(0)
  await expect(page.getByTestId('instrument-table')).toHaveAttribute('data-total', String(listed.total))
  await expect(page.getByTestId('instrument-row')).toHaveCount(listed.items.length)
  await page.waitForURL((url) => url.pathname === '/domain' && url.searchParams.get('import_id') === importId)

  // 2. One instrument: its versions, attributes and lineage, as the API has them.
  const target = listed.items[0]
  await page.locator(`[data-testid="instrument-row"][data-id="${target.instrument_id}"]`).getByTestId('instrument-link').click()
  await page.waitForURL(`**/domain/instruments/${target.instrument_id}`)
  const detail: Schemas['InstrumentDetail'] = await (await request.get(`${API}/api/v1/instruments/${target.instrument_id}`, { headers: steward })).json()
  const version = detail.versions.find((v) => v.import_id === importId)
  expect(version).toBeTruthy()
  await expect(page.getByTestId('instrument-id')).toHaveText(detail.instrument_id)
  await expect(page.getByTestId('instrument-version')).toHaveCount(detail.versions.length)
  const card = page.locator(`[data-testid="instrument-version"][data-version-id="${version!.version_id}"]`)
  await expect(card).toHaveAttribute('data-import-id', importId)
  await expect(card.getByTestId('version-source-ordinal')).toHaveText(String(version!.source_ordinal))
  await expect(card.getByTestId('version-canonical-record')).toHaveText(version!.canonical_record_id)
  await expect(card.getByTestId('version-attribute')).toHaveCount(version!.attributes.length)
  // The steward is cleared for `notional`: the exact decimal text the API sent.
  expect(typeof version!.notional).toBe('string')
  // Grouped by thousands (P-7); data-value keeps the server's exact text.
  await expect(card.getByTestId('version-notional')).toHaveText(groupDecimal(version!.notional as string))
  await expect(card.getByTestId('version-notional')).toHaveAttribute('data-value', version!.notional as string)
  if (process.env.DOMAIN_SCREENSHOT) await page.screenshot({ path: `${process.env.DOMAIN_SCREENSHOT}/instrument.png`, fullPage: true })

  // 3. Lineage: the version's import link opens that import.
  await card.getByTestId('version-import-link').click()
  await page.waitForURL(`**/ingestion/imports/${importId}`)
  await expect(page.getByTestId('import-id')).toHaveText(importId)

  // 4. risk_analyst: the same version, with `notional` masked by the server and shown as "masked".
  const analyst = await browser.newPage()
  await signIn(analyst, 'Risk analyst')
  const loaded = analyst.waitForResponse((r) => r.request().method() === 'GET' && r.url() === `${API}/api/v1/instruments/${target.instrument_id}`)
  await analyst.goto(`/domain/instruments/${target.instrument_id}`)
  const seen: Schemas['InstrumentDetail'] = await (await loaded).json()
  const masked = seen.versions.find((v) => v.version_id === version!.version_id)
  expect(masked?.notional).toEqual({ masked: true, classification: expect.any(String) })
  const notional = analyst.locator(`[data-testid="instrument-version"][data-version-id="${version!.version_id}"]`).getByTestId('version-notional')
  await expect(notional).toHaveAttribute('data-masked', 'true')
  await expect(notional).toHaveText('masked')
  if (process.env.DOMAIN_SCREENSHOT) await analyst.screenshot({ path: `${process.env.DOMAIN_SCREENSHOT}/instrument-analyst.png`, fullPage: true })
})
