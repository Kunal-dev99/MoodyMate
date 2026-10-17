import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { expect, type APIRequestContext, type Page } from '@playwright/test'
import type { components } from '../src/api/schema'

type Schemas = components['schemas']

/*
 * Shared e2e helpers (Feature 02 Phase 11). Setup that is not under test goes through the
 * API with dev-session tokens; what the Done-whens check happens in the browser.
 */

/**
 * The backend the browser build calls: E2E_API_PORT (default 8000), which must match the
 * NEXT_PUBLIC_API_BASE_URL the frontend was built with. playwright.config.ts starts the
 * backend on the same port.
 */
export const API = `http://localhost:${process.env.E2E_API_PORT ?? '8000'}`
export const SAMPLES = join(__dirname, '../../backend/samples/synthetic')

export async function signIn(page: Page, persona: string) {
  await page.goto('/login')
  await page.getByRole('button', { name: new RegExp(persona) }).click()
  await page.waitForURL('**/dashboard')
}

/** A dev-session bearer header for `role` (backend ENV=test). */
export async function bearer(request: APIRequestContext, role: string): Promise<Record<string, string>> {
  const r = await request.post(`${API}/api/v1/dev/sessions`, { data: { role } })
  expect(r.ok()).toBe(true)
  return { Authorization: `Bearer ${(await r.json()).access_token}` }
}

/** The seeded core_banking source and its current contract. */
export async function coreBanking(request: APIRequestContext, headers: Record<string, string>) {
  const r = await request.get(`${API}/api/v1/sources?limit=200`, { headers })
  expect(r.status()).toBe(200)
  const source = (await r.json()).items.find((s: { code: string }) => s.code === 'core_banking')
  expect(source?.current_contract).toBeTruthy()
  return { sourceId: source.id as string, contractId: source.current_contract.id as string }
}

/** POST /imports with a sample file (synthetic attested); `mimeType` for a non-CSV sample. */
export async function uploadSample(
  request: APIRequestContext,
  headers: Record<string, string>,
  sourceId: string,
  file: string,
  mimeType = 'text/csv',
) {
  const r = await request.post(`${API}/api/v1/imports`, {
    headers: { ...headers, 'Idempotency-Key': `e2e-${Date.now()}-${Math.random().toString(16).slice(2)}` },
    multipart: { source_id: sourceId, synthetic_attested: 'true', file: { name: file, mimeType, buffer: readFileSync(join(SAMPLES, file)) } },
  })
  expect(r.status()).toBe(201)
  return r.json()
}

const rule = (source: string, target: string, code = 'COPY', params: Record<string, unknown> = {}) => ({
  source_column: source,
  target_field: target,
  transform: { code, params },
})

/** instrument.csv → instrument (as the backend's INSTRUMENT_RULES test helper). */
export const INSTRUMENT_RULES = [
  rule('instrument_id', 'instrument_id'),
  rule('instrument_type', 'instrument_type'),
  rule('currency', 'currency'),
  rule('counterparty_id', 'counterparty_id'),
  rule('product_family', 'product_family'),
  rule('notional', 'notional', 'PARSE_DECIMAL'),
  rule('interest_rate', 'interest_rate', 'PARSE_DECIMAL'),
  rule('maturity_date', 'maturity_date', 'PARSE_DATE', { format: 'YYYY-MM-DD' }),
  rule('book_code', 'legacy_book_code'),
]

/**
 * Makes sure the core_banking contract has an ACTIVE mapping version, as e2e/mappings.spec.ts
 * does through the UI: create (or add a version), dry-run against `importId`, submit as the
 * steward, approve as the approver. A mapping already ACTIVE (e.g. from mappings.spec) is kept.
 * `code` names a new mapping; a spec with its own source passes its own (codes are unique).
 */
export async function ensureActiveMapping(
  request: APIRequestContext,
  contractId: string,
  sourceId: string,
  importId: string,
  code = 'e2e_core_instrument',
) {
  const steward = await bearer(request, 'data_steward')
  const approver = await bearer(request, 'approver')
  const list = await request.get(`${API}/api/v1/mappings?source_id=${sourceId}&limit=200`, { headers: steward })
  const existing = (await list.json()).items.find((m: { source_contract_id: string }) => m.source_contract_id === contractId)
  if (existing?.active_version) {
    // Usable only while it pins the import's schema version (a later spec may have moved the schema on).
    const job = await (await request.get(`${API}/api/v1/imports/${importId}`, { headers: steward })).json()
    const detail = await (await request.get(`${API}/api/v1/mappings/${existing.id}`, { headers: steward })).json()
    const active = detail.versions.find((v: { status: string }) => v.status === 'ACTIVE')
    if (active?.schema_version_id === job.schema_version_id) return existing.id as string
  }

  // A later schema change (smoke.spec's proposal drops counterparty_id) leaves some targets
  // unknown: those columns are PRESERVEd instead, so the rules fit the pinned schema version.
  let rules: Record<string, unknown>[] = INSTRUMENT_RULES
  const post = (data: Record<string, unknown>) =>
    existing
      ? request.post(`${API}/api/v1/mappings/${existing.id}/versions`, { headers: steward, data })
      : request.post(`${API}/api/v1/mappings`, {
          headers: steward,
          data: { code, name: 'Core banking instruments (e2e)', source_contract_id: contractId, ...data },
        })
  let r = await post({ rules })
  if (r.status() === 422) {
    const body = await r.json()
    const unknown: string[] = body.code === 'unknown_target_field' ? body.details.fields : []
    expect(unknown.length, JSON.stringify(body)).toBeGreaterThan(0)
    rules = INSTRUMENT_RULES.map((x) =>
      unknown.includes(x.target_field) ? { source_column: x.source_column, column_policy: 'PRESERVE', transform: { code: 'COPY', params: {} } } : x,
    )
    r = await post({ rules })
  }
  expect(r.status(), await r.text()).toBe(201)
  const out = await r.json()
  const version: { id: string; mapping_id: string; version: number } = existing ? out : out.versions[0]
  const dry = await request.post(`${API}/api/v1/imports/${importId}/mappings/validate`, { headers: steward, data: { mapping_version_id: version.id } })
  expect(dry.status(), await dry.text()).toBe(201)
  expect((await dry.json()).ok).toBe(true)
  const base = `${API}/api/v1/mappings/${version.mapping_id}/versions/${version.version}`
  expect((await request.post(`${base}/submit`, { headers: steward, data: {} })).status()).toBe(200)
  const approved = await request.post(`${base}/approval`, { headers: approver, data: { decision: 'APPROVE' } })
  expect(approved.status(), await approved.text()).toBe(200)
  expect((await approved.json()).status).toBe('ACTIVE')
  return version.mapping_id
}

/** Upload a sample through the import wizard (FE-8.1); returns the server's import. */
export async function uploadInBrowser(page: Page, sourceId: string, file: string) {
  await page.goto(`/ingestion/imports/new?source=${sourceId}`)
  await page.getByTestId('import-continue').click()
  await page.getByTestId('import-file').setInputFiles(join(SAMPLES, file))
  await page.getByTestId('synthetic-attested').click()
  const uploaded = page.waitForResponse((r) => r.request().method() === 'POST' && r.url() === `${API}/api/v1/imports`)
  await page.getByTestId('import-upload').click()
  const response = await uploaded
  expect(response.status()).toBe(201)
  const job = await response.json()
  await page.waitForURL(`**/ingestion/imports/${job.id}**`)
  return job as { id: string; status: string; row_count: number }
}

/** Validate the open import from its Validation tab (FE-11.1); returns the server's import. */
export async function validateInBrowser(page: Page, importId: string) {
  await page.getByTestId('import-tab-validation').click()
  const validated = page.waitForResponse((r) => r.request().method() === 'POST' && r.url() === `${API}/api/v1/imports/${importId}/validate`)
  await page.getByTestId('import-validate').click()
  const response = await validated
  expect(response.status(), await response.text()).toBe(200)
  return response.json()
}

/** A reconciliation figure as the API sends it (BE-12.1): a decimal string, masked, or absent. */
export type Figure = string | { masked: true; classification: string } | null
export type ReconItem = {
  sequence: number
  check: string
  column: string | null
  status: string
  source: Figure
  target: Figure
  held: Figure
  variance: Figure
  tolerance: string
}
/** How the Reconciliation tab renders a figure (FE-12.1). */
export const shownFigure = (v: Figure) => (v === null ? '—' : typeof v === 'string' ? v : 'masked')

// --- Feature 03 setup through the API (calculations.spec, rules.spec; not under test) ------------

/** A new source whose contract copies the seeded core_banking one (it fits instrument.csv). */
export async function registerInstrumentSource(request: APIRequestContext, steward: Record<string, string>) {
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
export async function cashflowImportReady(request: APIRequestContext, steward: Record<string, string>) {
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
export async function validateAndProject(request: APIRequestContext, steward: Record<string, string>, importId: string) {
  const validated = await request.post(`${API}/api/v1/imports/${importId}/validate`, { headers: steward })
  expect(validated.status(), await validated.text()).toBe(200)
  const job: Schemas['ImportDetail'] = await validated.json()
  expect(['COMPLETED', 'COMPLETED_WITH_WARNINGS']).toContain(job.status)
  const projected = await request.post(`${API}/api/v1/imports/${importId}/projection`, { headers: steward })
  expect(projected.status(), await projected.text()).toBe(201)
  const projection: Schemas['ProjectionOut'] = await projected.json()
  expect(projection.projected_count).toBe(job.manifest!.quality.accepted_records)
}
