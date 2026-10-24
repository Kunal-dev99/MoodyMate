import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { expect, type APIRequestContext, type Page, type Response } from '@playwright/test'
import type { components } from '../src/api/schema'
import { API, SAMPLES, bearer, ensureActiveMapping, uploadSample } from './support'

/*
 * Shared setup for the Feature 03 calculation specs (FE-17.1): projected synthetic imports
 * through the API (not under test), and exact decimal arithmetic for the assertions. Every
 * payload type is the generated one (src/api/schema.d.ts); none is written here.
 */

type Schemas = components['schemas']

// --- exact decimal text (test arithmetic only: the UI never parses a value) -------------------

function units(text: string): { value: bigint; scale: number } {
  // Money is shown grouped by thousands (P-7): the grouping commas are display-only.
  const m = /^(-?)(\d+)(?:\.(\d+))?$/.exec(text.trim().replace(/,/g, ''))
  expect(m, `"${text}" is exact decimal text`).toBeTruthy()
  const [, sign, whole, frac = ''] = m!
  const value = BigInt(`${whole}${frac}`)
  return { value: sign ? -value : value, scale: frac.length }
}

const TEN = BigInt(10)
const rescale = (u: { value: bigint; scale: number }, scale: number) => u.value * TEN ** BigInt(scale - u.scale)

/** a = b as decimals (1.50 = 1.5). */
export function sameDecimal(a: string, b: string): boolean {
  const x = units(a)
  const y = units(b)
  const scale = x.scale > y.scale ? x.scale : y.scale
  return rescale(x, scale) === rescale(y, scale)
}

/** The exact sum of decimal texts, as text at the widest scale ("0" for none). */
export function sumDecimals(values: string[]): string {
  if (values.length === 0) return '0'
  const all = values.map(units)
  const scale = all.reduce((s, u) => (u.scale > s ? u.scale : s), 0)
  const total = all.reduce((s, u) => s + rescale(u, scale), BigInt(0))
  const negative = total < BigInt(0)
  const digits = (negative ? -total : total).toString().padStart(scale + 1, '0')
  const text = scale === 0 ? digits : `${digits.slice(0, -scale)}.${digits.slice(-scale)}`
  return negative ? `-${text}` : text
}

// --- a submitted run, once the server has ended it (FE-33.1) -----------------------------------

const PENDING: readonly string[] = ['RUNNING', 'QUEUED']

/**
 * The status "Submit run" gets for a new run under this build (Q20): 202 with background runs on
 * (the default: `Prefer: respond-async`, D-59 §2), 201 with NEXT_PUBLIC_BACKGROUND_RUNS_UI=off
 * (the Phase 32 request exactly). Run the suite with the same value the frontend was built with.
 */
export function expectedRunStatus(): 201 | 202 {
  return process.env.NEXT_PUBLIC_BACKGROUND_RUNS_UI === 'off' ? 201 : 202
}

/**
 * The run a "Submit run" POST wrote, in the terminal status the server reports. A synchronous
 * answer (201, Phase 32 and NEXT_PUBLIC_BACKGROUND_RUNS_UI=off) is the terminal run itself. A
 * background answer (202, `Prefer: respond-async`, D-59 §2) is the run RUNNING; the browser then
 * polls GET /calculation-runs/{run_id}, and the first read the server answers with a terminal
 * status is returned. Call it as soon as the POST resolves (the first poll waits 0.5 s).
 */
export async function settledRun(page: Page, response: Response): Promise<Schemas['RunDetail']> {
  const body: Schemas['RunDetail'] = await response.json()
  if (response.status() !== 202) {
    expect(response.status(), JSON.stringify(body)).toBe(201)
    return body
  }
  expect(response.request().headers()['prefer']).toBe('respond-async')
  expect(response.headers()['preference-applied']).toBe('respond-async')
  expect(body.status, 'a background run is accepted RUNNING, never as an outcome').toBe('RUNNING')
  const url = `${API}/api/v1/calculation-runs/${body.run_id}`
  const ended = await page.waitForResponse(
    async (r) => r.request().method() === 'GET' && r.url() === url && r.ok() && !PENDING.includes(((await r.json()) as Schemas['RunDetail']).status),
    { timeout: 60_000 },
  )
  return ended.json()
}

// --- projected synthetic imports -------------------------------------------------------------

/** A new source whose contract copies the seeded core_banking one (it fits instrument.csv). */
async function registerInstrumentSource(request: APIRequestContext, steward: Record<string, string>, prefix: string) {
  const list: Schemas['SourcePage'] = await (await request.get(`${API}/api/v1/sources?limit=200`, { headers: steward })).json()
  const core = list.items.find((s) => s.code === 'core_banking')
  expect(core?.current_contract).toBeTruthy()
  const contract = core!.current_contract!
  const profile = core!.profile
  const body: Schemas['SourceCreate'] = {
    code: `${prefix}_instr_${Date.now()}`,
    name: `Instruments (${prefix} e2e)`,
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

/** A non-CSV synthetic sample, described as the backend's `validated_sample` helper does. */
const SAMPLE_FILES = {
  position: {
    file: 'position.xlsx',
    format: 'XLSX',
    mimeType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    key: 'position_id',
    // The header row of position.xlsx (the file's columns, not an API payload).
    columns: () => ['position_id', 'instrument_id', 'account_id', 'legal_entity_id', 'as_of_date', 'quantity', 'balance', 'currency', 'valuation_date'],
  },
  cashflow_event: {
    file: 'cashflow_event.json',
    format: 'JSON',
    mimeType: 'application/json',
    key: 'cashflow_id',
    columns: () => Object.keys((JSON.parse(readFileSync(join(SAMPLES, 'cashflow_event.json'), 'utf8')) as { records: Record<string, unknown>[] }).records[0]),
  },
} as const satisfies Record<string, { file: string; format: Schemas['SourceCreate']['profile']['format']; mimeType: string; key: string; columns: () => string[] }>

/**
 * A source for the synthetic position (XLSX) or cash-flow (JSON) sample (its own columns and
 * business key), and a mapping of every column to the same-named field of the schema version
 * the import pins (decimals and dates parsed), made ACTIVE against the import.
 */
async function sampleImportReady(request: APIRequestContext, steward: Record<string, string>, code: keyof typeof SAMPLE_FILES, prefix: string) {
  const spec = SAMPLE_FILES[code]
  const schemas: Schemas['SchemaList'] = await (await request.get(`${API}/api/v1/schemas`, { headers: steward })).json()
  const schema = schemas.items.find((s) => s.code === code)
  expect(schema).toBeTruthy()
  const columns = spec.columns()
  const body: Schemas['SourceCreate'] = {
    code: `${prefix}_${code}_${Date.now()}`,
    name: `${code} (${prefix} e2e)`,
    kind: 'FILE_DROP',
    profile: { format: spec.format, encoding: 'UTF-8', owner: 'Treasury data team (e2e)', default_classification: 'confidential' },
    contract: {
      schema_id: schema!.id,
      expected_columns: columns.map((name) => ({ name, required: true })),
      business_key_columns: [spec.key],
      control_total_columns: [],
    },
  }
  const created = await request.post(`${API}/api/v1/sources`, { headers: steward, data: body })
  expect(created.status(), await created.text()).toBe(201)
  const source: Schemas['SourceDetail'] = await created.json()
  const uploaded: Schemas['ImportDetail'] = await uploadSample(request, steward, source.id, spec.file, spec.mimeType)
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
  const mapping: Schemas['MappingCreate'] = {
    code: `${prefix}_${code}_${Date.now()}`,
    name: `${code} (${prefix} e2e)`,
    source_contract_id: source.current_contract!.id,
    rules,
  }
  const m = await request.post(`${API}/api/v1/mappings`, { headers: steward, data: mapping })
  expect(m.status(), await m.text()).toBe(201)
  const version: Schemas['MappingVersionOut'] = (await m.json()).versions[0]
  const dry = await request.post(`${API}/api/v1/imports/${uploaded.id}/mappings/validate`, { headers: steward, data: { mapping_version_id: version.id } })
  expect(dry.status(), await dry.text()).toBe(201)
  expect((await dry.json()).ok, JSON.stringify(await dry.json())).toBe(true)
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

/**
 * The synthetic instrument, position and cash-flow samples, each through its own source and
 * ACTIVE mapping, validated and projected into the WS3 domain (setup, not under test). Returns
 * the import ids per schema code. `prefix` keeps source and mapping codes unique per spec.
 */
export async function projectedSyntheticImports(request: APIRequestContext, prefix: string) {
  const steward = await bearer(request, 'data_steward')
  const { sourceId, contractId } = await registerInstrumentSource(request, steward, prefix)
  const instruments: Schemas['ImportDetail'] = await uploadSample(request, steward, sourceId, 'instrument.csv')
  expect(instruments.schema_version, 'projector v1 supports instrument v1 only: run on a fresh database, before smoke.spec').toBe(1)
  await ensureActiveMapping(request, contractId, sourceId, instruments.id, `${prefix}_instr_${Date.now()}`)
  await validateAndProject(request, steward, instruments.id)
  const position = await sampleImportReady(request, steward, 'position', prefix)
  await validateAndProject(request, steward, position)
  const cashflow = await sampleImportReady(request, steward, 'cashflow_event', prefix)
  await validateAndProject(request, steward, cashflow)
  return { instrument: instruments.id, position, cashflow_event: cashflow }
}
