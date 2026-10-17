import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { expect, test, type APIRequestContext, type Page } from '@playwright/test'
import type { components } from '../src/api/schema'
import { API, SAMPLES, bearer, ensureActiveMapping, signIn, uploadSample } from './support'

/**
 * Feature 03 Phase 15 browser checks against the real stack (backend ENV=test, synthetic).
 * FE-15.1: risk_analyst picks the projected instrument and cash-flow imports, builds the
 *          dataset snapshot, picks maturity_ladder v1 and a reporting date, sees the versions
 *          the run will pin (as GET /calculation-modules reports them) before submitting, and
 *          submits a run that the server ends SUCCEEDED or SUCCEEDED_WITH_WARNINGS. The
 *          approver has no New-run button and the server refuses its direct POSTs (403).
 * FE-15.2: the approver (cleared for the contributions, D-33) opens the run: the manifest, the
 *          warnings and the ladder are the server's; one bucket's displayed totals equal the
 *          sum of its drill-down rows, and a row is followed to its import. risk_analyst sees the
 *          same bucket's contributions "masked" and its aggregates in clear (D-33).
 *
 * Every payload type is the generated one (src/api/schema.d.ts); none is written here.
 * Decimal text is compared exactly (scaled integers), never as floats.
 *
 * Ordering: this file sorts before smoke.spec, whose applied proposal moves the `instrument`
 * schema past v1, which projector v1 refuses. It registers its own sources (instrument: a copy
 * of core_banking's contract; cash flows: a JSON source for `cashflow_event`) and mappings, so
 * core_banking stays unmapped for mappings.spec. Run it on a fresh database (CI does; locally a
 * disposable copy, D-03).
 */

type Schemas = components['schemas']

test.describe.configure({ mode: 'serial' })

const REPORTING_DATE = '2026-12-31'
const MODULE = 'maturity_ladder'

/** The ladder figures of a row, and where the ladder table shows each (FE-15.2). */
const FIGURE_TEST_IDS = {
  inflows: 'ladder-inflows',
  outflows: 'ladder-outflows',
  net_gap: 'ladder-net',
  cumulative_gap: 'ladder-cumulative',
  inflows_contractual: 'ladder-inflows-contractual',
  outflows_contractual: 'ladder-outflows-contractual',
  inflows_behavioural: 'ladder-inflows-behavioural',
  outflows_behavioural: 'ladder-outflows-behavioural',
} as const satisfies Partial<Record<keyof Schemas['LadderRow'], string>>
const FIGURES = Object.keys(FIGURE_TEST_IDS) as (keyof typeof FIGURE_TEST_IDS)[]

let instrumentImport = ''
let cashflowImport = ''
let run: Schemas['RunDetail'] | null = null

// --- exact decimal text (test arithmetic only: the UI never parses a value) -------------------

function units(text: string): { value: bigint; scale: number } {
  const m = /^(-?)(\d+)(?:\.(\d+))?$/.exec(text.trim())
  expect(m, `"${text}" is exact decimal text`).toBeTruthy()
  const [, sign, whole, frac = ''] = m!
  const value = BigInt(`${whole}${frac}`)
  return { value: sign ? -value : value, scale: frac.length }
}

const TEN = BigInt(10)
const rescale = (u: { value: bigint; scale: number }, scale: number) => u.value * TEN ** BigInt(scale - u.scale)

/** a = b as decimals (1.50 = 1.5). */
function sameDecimal(a: string, b: string): boolean {
  const x = units(a)
  const y = units(b)
  const scale = x.scale > y.scale ? x.scale : y.scale
  return rescale(x, scale) === rescale(y, scale)
}

/** The exact sum of decimal texts, as text at the widest scale. */
function sumDecimals(values: string[]): string {
  const all = values.map(units)
  const scale = all.reduce((s, u) => (u.scale > s ? u.scale : s), 0)
  const total = all.reduce((s, u) => s + rescale(u, scale), BigInt(0))
  const negative = total < BigInt(0)
  const digits = (negative ? -total : total).toString().padStart(scale + 1, '0')
  const text = scale === 0 ? digits : `${digits.slice(0, -scale)}.${digits.slice(-scale)}`
  return negative ? `-${text}` : text
}

// --- setup through the API (not under test) ----------------------------------------------------

/** A new source whose contract copies the seeded core_banking one (it fits instrument.csv). */
async function registerInstrumentSource(request: APIRequestContext, steward: Record<string, string>) {
  const list: Schemas['SourcePage'] = await (await request.get(`${API}/api/v1/sources?limit=200`, { headers: steward })).json()
  const core = list.items.find((s) => s.code === 'core_banking')
  expect(core?.current_contract).toBeTruthy()
  const contract = core!.current_contract!
  const profile = core!.profile
  const body: Schemas['SourceCreate'] = {
    code: `e2e_calc_instr_${Date.now()}`,
    name: 'Calculations instruments (e2e)',
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

/**
 * A JSON source for the synthetic cash-flow sample (its own columns, business key
 * `cashflow_id`), and a mapping of every column to the same-named `cashflow_event` v1 field
 * (decimals and dates parsed), made ACTIVE against the import (as the backend's
 * `validated_sample` helper does).
 */
async function cashflowImportReady(request: APIRequestContext, steward: Record<string, string>) {
  const schemas: Schemas['SchemaList'] = await (await request.get(`${API}/api/v1/schemas`, { headers: steward })).json()
  const schema = schemas.items.find((s) => s.code === 'cashflow_event')
  expect(schema).toBeTruthy()
  const sample: { records: Record<string, unknown>[] } = JSON.parse(readFileSync(join(SAMPLES, 'cashflow_event.json'), 'utf8'))
  const columns = Object.keys(sample.records[0])
  const body: Schemas['SourceCreate'] = {
    code: `e2e_calc_cf_${Date.now()}`,
    name: 'Calculations cash flows (e2e)',
    kind: 'FILE_DROP',
    profile: { format: 'JSON', encoding: 'UTF-8', owner: 'Treasury data team (e2e)', default_classification: 'confidential' },
    contract: {
      schema_id: schema!.id,
      expected_columns: columns.map((name) => ({ name, required: true })),
      business_key_columns: ['cashflow_id'],
      control_total_columns: [],
    },
  }
  const created = await request.post(`${API}/api/v1/sources`, { headers: steward, data: body })
  expect(created.status(), await created.text()).toBe(201)
  const source: Schemas['SourceDetail'] = await created.json()
  const uploaded: Schemas['ImportDetail'] = await uploadSample(request, steward, source.id, 'cashflow_event.json', 'application/json')
  expect(uploaded.schema_code).toBe('cashflow_event')

  const versions: Schemas['SchemaVersionList'] = await (await request.get(`${API}/api/v1/schemas/${schema!.id}/versions`, { headers: steward })).json()
  const pinned = versions.items.find((v) => v.id === uploaded.schema_version_id)
  expect(pinned, 'the import pins a listed schema version').toBeTruthy()
  const types = new Map(pinned!.fields.map((f) => [f.name, f.data_type]))
  const rules: Schemas['RuleIn'][] = columns.map((column) => {
    const type = types.get(column)
    expect(type, `cashflow_event v${pinned!.version} has a field ${column}`).toBeTruthy()
    let transform: Schemas['RuleIn']['transform'] = { code: 'COPY', params: {} }
    if (type === 'DECIMAL') transform = { code: 'PARSE_DECIMAL' }
    else if (type === 'DATE') transform = { code: 'PARSE_DATE', params: { format: 'YYYY-MM-DD' } }
    return { source_column: column, target_field: column, column_policy: 'MAP', null_policy: 'ALLOW_NULL', transform }
  })
  const mapping: Schemas['MappingCreate'] = {
    code: `e2e_calc_cf_${Date.now()}`,
    name: 'Calculations cash flows (e2e)',
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
  return uploaded.id
}

/** Validate (COMPLETED or with warnings) and project an import, as the steward. */
async function validateAndProject(request: APIRequestContext, steward: Record<string, string>, importId: string) {
  const validated = await request.post(`${API}/api/v1/imports/${importId}/validate`, { headers: steward })
  expect(validated.status(), await validated.text()).toBe(200)
  const job: Schemas['ImportDetail'] = await validated.json()
  expect(['COMPLETED', 'COMPLETED_WITH_WARNINGS']).toContain(job.status)
  const projected = await request.post(`${API}/api/v1/imports/${importId}/projection`, { headers: steward })
  expect(projected.status(), await projected.text()).toBe(201)
  const projection: Schemas['ProjectionOut'] = await projected.json()
  expect(projection.projected_count).toBe(job.manifest!.quality.accepted_records)
}

const isPost = (page: Page, url: string) => page.waitForResponse((r) => r.request().method() === 'POST' && r.url() === url)

test('FE-15.1: risk_analyst builds a snapshot, reviews the pins and submits a run that succeeds', async ({ page, request }) => {
  // Setup: a projected synthetic instrument import and a projected synthetic cash-flow import.
  const steward = await bearer(request, 'data_steward')
  const { sourceId, contractId } = await registerInstrumentSource(request, steward)
  const instruments: Schemas['ImportDetail'] = await uploadSample(request, steward, sourceId, 'instrument.csv')
  instrumentImport = instruments.id
  expect(instruments.schema_version, 'projector v1 supports instrument v1 only: run on a fresh database, before smoke.spec').toBe(1)
  await ensureActiveMapping(request, contractId, sourceId, instrumentImport, `e2e_calc_instr_${Date.now()}`)
  await validateAndProject(request, steward, instrumentImport)
  cashflowImport = await cashflowImportReady(request, steward)
  await validateAndProject(request, steward, cashflowImport)

  // What a run submitted now pins, as the server reports it (to check the review against).
  const analystApi = await bearer(request, 'risk_analyst')
  const modules: Schemas['ModulePage'] = await (await request.get(`${API}/api/v1/calculation-modules`, { headers: analystApi })).json()
  const ladder = modules.items.find((m) => m.code === MODULE && m.implementation_status === 'AVAILABLE')
  expect(ladder?.runnable, 'maturity_ladder v1 is seeded AVAILABLE with every rule ACTIVE').toBe(true)

  // 1. The workspace: live (no placeholder), labelled illustrative, with "New run" for the analyst.
  await signIn(page, 'Risk analyst')
  await page.goto('/calculations')
  await expect(page.getByText('This workspace is planned, not built')).toHaveCount(0)
  await expect(page.getByTestId('illustrative-banner')).toContainText('Illustrative prototype — not regulatory')
  await expect(page.getByTestId('illustrative-banner')).toContainText('no parity with any vendor system is claimed')
  await page.getByTestId('new-run').click()
  await page.waitForURL('**/calculations/new')

  // 2. One projected import per schema, then build (or reuse) the snapshot.
  for (const [code, id] of [
    ['instrument', instrumentImport],
    ['cashflow_event', cashflowImport],
  ] as const) {
    const pick = page.getByTestId(`snapshot-pick-${code}`)
    await expect(pick.locator(`option[value="${id}"]`)).toHaveCount(1)
    await pick.selectOption(id)
  }
  const built = isPost(page, `${API}/api/v1/dataset-snapshots`)
  await page.getByTestId('snapshot-build').click()
  const snapshotResponse = await built
  expect([200, 201], await snapshotResponse.text()).toContain(snapshotResponse.status())
  const snapshot: Schemas['SnapshotOut'] = await snapshotResponse.json()
  expect(snapshot.members.map((m) => m.import_id).sort()).toEqual([instrumentImport, cashflowImport].sort())
  const result = page.getByTestId('snapshot-result')
  await expect(result).toHaveAttribute('data-snapshot-id', snapshot.snapshot_id)
  await expect(result).toHaveAttribute('data-origin', snapshotResponse.status() === 201 ? 'created' : 'reused')
  await expect(page.getByTestId('snapshot-content-sha256')).toHaveText(snapshot.content_sha256)
  await expect(result.getByTestId('snapshot-member')).toHaveCount(2)

  // 3. The module (AVAILABLE only: PLANNED ones cannot be picked) and the reporting date.
  for (const m of modules.items.filter((x) => x.implementation_status === 'PLANNED'))
    await expect(page.locator(`[data-testid="module-option"][data-module-version-id="${m.module_version_id}"]`).getByTestId('module-radio')).toBeDisabled()
  await page.locator(`[data-testid="module-option"][data-module-version-id="${ladder!.module_version_id}"]`).getByTestId('module-radio').check()
  await page.getByTestId('reporting-date').fill(REPORTING_DATE)

  // 4. Before submitting: the versions the run will pin, as the server reported them.
  const pins = page.getByTestId('pins-preview')
  await expect(pins.locator('[data-testid="pin-row"][data-missing="true"]')).toHaveCount(0)
  await expect(pins.locator(`[data-testid="pin-row"][data-kind="MODULE_VERSION"][data-id="${ladder!.module_version_id}"]`)).toHaveCount(1)
  await expect(pins.locator('[data-testid="pin-row"][data-kind="ENGINE"]').getByTestId('pin-version')).toHaveText(ladder!.engine_version)
  expect(ladder!.rules.length).toBeGreaterThan(0)
  for (const rule of ladder!.rules) {
    const row = pins.locator(`[data-testid="pin-row"][data-kind="RULE_VERSION"][data-id="${rule.active_version!.rule_version_id}"]`)
    await expect(row).toContainText(rule.code)
    await expect(row.getByTestId('pin-version')).toHaveText(`v${rule.active_version!.version}`)
    await expect(row).toContainText(rule.active_version!.parameters_sha256)
  }
  await expect(pins.locator(`[data-testid="pin-row"][data-kind="ASSUMPTION_SET"][data-id="${ladder!.assumption_set!.assumption_set_id}"]`)).toHaveCount(1)
  await expect(pins.locator(`[data-testid="pin-row"][data-kind="DATASET_SNAPSHOT"][data-id="${snapshot.snapshot_id}"]`)).toHaveCount(1)
  await expect(pins.locator('[data-testid="pin-row"][data-kind="REPORTING_DATE"]').getByTestId('pin-version')).toHaveText(REPORTING_DATE)
  if (process.env.CALC_SCREENSHOT) await page.screenshot({ path: `${process.env.CALC_SCREENSHOT}/new-run.png`, fullPage: true })

  // 5. Submit: the server runs synchronously and ends the run; the page opens it in that status.
  const submitted = isPost(page, `${API}/api/v1/calculation-runs`)
  await page.getByTestId('run-submit').click()
  const runResponse = await submitted
  expect(runResponse.status(), await runResponse.text()).toBe(201)
  expect(runResponse.request().headers()['idempotency-key']).toBeTruthy()
  run = await runResponse.json()
  expect(['SUCCEEDED', 'SUCCEEDED_WITH_WARNINGS']).toContain(run!.status)
  expect(run!.dataset_snapshot_id).toBe(snapshot.snapshot_id)
  expect(run!.reporting_date).toBe(REPORTING_DATE)
  await page.waitForURL(`**/calculations/runs/${run!.run_id}`)
  await expect(page.getByTestId('run-detail-status')).toHaveAttribute('data-status', run!.status)
  await expect(page.getByTestId('run-id')).toHaveText(run!.run_id)
  await expect(page.getByTestId('run-reporting-date')).toHaveText(REPORTING_DATE)
  await expect(page.getByTestId('illustrative-banner')).toContainText(run!.statement)

  // 6. The run list shows it, in the server's status.
  await page.goto('/calculations')
  const listed = page.locator(`[data-testid="run-row"][data-run-id="${run!.run_id}"]`)
  await expect(listed).toHaveAttribute('data-status', run!.status)
  await expect(listed.getByTestId('run-reporting-date')).toHaveText(REPORTING_DATE)
})

test('FE-15.1: the approver has no New-run button and the server refuses its direct POSTs (403)', async ({ page, request }) => {
  expect(run).not.toBeNull()
  await signIn(page, 'Approver')
  await page.goto('/calculations')
  // calc:read: the list shows; no calc:run: no button.
  await expect(page.locator(`[data-testid="run-row"][data-run-id="${run!.run_id}"]`)).toBeVisible()
  await expect(page.getByTestId('new-run')).toHaveCount(0)
  await page.goto('/calculations/new')
  await expect(page.getByText("You don't have access to this")).toBeVisible()
  await expect(page.getByTestId('new-run-panel')).toHaveCount(0)

  // The button's absence is cosmetic: the server decides (D-04).
  const approver = await bearer(request, 'approver')
  const body: Schemas['RunCreate'] = {
    dataset_snapshot_id: run!.dataset_snapshot_id,
    module_version_id: run!.module.module_version_id,
    reporting_date: REPORTING_DATE,
  }
  const refused = await request.post(`${API}/api/v1/calculation-runs`, {
    headers: { ...approver, 'Idempotency-Key': `e2e-approver-${Date.now()}` },
    data: body,
  })
  expect(refused.status()).toBe(403)
  const envelope: Schemas['ErrorEnvelope'] = await refused.json()
  expect(envelope.code).toBe('forbidden')
  const snapshotBody: Schemas['SnapshotCreate'] = { import_ids: [instrumentImport, cashflowImport] }
  const refusedSnapshot = await request.post(`${API}/api/v1/dataset-snapshots`, { headers: approver, data: snapshotBody })
  expect(refusedSnapshot.status()).toBe(403)
})

test('FE-15.2: the run shows its manifest, warnings and ladder; a bucket total equals the sum of its drill-down rows; a row leads to its import', async ({
  page,
  browser,
  request,
}) => {
  expect(run).not.toBeNull()
  const runId = run!.run_id
  const approverApi = await bearer(request, 'approver')

  // 1. Open the run (approver: calc:read, cleared for the contributions under D-33).
  await signIn(page, 'Approver')
  const ladderUrl = `${API}/api/v1/calculation-runs/${runId}/maturity-ladder`
  const ladderRead = page.waitForResponse((r) => r.request().method() === 'GET' && r.url() === ladderUrl)
  await page.goto(`/calculations/runs/${runId}`)
  const ladder: Schemas['MaturityLadderOut'] = await (await ladderRead).json()
  const detail: Schemas['RunDetail'] = await (await request.get(`${API}/api/v1/calculation-runs/${runId}`, { headers: approverApi })).json()
  await expect(page.getByTestId('run-detail-status')).toHaveAttribute('data-status', detail.status)
  await expect(page.getByTestId('illustrative-banner')).toBeVisible()

  // The manifest: every pin, as the server wrote it.
  await expect(page.getByTestId('manifest-sha256')).toHaveText(detail.manifest_sha256)
  await expect(page.getByTestId('result-sha256')).toHaveText(detail.result_sha256!)
  await expect(page.getByTestId('manifest-rule-set-id')).toHaveText(detail.manifest.rule_set.rule_set_id)
  await expect(page.getByTestId('manifest-snapshot-id')).toHaveText(detail.manifest.dataset_snapshot.dataset_snapshot_id)
  await expect(page.getByTestId('manifest-engine')).toHaveText(detail.manifest.engine_version)
  await expect(page.getByTestId('manifest-rule')).toHaveCount(detail.manifest.rule_set.members.length)
  await expect(page.getByTestId('manifest-members').getByTestId('snapshot-member')).toHaveCount(detail.manifest.dataset_snapshot.members.length)
  await expect(page.getByTestId('input-ref')).toHaveCount(detail.input_refs.length)
  // The warnings table, one row per server warning.
  await expect(page.getByTestId('run-warning')).toHaveCount(detail.warnings.length)
  // Every result view carries the run ID and reporting date.
  await expect(page.getByTestId('maturity-ladder').getByTestId('identity-run-id')).toHaveText(runId)
  await expect(page.getByTestId('maturity-ladder').getByTestId('identity-reporting-date')).toHaveText(detail.reporting_date)

  // 2. The ladder per currency, exactly as the server aggregated it.
  expect(ladder.currencies.length).toBeGreaterThan(0)
  await expect(page.getByTestId('ladder-currency')).toHaveCount(ladder.currencies.length)
  const candidates = ladder.currencies.flatMap((c) => c.rows.filter((r) => r.contribution_count > 0).map((r) => ({ currency: c.currency, row: r })))
  expect(candidates.length).toBeGreaterThan(0)
  // Prefer a bucket with both directions, so both totals are checked, and with fewer than the
  // D-33 minimum of contributions, so step 5 sees its figures masked for risk_analyst.
  const bothWays = ({ row }: (typeof candidates)[number]) =>
    typeof row.inflows === 'string' && typeof row.outflows === 'string' && !sameDecimal(row.inflows, '0') && !sameDecimal(row.outflows, '0')
  const target =
    candidates.find((c) => bothWays(c) && c.row.contribution_count < ladder.min_aggregate_count) ?? candidates.find(bothWays) ?? candidates[0]
  const row = page.locator(`[data-testid="ladder-row"][data-currency="${target.currency}"][data-bucket="${target.row.bucket_code}"]`)
  // The approver (clearance confidential) is cleared for every figure, so none is masked or hinted.
  for (const figure of FIGURES) expect(typeof target.row[figure], `${figure} is clear for the approver (D-33)`).toBe('string')
  await expect(row.locator('[data-masked="true"]')).toHaveCount(0)
  await expect(page.getByTestId('ladder-min-aggregate-count')).toHaveText(String(ladder.min_aggregate_count))
  const shownInflows = (await row.getByTestId('ladder-inflows').textContent())!.trim()
  const shownOutflows = (await row.getByTestId('ladder-outflows').textContent())!.trim()
  expect(shownInflows).toBe(target.row.inflows)
  expect(shownOutflows).toBe(target.row.outflows)
  await expect(row.getByTestId('ladder-net')).toHaveText(target.row.net_gap as string)
  await expect(row.getByTestId('ladder-cumulative')).toHaveText(target.row.cumulative_gap as string)
  await expect(row.getByTestId('ladder-count')).toHaveText(String(target.row.contribution_count))

  // 3. Drill down: the bucket's contributions; their displayed values sum to the displayed totals.
  const resultsRead = page.waitForResponse(
    (r) =>
      r.request().method() === 'GET' &&
      r.url().startsWith(`${API}/api/v1/calculation-runs/${runId}/results?`) &&
      new URL(r.url()).searchParams.get('bucket_code') === target.row.bucket_code &&
      new URL(r.url()).searchParams.get('currency') === target.currency,
  )
  await row.getByTestId('ladder-drill').click()
  const results: Schemas['ResultPage'] = await (await resultsRead).json()
  expect(results.total).toBe(target.row.contribution_count)
  const drill = page.getByTestId('contributions')
  await expect(drill).toHaveAttribute('data-bucket', target.row.bucket_code)
  await expect(drill.getByTestId('contribution')).toHaveCount(target.row.contribution_count)
  await expect(drill.getByTestId('bucket-count')).toHaveText(String(target.row.contribution_count))
  const shown = async (direction: Schemas['CashflowDirection']) => {
    const cells = drill.locator(`[data-testid="contribution"][data-direction="${direction}"]`).getByTestId('contribution-value')
    return (await cells.allTextContents()).map((t) => t.trim())
  }
  const inflows = await shown('INFLOW')
  const outflows = await shown('OUTFLOW')
  expect(inflows.length + outflows.length).toBe(target.row.contribution_count)
  for (const v of [...inflows, ...outflows]) expect(v, 'the approver is cleared for contributions (D-33)').not.toBe('masked')
  expect(sameDecimal(inflows.length ? sumDecimals(inflows) : '0', shownInflows), `inflows ${inflows.join(' + ')} = ${shownInflows}`).toBe(true)
  expect(sameDecimal(outflows.length ? sumDecimals(outflows) : '0', shownOutflows), `outflows ${outflows.join(' + ')} = ${shownOutflows}`).toBe(true)
  // Each row is traced to the snapshot's cash-flow import and a source row.
  for (const item of results.items) expect(item.lineage.import_id).toBe(cashflowImport)
  if (process.env.CALC_SCREENSHOT) await page.screenshot({ path: `${process.env.CALC_SCREENSHOT}/run.png`, fullPage: true })

  // 4. Follow a row to its import.
  const first = results.items[0]
  const link = drill.locator(`[data-testid="contribution"][data-id="${first.intermediate_value_id}"]`).getByTestId('contribution-import-link')
  await expect(link).toContainText(`row ${first.lineage.source_ordinal}`)
  await link.click()
  await page.waitForURL(`**/ingestion/imports/${first.lineage.import_id}`)
  await expect(page.getByTestId('import-id')).toHaveText(first.lineage.import_id)

  // 5. risk_analyst (clearance internal): the bucket's contributions are masked (D-33), and so is
  //    every figure built from fewer than min_aggregate_count of them (or derived from one), with
  //    a hint; its zero figures stay clear. The UI shows exactly what the server sent.
  const analyst = await browser.newPage()
  await signIn(analyst, 'Risk analyst')
  const analystLadderRead = analyst.waitForResponse((r) => r.request().method() === 'GET' && r.url() === ladderUrl)
  const masked = analyst.waitForResponse(
    (r) =>
      r.request().method() === 'GET' &&
      r.url().startsWith(`${API}/api/v1/calculation-runs/${runId}/results?`) &&
      new URL(r.url()).searchParams.get('bucket_code') === target.row.bucket_code,
  )
  await analyst.goto(`/calculations/runs/${runId}?${new URLSearchParams({ currency: target.currency, bucket: target.row.bucket_code })}`)
  const analystLadder: Schemas['MaturityLadderOut'] = await (await analystLadderRead).json()
  const seen: Schemas['ResultPage'] = await (await masked).json()
  expect(seen.items[0].value).toEqual({ masked: true, classification: expect.any(String) })
  const analystDrill = analyst.getByTestId('contributions')
  await expect(analystDrill.getByTestId('contribution').first()).toHaveAttribute('data-masked', 'true')
  await expect(analystDrill.getByTestId('contribution-value').first()).toHaveText('masked')

  const min = analystLadder.min_aggregate_count
  expect(target.row.contribution_count, `the drilled bucket has fewer than ${min} contributions`).toBeLessThan(min)
  const seenRow = analystLadder.currencies.find((c) => c.currency === target.currency)?.rows.find((r) => r.bucket_code === target.row.bucket_code)
  expect(seenRow?.contribution_count).toBe(target.row.contribution_count)
  const analystRow = analyst.locator(`[data-testid="ladder-row"][data-currency="${target.currency}"][data-bucket="${target.row.bucket_code}"]`)
  await expect(analystRow.getByTestId('ladder-count')).toHaveText(String(target.row.contribution_count))
  let maskedFigures = 0
  for (const figure of FIGURES) {
    const value = seenRow![figure]
    const cell = analystRow.getByTestId(FIGURE_TEST_IDS[figure])
    // The UI shows exactly what the server sent: the decimal text, or "masked".
    if (typeof value === 'string') {
      await expect(cell).toHaveText(value)
      await expect(cell).not.toHaveAttribute('data-masked', 'true')
    } else {
      await expect(cell).toHaveAttribute('data-masked', 'true')
      await expect(cell).toHaveText('masked')
    }
    // D-33 minimum aggregation, against the approver's clear value (step 2): a zero figure stays
    // clear; a non-zero one, built from fewer than the minimum (or derived from one), is masked.
    const clear = target.row[figure] as string
    if (sameDecimal(clear, '0')) {
      if (figure !== 'cumulative_gap') expect(value, `${figure} is zero, so clear`).toBe(clear)
    } else {
      expect(value, `${figure} is masked for risk_analyst`).toEqual({ masked: true, classification: expect.any(String) })
      maskedFigures += 1
    }
  }
  expect(maskedFigures).toBeGreaterThan(0)
  // The masked headline figures say why: fewer than the server's minimum.
  for (const figure of ['inflows', 'outflows', 'net_gap', 'cumulative_gap'] as const)
    if (typeof seenRow![figure] !== 'string')
      await expect(analystRow.getByTestId(`${FIGURE_TEST_IDS[figure]}-hint`)).toContainText(`fewer than ${min} contributions`)
  // The drill-down's bucket totals are the same server figures, masked the same way.
  await expect(analystDrill.getByTestId('bucket-inflows')).toHaveAttribute('data-masked', 'true')
  await expect(analystDrill.getByTestId('bucket-inflows-hint')).toContainText(`fewer than ${min} contributions`)
  await expect(analystDrill.getByTestId('bucket-count')).toHaveText(String(target.row.contribution_count))
  if (process.env.CALC_SCREENSHOT) await analyst.screenshot({ path: `${process.env.CALC_SCREENSHOT}/run-analyst.png`, fullPage: true })
})
