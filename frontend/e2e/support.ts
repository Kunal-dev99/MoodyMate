import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { expect, type APIRequestContext, type Page } from '@playwright/test'

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

/** POST /imports with a sample file (synthetic attested). */
export async function uploadSample(request: APIRequestContext, headers: Record<string, string>, sourceId: string, file: string) {
  const r = await request.post(`${API}/api/v1/imports`, {
    headers: { ...headers, 'Idempotency-Key': `e2e-${Date.now()}-${Math.random().toString(16).slice(2)}` },
    multipart: { source_id: sourceId, synthetic_attested: 'true', file: { name: file, mimeType: 'text/csv', buffer: readFileSync(join(SAMPLES, file)) } },
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
 */
export async function ensureActiveMapping(request: APIRequestContext, contractId: string, sourceId: string, importId: string) {
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
          data: { code: 'e2e_core_instrument', name: 'Core banking instruments (e2e)', source_contract_id: contractId, ...data },
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
