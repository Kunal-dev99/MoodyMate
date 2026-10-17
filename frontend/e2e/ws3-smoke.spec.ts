import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { expect, test, type APIRequestContext, type Page } from '@playwright/test'
import type { components } from '../src/api/schema'
import { API, SAMPLES, bearer, ensureActiveMapping, signIn, uploadSample } from './support'

/**
 * Feature 03 Phase 18, FE-18.2 (and the Playwright half of FE-18.1): the WS3 smoke, one serial
 * journey against the real stack (backend ENV=test, synthetic samples only), in the plan's order:
 *   1. data_steward projects the instrument, position and cash-flow imports (browser);
 *   2. the steward browses the Financial Domain catalogue to an instrument of that import;
 *   3. risk_analyst builds the dataset snapshot; the PLANNED modules (alm_irrbb, collateral,
 *      capital, BE-18.1) are disabled "Planned — extension point" entries that cannot be
 *      selected, and a direct POST of one is refused (409 module_not_available) (FE-18.1);
 *   4. the analyst runs the maturity ladder, and (Phase 17) LCR with its liquidity view;
 *   5. the ladder, and a drill-down from a bucket to a contribution's source row;
 *   6. (Phase 16, FE-16.2) the analyst proposes a bucket change and the approver approves it;
 *   7. (Phase 16) a new run differs, and the old run is unchanged;
 *   8. (Phase 16 FE-16.1, FE-18.1) the module registry lists the PLANNED modules with the
 *      PlannedModuleBadge and their contracts, never as ready.
 *
 * Step 4b needs the Phase 17 liquidity view. Until that is merged into this branch it skips
 * itself on a feature check (no AVAILABLE lcr_hqla module), with a TODO naming its phase: it is
 * never silently green.
 *
 * Every payload type is the generated one (src/api/schema.d.ts); none is written here.
 *
 * Ordering: this file sorts after smoke.spec, whose applied proposal moves the `instrument`
 * schema past v1, which projector v1 refuses (`projection_unsupported`). The position and
 * cash-flow imports are always this journey's own; the instrument import is too while
 * `instrument` is still at v1 (a fresh database, or this file run alone). Once the schema has
 * moved on, the journey uses the newest instrument import already projected in this run (by
 * domain.spec or calculations.spec), and step 1 then shows its existing projection (200).
 */

type Schemas = components['schemas']
type AuthHeaders = Record<string, string>

test.describe.configure({ mode: 'serial' })

const REPORTING_DATE = '2026-12-31'
const LADDER = 'maturity_ladder'
/** BE-18.1: the declared extension points, seeded PLANNED. */
const PLANNED_CODES = ['alm_irrbb', 'collateral', 'capital'] as const
/** FE-17.1's module code (Phase 17). */
const LCR = 'lcr_hqla'
const XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
/** The header row of samples/synthetic/position.xlsx (the backend's `validated_sample` reads it with the XLSX adapter). */
const POSITION_COLUMNS = ['position_id', 'instrument_id', 'account_id', 'legal_entity_id', 'as_of_date', 'quantity', 'balance', 'currency', 'valuation_date']

const isPost = (page: Page, url: string) => page.waitForResponse((r) => r.request().method() === 'POST' && r.url() === url)
const isGet = (page: Page, url: string) => page.waitForResponse((r) => r.request().method() === 'GET' && r.url() === url)

// --- setup through the API (not under test) ----------------------------------------------------

/** A new source whose contract copies the seeded core_banking one (it fits instrument.csv). */
async function registerInstrumentSource(request: APIRequestContext, steward: AuthHeaders, stamp: number) {
  const list: Schemas['SourcePage'] = await (await request.get(`${API}/api/v1/sources?limit=200`, { headers: steward })).json()
  const core = list.items.find((s) => s.code === 'core_banking')
  expect(core?.current_contract).toBeTruthy()
  const contract = core!.current_contract!
  const profile = core!.profile
  const body: Schemas['SourceCreate'] = {
    code: `e2e_ws3_instr_${stamp}`,
    name: 'WS3 smoke instruments (e2e)',
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

/** Validate an import as the steward (COMPLETED or with warnings); it is projected in the browser. */
async function validate(request: APIRequestContext, steward: AuthHeaders, importId: string): Promise<Schemas['ImportDetail']> {
  const r = await request.post(`${API}/api/v1/imports/${importId}/validate`, { headers: steward })
  expect(r.status(), await r.text()).toBe(200)
  const job: Schemas['ImportDetail'] = await r.json()
  expect(['COMPLETED', 'COMPLETED_WITH_WARNINGS']).toContain(job.status)
  return job
}

/**
 * The validated instrument import of this journey: its own (instrument.csv on a copy of
 * core_banking's contract) while `instrument` is at v1, else the newest one already projected.
 */
async function instrumentImport(request: APIRequestContext, steward: AuthHeaders, stamp: number): Promise<{ id: string; own: boolean }> {
  const schemas: Schemas['SchemaList'] = await (await request.get(`${API}/api/v1/schemas`, { headers: steward })).json()
  const schema = schemas.items.find((s) => s.code === 'instrument')
  expect(schema?.effective_version).toBeTruthy()
  if (schema!.effective_version!.version === 1) {
    const { sourceId, contractId } = await registerInstrumentSource(request, steward, stamp)
    const uploaded: Schemas['ImportDetail'] = await uploadSample(request, steward, sourceId, 'instrument.csv')
    expect(uploaded.schema_version).toBe(1)
    await ensureActiveMapping(request, contractId, sourceId, uploaded.id, `e2e_ws3_instr_${stamp}`)
    await validate(request, steward, uploaded.id)
    return { id: uploaded.id, own: true }
  }
  const r = await request.get(`${API}/api/v1/imports?projected=true&schema_code=instrument&limit=1&offset=0`, { headers: steward })
  expect(r.status()).toBe(200)
  const page: Schemas['ImportPage'] = await r.json()
  expect(page.items.length, 'instrument is past v1 (smoke.spec): an instrument import projected earlier in this run (domain.spec, calculations.spec) is needed').toBe(1)
  return { id: page.items[0].id, own: false }
}

/**
 * A synthetic position (XLSX) or cash-flow (JSON) sample on its own source (the file's columns,
 * its business key) with a mapping of every column to the same-named field of the schema
 * version the import pins (decimals and dates parsed), made ACTIVE against it, then validated
 * (as the backend's `validated_sample` helper and calculations.spec do).
 */
async function sampleImport(
  request: APIRequestContext,
  steward: AuthHeaders,
  stamp: number,
  spec: { schemaCode: 'position' | 'cashflow_event'; file: string; mimeType: string; format: Schemas['SourceFormat']; key: string; columns: string[] },
) {
  const schemas: Schemas['SchemaList'] = await (await request.get(`${API}/api/v1/schemas`, { headers: steward })).json()
  const schema = schemas.items.find((s) => s.code === spec.schemaCode)
  expect(schema).toBeTruthy()
  const body: Schemas['SourceCreate'] = {
    code: `e2e_ws3_${spec.schemaCode}_${stamp}`,
    name: `WS3 smoke ${spec.schemaCode} (e2e)`,
    kind: 'FILE_DROP',
    profile: { format: spec.format, encoding: 'UTF-8', owner: 'Treasury data team (e2e)', default_classification: 'confidential' },
    contract: {
      schema_id: schema!.id,
      expected_columns: spec.columns.map((name) => ({ name, required: true })),
      business_key_columns: [spec.key],
      control_total_columns: [],
    },
  }
  const created = await request.post(`${API}/api/v1/sources`, { headers: steward, data: body })
  expect(created.status(), await created.text()).toBe(201)
  const source: Schemas['SourceDetail'] = await created.json()
  const uploaded: Schemas['ImportDetail'] = await uploadSample(request, steward, source.id, spec.file, spec.mimeType)
  expect(uploaded.schema_code).toBe(spec.schemaCode)

  const versions: Schemas['SchemaVersionList'] = await (await request.get(`${API}/api/v1/schemas/${schema!.id}/versions`, { headers: steward })).json()
  const pinned = versions.items.find((v) => v.id === uploaded.schema_version_id)
  expect(pinned, 'the import pins a listed schema version').toBeTruthy()
  const types = new Map(pinned!.fields.map((f) => [f.name, f.data_type]))
  const rules: Schemas['RuleIn'][] = spec.columns.map((column) => {
    const type = types.get(column)
    expect(type, `${spec.schemaCode} v${pinned!.version} has a field ${column}`).toBeTruthy()
    let transform: Schemas['RuleIn']['transform'] = { code: 'COPY', params: {} }
    if (type === 'DECIMAL') transform = { code: 'PARSE_DECIMAL' }
    else if (type === 'DATE') transform = { code: 'PARSE_DATE', params: { format: 'YYYY-MM-DD' } }
    return { source_column: column, target_field: column, column_policy: 'MAP', null_policy: 'ALLOW_NULL', transform }
  })
  const mapping: Schemas['MappingCreate'] = {
    code: `e2e_ws3_${spec.schemaCode}_${stamp}`,
    name: `WS3 smoke ${spec.schemaCode} (e2e)`,
    source_contract_id: source.current_contract!.id,
    rules,
  }
  const m = await request.post(`${API}/api/v1/mappings`, { headers: steward, data: mapping })
  expect(m.status(), await m.text()).toBe(201)
  const version: Schemas['MappingVersionOut'] = (await m.json()).versions[0]
  const dry = await request.post(`${API}/api/v1/imports/${uploaded.id}/mappings/validate`, { headers: steward, data: { mapping_version_id: version.id } })
  expect(dry.status(), await dry.text()).toBe(201)
  expect((await dry.json()).ok).toBe(true)
  const base = `${API}/api/v1/mappings/${version.mapping_id}/versions/${version.version}`
  expect((await request.post(`${base}/submit`, { headers: steward, data: {} })).status()).toBe(200)
  const approved = await request.post(`${base}/approval`, { headers: await bearer(request, 'approver'), data: { decision: 'APPROVE' } })
  expect(approved.status(), await approved.text()).toBe(200)
  await validate(request, steward, uploaded.id)
  return uploaded.id
}

// --- browser helpers ------------------------------------------------------------------------------

/** On /calculations/new: pick one import per schema and build (or reuse) the snapshot. */
async function buildSnapshotInBrowser(page: Page, picks: [string, string][]) {
  for (const [code, id] of picks) {
    const pick = page.getByTestId(`snapshot-pick-${code}`)
    await expect(pick.locator(`option[value="${id}"]`)).toHaveCount(1)
    await pick.selectOption(id)
  }
  const built = isPost(page, `${API}/api/v1/dataset-snapshots`)
  await page.getByTestId('snapshot-build').click()
  const response = await built
  expect([200, 201], await response.text()).toContain(response.status())
  const snapshot: Schemas['SnapshotOut'] = await response.json()
  expect(snapshot.members.map((m) => m.import_id).sort()).toEqual(picks.map(([, id]) => id).sort())
  await expect(page.getByTestId('snapshot-result')).toHaveAttribute('data-snapshot-id', snapshot.snapshot_id)
  await expect(page.getByTestId('snapshot-result')).toHaveAttribute('data-origin', response.status() === 201 ? 'created' : 'reused')
  return { snapshot, status: response.status() }
}

/** Pick an AVAILABLE module and the date, submit, and open the run the server wrote. */
async function submitRunInBrowser(page: Page, moduleVersionId: string): Promise<Schemas['RunDetail']> {
  await page.locator(`[data-testid="module-option"][data-module-version-id="${moduleVersionId}"]`).getByTestId('module-radio').check()
  await page.getByTestId('reporting-date').fill(REPORTING_DATE)
  await expect(page.getByTestId('pins-preview').locator('[data-testid="pin-row"][data-missing="true"]')).toHaveCount(0)
  const submitted = isPost(page, `${API}/api/v1/calculation-runs`)
  await page.getByTestId('run-submit').click()
  const response = await submitted
  expect(response.status(), await response.text()).toBe(201)
  const run: Schemas['RunDetail'] = await response.json()
  // Never shown as a success before the server says so: the page opens in the server's status.
  await page.waitForURL(`**/calculations/runs/${run.run_id}`)
  await expect(page.getByTestId('run-detail-status')).toHaveAttribute('data-status', run.status)
  await expect(page.getByTestId('run-id')).toHaveText(run.run_id)
  await expect(page.getByTestId('run-reporting-date')).toHaveText(REPORTING_DATE)
  await expect(page.getByTestId('illustrative-banner')).toContainText(run.statement)
  return run
}

test('FE-18.2: WS3 smoke: project → catalogue → snapshot (PLANNED not selectable) → run → ladder → drill-down to the source row → rule change → new run differs', async ({
  browser,
  request,
}) => {
  test.setTimeout(300_000)
  const stamp = Date.now()
  const stewardApi = await bearer(request, 'data_steward')
  const analystApi = await bearer(request, 'risk_analyst')
  const approverApi = await bearer(request, 'approver')

  // Setup (not under test): three validated synthetic imports, not yet projected.
  const instrument = await instrumentImport(request, stewardApi, stamp)
  const positionId = await sampleImport(request, stewardApi, stamp, {
    schemaCode: 'position',
    file: 'position.xlsx',
    mimeType: XLSX,
    format: 'XLSX',
    key: 'position_id',
    columns: POSITION_COLUMNS,
  })
  const cashflowSample: { records: Record<string, unknown>[] } = JSON.parse(readFileSync(join(SAMPLES, 'cashflow_event.json'), 'utf8'))
  const cashflowId = await sampleImport(request, stewardApi, stamp, {
    schemaCode: 'cashflow_event',
    file: 'cashflow_event.json',
    mimeType: 'application/json',
    format: 'JSON',
    key: 'cashflow_id',
    columns: Object.keys(cashflowSample.records[0]),
  })
  const picks: [string, string][] = [
    ['instrument', instrument.id],
    ['position', positionId],
    ['cashflow_event', cashflowId],
  ]

  const modules: Schemas['ModulePage'] = await (await request.get(`${API}/api/v1/calculation-modules`, { headers: analystApi })).json()
  const ladder = modules.items.find((m) => m.code === LADDER && m.implementation_status === 'AVAILABLE')
  expect(ladder?.runnable, 'maturity_ladder is seeded AVAILABLE with every rule ACTIVE').toBe(true)

  const steward = await browser.newPage()
  await signIn(steward, 'Data steward')
  const analyst = await browser.newPage()
  await signIn(analyst, 'Risk analyst')
  const approver = await browser.newPage()
  await signIn(approver, 'Approver')

  let snapshot: Schemas['SnapshotOut'] | null = null
  let run: Schemas['RunDetail'] | null = null

  await test.step('1. the steward projects the instrument, position and cash-flow imports', async () => {
    for (const [code, id] of picks) {
      await steward.goto(`/ingestion/imports/${id}`)
      await expect(steward.getByTestId('import-id')).toHaveText(id)
      const job: Schemas['ImportDetail'] = await (await request.get(`${API}/api/v1/imports/${id}`, { headers: stewardApi })).json()
      const accepted = job.manifest!.quality.accepted_records
      expect(accepted, `${code}: accepted records to project`).toBeGreaterThan(0)
      const posted = isPost(steward, `${API}/api/v1/imports/${id}/projection`)
      await steward.getByTestId('domain-project').click()
      const response = await posted
      // 201: projected now. 200: only the reused instrument import (already projected, nothing written).
      const fresh = code !== 'instrument' || instrument.own
      expect(response.status(), await response.text()).toBe(fresh ? 201 : 200)
      const projection: Schemas['ProjectionOut'] = await response.json()
      expect(projection.projected_count).toBe(accepted)
      const summary = steward.getByTestId('projection-summary')
      await expect(summary).toHaveAttribute('data-projection-id', projection.projection_id)
      await expect(summary).toHaveAttribute('data-origin', fresh ? 'created' : 'replayed')
      await expect(steward.getByTestId('projection-projected')).toHaveText(String(accepted))
      await expect(steward.getByTestId('projection-conserves')).toHaveAttribute('data-conserves', 'true')
    }
  })

  await test.step('2. the steward browses the catalogue to an instrument of that import', async () => {
    await steward.goto('/domain')
    const filtered = steward.waitForResponse(
      (r) => r.request().method() === 'GET' && r.url().startsWith(`${API}/api/v1/instruments?`) && r.url().includes(`import_id=${instrument.id}`),
    )
    await steward.getByTestId('filter-import').fill(instrument.id)
    await steward.getByTestId('filter-apply').click()
    const listed: Schemas['InstrumentPage'] = await (await filtered).json()
    expect(listed.total).toBeGreaterThan(0)
    await expect(steward.getByTestId('instrument-table')).toHaveAttribute('data-total', String(listed.total))
    const target = listed.items[0]
    await steward.locator(`[data-testid="instrument-row"][data-id="${target.instrument_id}"]`).getByTestId('instrument-link').click()
    await steward.waitForURL(`**/domain/instruments/${target.instrument_id}`)
    const detail: Schemas['InstrumentDetail'] = await (await request.get(`${API}/api/v1/instruments/${target.instrument_id}`, { headers: stewardApi })).json()
    const version = detail.versions.find((v) => v.import_id === instrument.id)
    expect(version).toBeTruthy()
    const card = steward.locator(`[data-testid="instrument-version"][data-version-id="${version!.version_id}"]`)
    await expect(card).toHaveAttribute('data-import-id', instrument.id)
    await expect(card.getByTestId('version-source-ordinal')).toHaveText(String(version!.source_ordinal))
  })

  await test.step('3. the analyst builds the snapshot; PLANNED modules are disabled and a direct POST is refused (FE-18.1)', async () => {
    await analyst.goto('/calculations')
    await analyst.getByTestId('new-run').click()
    await analyst.waitForURL('**/calculations/new')
    const built = await buildSnapshotInBrowser(analyst, picks)
    snapshot = built.snapshot
    await expect(analyst.getByTestId('snapshot-result').getByTestId('snapshot-member')).toHaveCount(picks.length)

    // BE-18.1's seeds, as the server lists them: PLANNED, never runnable.
    const planned = modules.items.filter((m) => m.implementation_status === 'PLANNED')
    expect(planned.map((m) => m.code)).toEqual(expect.arrayContaining([...PLANNED_CODES]))
    for (const m of planned) {
      expect(m.runnable).toBe(false)
      const option = analyst.locator(`[data-testid="module-option"][data-module-version-id="${m.module_version_id}"]`)
      await expect(option).toHaveAttribute('data-status', 'PLANNED')
      await expect(option).toHaveAttribute('aria-disabled', 'true')
      await expect(option.getByTestId('planned-module-badge')).toHaveText('Planned — extension point')
      await expect(option.getByTestId('module-implementation')).toHaveCount(0)
      await expect(option.getByTestId('module-planned-reason')).toContainText('module_not_available')
      // Its declared contracts, as the server sent them.
      await expect(option.getByTestId('contract-rules')).toHaveText(m.input_contract.rules.length ? m.input_contract.rules.join(', ') : '—')
      await expect(option.getByTestId('contract-results')).toHaveText(m.output_contract.results.length ? m.output_contract.results.join(', ') : '—')
      // It cannot be selected: the radio is disabled, and a click on the entry picks nothing.
      const radio = option.getByTestId('module-radio')
      await expect(radio).toBeDisabled()
      await option.locator('label').click({ force: true })
      await expect(radio).not.toBeChecked()
      await expect(analyst.getByTestId('pins-empty')).toBeVisible()

      // The disabled entry is cosmetic: the server refuses a run of it (D-04).
      const body: Schemas['RunCreate'] = { dataset_snapshot_id: snapshot!.snapshot_id, module_version_id: m.module_version_id, reporting_date: REPORTING_DATE }
      const refused = await request.post(`${API}/api/v1/calculation-runs`, {
        headers: { ...analystApi, 'Idempotency-Key': `e2e-ws3-planned-${m.code}-${stamp}` },
        data: body,
      })
      expect(refused.status(), await refused.text()).toBe(409)
      const envelope: Schemas['ErrorEnvelope'] = await refused.json()
      expect(envelope.code).toBe('module_not_available')
    }
    if (process.env.WS3_SMOKE_SCREENSHOT) await analyst.screenshot({ path: `${process.env.WS3_SMOKE_SCREENSHOT}/new-run-planned.png`, fullPage: true })
  })

  await test.step('4. the analyst runs the maturity ladder on the snapshot', async () => {
    const pins = analyst.getByTestId('pins-preview')
    await analyst.locator(`[data-testid="module-option"][data-module-version-id="${ladder!.module_version_id}"]`).getByTestId('module-radio').check()
    await expect(pins.locator(`[data-testid="pin-row"][data-kind="DATASET_SNAPSHOT"][data-id="${snapshot!.snapshot_id}"]`)).toHaveCount(1)
    run = await submitRunInBrowser(analyst, ladder!.module_version_id)
    expect(['SUCCEEDED', 'SUCCEEDED_WITH_WARNINGS']).toContain(run.status)
    expect(run.dataset_snapshot_id).toBe(snapshot!.snapshot_id)
    // Every pinned configuration version is on the run page, as the server wrote it.
    await expect(analyst.getByTestId('manifest-sha256')).toHaveText(run.manifest_sha256)
    await expect(analyst.getByTestId('manifest-rule-set-id')).toHaveText(run.manifest.rule_set.rule_set_id)
    await expect(analyst.getByTestId('manifest-snapshot-id')).toHaveText(snapshot!.snapshot_id)
    await expect(analyst.getByTestId('manifest-rule')).toHaveCount(run.manifest.rule_set.members.length)
  })

  await test.step('4b. the analyst runs LCR and sees the liquidity view (Phase 17)', async (step) => {
    const lcr = modules.items.find((m) => m.code === LCR && m.implementation_status === 'AVAILABLE')
    // TODO(Phase 17, FE-17.1): enable after the merge; confirm the liquidity view's test IDs.
    step.skip(!lcr, `Phase 17 is not on this branch: no AVAILABLE ${LCR} module`)
    await analyst.goto('/calculations/new')
    // The same members: the content-addressed snapshot is reused, nothing new is written.
    const again = await buildSnapshotInBrowser(analyst, picks)
    expect(again.status).toBe(200)
    expect(again.snapshot.snapshot_id).toBe(snapshot!.snapshot_id)
    const lcrRun = await submitRunInBrowser(analyst, lcr!.module_version_id)
    expect(['SUCCEEDED', 'SUCCEEDED_WITH_WARNINGS']).toContain(lcrRun.status)
    const view = analyst.getByTestId('liquidity-view')
    await expect(view).toBeVisible()
    await expect(view.getByTestId('identity-run-id')).toHaveText(lcrRun.run_id)
    await expect(view.getByTestId('identity-reporting-date')).toHaveText(REPORTING_DATE)
    await expect(analyst.getByTestId('illustrative-banner')).toBeVisible()
  })

  await test.step('5. the ladder, and a drill-down from a bucket to a contribution’s source row', async () => {
    // The approver is cleared for the contributions (D-33), so the drill shows their values.
    const runId = run!.run_id
    const ladderRead = isGet(approver, `${API}/api/v1/calculation-runs/${runId}/maturity-ladder`)
    await approver.goto(`/calculations/runs/${runId}`)
    const shown: Schemas['MaturityLadderOut'] = await (await ladderRead).json()
    await expect(approver.getByTestId('maturity-ladder').getByTestId('identity-run-id')).toHaveText(runId)
    await expect(approver.getByTestId('maturity-ladder').getByTestId('identity-reporting-date')).toHaveText(REPORTING_DATE)
    await expect(approver.getByTestId('ladder-currency')).toHaveCount(shown.currencies.length)
    const target = shown.currencies.flatMap((c) => c.rows.filter((r) => r.contribution_count > 0).map((r) => ({ currency: c.currency, row: r })))[0]
    expect(target, 'the ladder has a bucket with contributions').toBeTruthy()
    const row = approver.locator(`[data-testid="ladder-row"][data-currency="${target.currency}"][data-bucket="${target.row.bucket_code}"]`)
    await expect(row.getByTestId('ladder-count')).toHaveText(String(target.row.contribution_count))
    if (typeof target.row.inflows === 'string') await expect(row.getByTestId('ladder-inflows')).toHaveText(target.row.inflows)
    if (typeof target.row.outflows === 'string') await expect(row.getByTestId('ladder-outflows')).toHaveText(target.row.outflows)

    const resultsRead = approver.waitForResponse(
      (r) =>
        r.request().method() === 'GET' &&
        r.url().startsWith(`${API}/api/v1/calculation-runs/${runId}/results?`) &&
        new URL(r.url()).searchParams.get('bucket_code') === target.row.bucket_code &&
        new URL(r.url()).searchParams.get('currency') === target.currency,
    )
    await row.getByTestId('ladder-drill').click()
    const results: Schemas['ResultPage'] = await (await resultsRead).json()
    expect(results.total).toBe(target.row.contribution_count)
    const drill = approver.getByTestId('contributions')
    await expect(drill.getByTestId('contribution')).toHaveCount(target.row.contribution_count)
    // Each contribution is traced to the snapshot's cash-flow import and one of its source rows.
    for (const item of results.items) expect(item.lineage.import_id).toBe(cashflowId)

    const first = results.items[0]
    const link = drill.locator(`[data-testid="contribution"][data-id="${first.intermediate_value_id}"]`).getByTestId('contribution-import-link')
    await expect(link).toContainText(`row ${first.lineage.source_ordinal}`)
    await link.click()
    await approver.waitForURL(`**/ingestion/imports/${first.lineage.import_id}`)
    await expect(approver.getByTestId('import-id')).toHaveText(cashflowId)
    if (process.env.WS3_SMOKE_SCREENSHOT) await approver.screenshot({ path: `${process.env.WS3_SMOKE_SCREENSHOT}/source-import.png`, fullPage: true })
  })

  // Phase 16: the rule registry and its maker-checker flow (FE-16.2).
  const bucketsRule = ladder!.rules.find((r) => r.code === 'maturity_buckets')
  const oldBuckets = run!.manifest.rule_set.members.find((r) => r.rule_code === 'maturity_buckets')
  let newBucketsVersionId = ''

  await test.step('6. the analyst proposes a bucket change and the approver approves it (Phase 16, FE-16.2)', async () => {
    expect(bucketsRule && oldBuckets).toBeTruthy()
    const ruleId = bucketsRule!.rule_id
    const versionsUrl = `${API}/api/v1/rules/${ruleId}/versions`
    // The run of step 4 pinned the ACTIVE version: the proposal starts from it.
    const before: Schemas['RuleDetail'] = await (await request.get(`${API}/api/v1/rules/${ruleId}`, { headers: analystApi })).json()
    expect(before.change_enabled, 'RULE_CHANGE_ENABLED is on').toBe(true)
    expect(before.active_rule_version_id).toBe(oldBuckets!.rule_version_id)

    // The analyst narrows the third bucket: a new upper bound strictly inside it.
    await analyst.goto(`/domain/rules/${ruleId}/propose`)
    const editor = analyst.getByTestId('parameter-editor')
    await expect(editor).toHaveAttribute('data-kind', 'DAY_BUCKETS')
    await expect(analyst.getByTestId('rule-propose')).toHaveAttribute('data-base-version', String(oldBuckets!.version))
    await expect(analyst.getByTestId('propose-submit')).toBeDisabled()
    const third = editor.locator('[data-testid="bucket-row"][data-index="2"]')
    const fourth = editor.locator('[data-testid="bucket-row"][data-index="3"]')
    const upperInput = third.getByTestId('bucket-upper')
    const lower = Number(await third.getByTestId('bucket-lower').innerText())
    const upper = Number(await upperInput.inputValue())
    const boundary = Math.floor((lower + upper) / 2)
    expect(boundary).toBeGreaterThan(lower)
    expect(boundary).toBeLessThan(upper)
    await upperInput.fill(String(boundary))
    // Contiguous by construction: the fourth bucket now starts where the third ends.
    await expect(fourth.getByTestId('bucket-lower')).toHaveText(String(boundary))
    await expect(third.getByTestId('bucket-label')).toHaveText(`(${lower},${boundary}]`)
    await analyst.getByTestId('change-summary').fill(`WS3 smoke: the third bucket ends at day ${boundary} (${stamp})`)
    await expect(analyst.getByTestId('parameter-problem')).toHaveCount(0)
    const proposedResponse = isPost(analyst, versionsUrl)
    await analyst.getByTestId('propose-submit').click()
    const proposedAnswer = await proposedResponse
    expect(proposedAnswer.status(), await proposedAnswer.text()).toBe(201)
    const proposed: Schemas['RuleVersionOut'] = await proposedAnswer.json()
    expect(proposed.status).toBe('DRAFT')
    expect(proposed.version).toBeGreaterThan(oldBuckets!.version)
    newBucketsVersionId = proposed.rule_version_id
    await expect(analyst.getByTestId('propose-result')).toHaveAttribute('data-version', String(proposed.version))

    // Submit the DRAFT from the open versions.
    const mine = analyst.locator(`[data-testid="open-version"][data-version="${proposed.version}"]`)
    await expect(mine).toHaveAttribute('data-status', 'DRAFT')
    const submitted = isPost(analyst, `${versionsUrl}/${proposed.version}/submit`)
    await mine.getByTestId('version-submit').click()
    const submittedAnswer = await submitted
    expect(submittedAnswer.status(), await submittedAnswer.text()).toBe(200)
    await expect(mine).toHaveAttribute('data-status', 'IN_REVIEW')
    // Maker-checker: the author has no Approve button, and is told why.
    await expect(mine.getByTestId('version-approve')).toHaveCount(0)
    await expect(mine.getByTestId('self-approval-blocked')).toBeVisible()

    await approver.goto(`/domain/rules/${ruleId}`)
    const review = approver.locator(`[data-testid="open-version"][data-version="${proposed.version}"]`)
    await expect(review).toHaveAttribute('data-status', 'IN_REVIEW')
    await expect(review).toHaveAttribute('data-mine', 'false')
    await review.getByTestId('version-approve').click()
    const dialog = approver.getByTestId('decision-dialog')
    await expect(dialog).toHaveAttribute('data-decision', 'APPROVE')
    await dialog.getByTestId('decision-comment').fill(`WS3 smoke: the third bucket ends at day ${boundary} (${stamp}).`)
    const decided = isPost(approver, `${versionsUrl}/${proposed.version}/approval`)
    await dialog.getByTestId('decision-confirm').click()
    const decision = await decided
    expect(decision.status(), await decision.text()).toBe(200)
    const approved: Schemas['RuleVersionOut'] = await decision.json()
    expect(approved.status).toBe('ACTIVE')
    expect(approved.rule_version_id).toBe(newBucketsVersionId)
    await expect(approver.getByTestId('rule-step-result')).toHaveAttribute('data-status', 'ACTIVE')
  })

  await test.step('7. a new run differs, and the old run is unchanged (Phase 16)', async () => {
    expect(newBucketsVersionId).not.toBe('')
    const old = run!
    await analyst.goto('/calculations/new')
    const again = await buildSnapshotInBrowser(analyst, picks)
    expect(again.snapshot.snapshot_id).toBe(snapshot!.snapshot_id)
    const next = await submitRunInBrowser(analyst, ladder!.module_version_id)
    expect(['SUCCEEDED', 'SUCCEEDED_WITH_WARNINGS']).toContain(next.status)
    // The new run pins the approved version: its rule set and manifest differ.
    const pinned = next.manifest.rule_set.members.find((r) => r.rule_code === 'maturity_buckets')
    expect(pinned?.rule_version_id).toBe(newBucketsVersionId)
    expect(next.rule_set_id).not.toBe(old.rule_set_id)
    expect(next.manifest_sha256).not.toBe(old.manifest_sha256)
    await expect(analyst.locator('[data-testid="manifest-rule"][data-rule="maturity_buckets"]')).toHaveAttribute('data-rule-version-id', newBucketsVersionId)

    // The old run is exactly as it was written: same manifest, same results, the old version shown.
    const reread: Schemas['RunDetail'] = await (await request.get(`${API}/api/v1/calculation-runs/${old.run_id}`, { headers: approverApi })).json()
    expect(reread.status).toBe(old.status)
    expect(reread.manifest_sha256).toBe(old.manifest_sha256)
    expect(reread.result_sha256).toBe(old.result_sha256)
    expect(reread.manifest.rule_set).toEqual(old.manifest.rule_set)
    await analyst.goto(`/calculations/runs/${old.run_id}`)
    await expect(analyst.getByTestId('manifest-sha256')).toHaveText(old.manifest_sha256)
    await expect(analyst.locator('[data-testid="manifest-rule"][data-rule="maturity_buckets"]')).toHaveAttribute('data-rule-version-id', oldBuckets!.rule_version_id)
  })

  await test.step('8. the module registry lists the PLANNED modules as planned, never as ready (Phase 16, FE-18.1)', async () => {
    const planned = modules.items.filter((x) => x.implementation_status === 'PLANNED')
    await analyst.goto('/domain/modules')
    await expect(analyst.getByTestId('modules-PLANNED').getByTestId('registry-module')).toHaveCount(planned.length)
    for (const m of planned) {
      const entry = analyst.locator(`[data-testid="registry-module"][data-module-version-id="${m.module_version_id}"]`)
      await expect(entry).toHaveAttribute('data-status', 'PLANNED')
      await expect(entry.getByTestId('planned-module-badge')).toHaveText('Planned — extension point')
      await expect(entry.getByTestId('module-implementation')).toHaveCount(0)
      await expect(entry.getByTestId('module-runnable')).toHaveCount(0)
      await expect(entry.getByTestId('module-planned-note')).toContainText('not production-ready')
      // Its declared contracts, as the server sent them.
      await expect(entry.getByTestId('contract-rules')).toHaveText(m.input_contract.rules.length ? m.input_contract.rules.join(', ') : '—')
      await expect(entry.getByTestId('contract-results')).toHaveText(m.output_contract.results.length ? m.output_contract.results.join(', ') : '—')
      await expect(entry.getByText(/Available|Runnable now/)).toHaveCount(0)
    }
    if (process.env.WS3_SMOKE_SCREENSHOT) await analyst.screenshot({ path: `${process.env.WS3_SMOKE_SCREENSHOT}/registry-planned.png`, fullPage: true })
  })
})
