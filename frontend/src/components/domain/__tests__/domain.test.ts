import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, api } from '@/api/client'
import { ShownValue } from '@/components/domain/ShownValue'
import { ProjectionSummaryView, fetchProjection } from '@/components/domain/ProjectToDomain'
import {
  DOMAIN_SCHEMA_CODES,
  NO_FILTERS,
  PROJECTABLE_STATUSES,
  canProject,
  cashFlowsOf,
  catalogueHref,
  catalogueQuery,
  fieldCounts,
  isDomainSchemaCode,
  isNotProjected,
  originOf,
  projectionConserves,
  projectionRefusal,
  vocabularyEntry,
  type CashFlow,
  type InstrumentVersion,
  type Projection,
} from '@/components/domain/domain'
import type { ImportStatus } from '@/components/ingestion/imports'

// The OpenAPI document the types are generated from: enums are tested against it (D-23).
const openapi = JSON.parse(readFileSync(fileURLToPath(new URL('../../../../../backend/openapi.json', import.meta.url)), 'utf8'))
const enumOf = (name: string): string[] => [...openapi.components.schemas[name].enum].sort()

const envelope = (code: string) => ({ code, message: `${code} message`, details: null, correlation_id: 'cid-14', retryable: false })
const manifest = { manifest_id: 'm-1' } as unknown as NonNullable<Parameters<typeof canProject>[0]['manifest']>

const projection = (over: Partial<Projection> = {}): Projection => ({
  projection_id: '11111111-1111-4111-8111-111111111111',
  import_id: '22222222-2222-4222-8222-222222222222',
  import_status: 'COMPLETED',
  schema_code: 'instrument',
  schema_version_id: '33333333-3333-4333-8333-333333333333',
  schema_version: 1,
  projector_version: 1,
  accepted_count: 12,
  projected_count: 12,
  summary: {
    records: 12,
    rows_projected: 12,
    new_identities: 12,
    attribute_values: 24,
    type_not_projected: { maturity_date: 12 },
    unknown_values: {},
    canonical_only_fields: ['counterparty_id'],
  },
  projected_at: '2026-10-17T10:00:00Z',
  projected_by: 'user:steward',
  ...over,
})

const flow = (id: string): CashFlow => ({
  cashflow_event_id: id,
  business_key: 'INS-1',
  import_id: '44444444-4444-4444-8444-444444444444',
  canonical_record_id: `cr-${id}`,
  source_ordinal: 1,
  schema_version_id: 'sv',
  due_date: '2027-01-01',
  amount: '100.00',
  currency: 'EUR',
  cashflow_type: 'PRINCIPAL',
  origin: 'CONTRACTUAL',
})

describe('projection eligibility (FE-14.2, D-29 §1)', () => {
  it('supports exactly the API schema codes', () => {
    expect([...DOMAIN_SCHEMA_CODES].sort()).toEqual(enumOf('DomainSchemaCode'))
    expect(isDomainSchemaCode('instrument')).toBe(true)
    expect(isDomainSchemaCode('counterparty')).toBe(false)
    expect(isDomainSchemaCode(null)).toBe(false)
  })

  it('offers the action only for a validated import (manifest + projectable status) of a supported schema', () => {
    for (const status of enumOf('ImportStatus') as ImportStatus[]) {
      const expected = PROJECTABLE_STATUSES.includes(status)
      expect(canProject({ status, manifest, schema_code: 'instrument' }), status).toBe(expected)
    }
    expect([...PROJECTABLE_STATUSES].sort()).toEqual(['COMPLETED', 'COMPLETED_WITH_WARNINGS', 'QUARANTINED'])
    expect(canProject({ status: 'COMPLETED', manifest: null, schema_code: 'instrument' })).toBe(false)
    expect(canProject({ status: 'COMPLETED', manifest, schema_code: 'counterparty' })).toBe(false)
    expect(canProject({ status: 'COMPLETED', manifest, schema_code: null })).toBe(false)
    expect(canProject({ status: 'QUARANTINED', manifest, schema_code: 'cashflow_event' })).toBe(true)
  })

  it('names each refusal with the server code, message and correlation ID', () => {
    for (const code of ['projection_unsupported', 'not_validated', 'domain_projection_disabled', 'forbidden']) {
      const r = projectionRefusal(new ApiError(code === 'forbidden' ? 403 : 409, envelope(code)))
      expect(r).toMatchObject({ code, message: `${code} message`, cid: 'cid-14' })
      expect(r.title).not.toBe('Could not project the import')
    }
    expect(projectionRefusal(new ApiError(500, envelope('internal_error'))).title).toBe('Could not project the import')
    expect(projectionRefusal(new Error('x'))).toMatchObject({ status: null, code: 'unexpected_error' })
  })
})

describe('reading an existing projection (FE-14.2, GET /imports/{id}/projection)', () => {
  const answer = (status: number, body: unknown) =>
    vi.spyOn(api, 'GET').mockResolvedValue({
      data: status === 200 ? body : undefined,
      error: status === 200 ? undefined : body,
      response: new Response(null, { status }),
    } as never)
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('tells "not projected yet" (404 projection_not_found) from other errors', () => {
    expect(isNotProjected(new ApiError(404, envelope('projection_not_found')))).toBe(true)
    expect(isNotProjected(new ApiError(404, envelope('not_found')))).toBe(false)
    expect(isNotProjected(new ApiError(409, envelope('projection_not_found')))).toBe(false)
    expect(isNotProjected(new Error('projection_not_found'))).toBe(false)
  })

  it('reads the projection of the import, by GET only', async () => {
    const get = answer(200, projection())
    const signal = new AbortController().signal
    await expect(fetchProjection('22222222-2222-4222-8222-222222222222')(signal)).resolves.toMatchObject({ projection_id: projection().projection_id })
    expect(get).toHaveBeenCalledWith('/api/v1/imports/{import_id}/projection', {
      params: { path: { import_id: '22222222-2222-4222-8222-222222222222' } },
      signal,
    })
  })

  it('answers null while the import is not projected, and fails on any other refusal', async () => {
    answer(404, envelope('projection_not_found'))
    await expect(fetchProjection('i')(new AbortController().signal)).resolves.toBeNull()
    vi.restoreAllMocks()
    answer(404, envelope('not_found'))
    await expect(fetchProjection('i')(new AbortController().signal)).rejects.toMatchObject({ status: 404, envelope: { code: 'not_found' } })
    vi.restoreAllMocks()
    answer(403, envelope('forbidden'))
    await expect(fetchProjection('i')(new AbortController().signal)).rejects.toMatchObject({ status: 403 })
  })
})

describe('projection summary (FE-14.2)', () => {
  it('checks conservation against the manifest accepted count', () => {
    expect(projectionConserves(projection())).toBe(true)
    expect(projectionConserves(projection({ projected_count: 11 }))).toBe(false)
  })

  it('renders the server counts, TYPE_NOT_PROJECTED per field and the projection ID', () => {
    const html = renderToStaticMarkup(createElement(ProjectionSummaryView, { projection: projection(), origin: 'created' }))
    expect(html).toContain('data-projection-id="11111111-1111-4111-8111-111111111111"')
    expect(html).toContain('data-origin="created"')
    expect(html).toMatch(/data-testid="projection-accepted"[^>]*>12</)
    expect(html).toMatch(/data-testid="projection-projected"[^>]*>12</)
    expect(html).toContain('data-conserves="true"')
    expect(html).toContain('data-testid="type-not-projected" data-field="maturity_date" data-count="12"')
    expect(html).toContain('data-testid="unknown-value-none"')
    expect(html).toContain('counterparty_id')
    expect(html).toContain('Projected now')
    expect(html).toContain(`href="${catalogueHref('22222222-2222-4222-8222-222222222222').replace(/&/g, '&amp;')}"`)
  })

  it('says a replay wrote nothing, and flags a projection that does not conserve rows', () => {
    const replay = renderToStaticMarkup(createElement(ProjectionSummaryView, { projection: projection(), origin: 'replayed' }))
    expect(replay).toContain('Already projected')
    expect(replay).toContain('Nothing new was written')
    const broken = renderToStaticMarkup(createElement(ProjectionSummaryView, { projection: projection({ projected_count: 11 }), origin: 'created' }))
    expect(broken).toContain('data-conserves="false"')
  })

  it('labels a projection read on load as the server reported it, not as projected now', () => {
    const html = renderToStaticMarkup(createElement(ProjectionSummaryView, { projection: projection(), origin: 'existing' }))
    expect(html).toContain('data-origin="existing"')
    expect(html).toContain('by user:steward, as the server reports it')
    expect(html).not.toContain('Projected now')
    expect(originOf(201)).toBe('created')
    expect(originOf(200)).toBe('replayed')
  })

  it('links to the catalogue only for instrument projections', () => {
    const html = renderToStaticMarkup(createElement(ProjectionSummaryView, { projection: projection({ schema_code: 'cashflow_event' }), origin: 'created' }))
    expect(html).not.toContain('projection-browse')
    expect(html).toContain('cash-flow event per accepted canonical record')
  })

  it('sorts per-field counts and keeps zero counts', () => {
    expect(fieldCounts({ b: 0, a: 2 })).toEqual([
      ['a', 2],
      ['b', 0],
    ])
    expect(fieldCounts(undefined)).toEqual([])
  })
})

describe('catalogue filters (FE-14.1)', () => {
  it('sends only well-formed filters, as the API patterns require', () => {
    expect(catalogueQuery(NO_FILTERS)).toEqual({ query: {}, problems: [] })
    const id = '22222222-2222-4222-8222-222222222222'
    expect(catalogueQuery({ instrument_type: 'LOAN', currency: 'eur', source_id: id, import_id: ` ${id} ` })).toEqual({
      query: { instrument_type: 'LOAN', currency: 'EUR', source_id: id, import_id: id },
      problems: [],
    })
    expect(catalogueQuery({ ...NO_FILTERS, currency: 'EU', import_id: 'abc' })).toEqual({ query: {}, problems: ['currency', 'import_id'] })
  })

  it('links the catalogue to one import', () => {
    expect(catalogueHref('abc')).toBe('/domain?import_id=abc')
  })

  it('finds the seeded vocabulary entry by exact code', () => {
    const types = [{ code: 'LOAN', name: 'Loan', provisional: true }]
    expect(vocabularyEntry(types, 'LOAN')?.provisional).toBe(true)
    expect(vocabularyEntry(types, 'loan')).toBeUndefined()
    expect(vocabularyEntry(types, null)).toBeUndefined()
  })
})

describe('instrument detail (FE-14.1)', () => {
  it('lists each cash flow once though every version carries it', () => {
    const v = (flows: CashFlow[]) => ({ cash_flows: flows }) as unknown as InstrumentVersion
    expect(cashFlowsOf({ versions: [v([flow('a'), flow('b')]), v([flow('a'), flow('b'), flow('c')])] }).map((c) => c.cashflow_event_id)).toEqual([
      'a',
      'b',
      'c',
    ])
  })

  it('shows masked values as "masked", never blank, and decimal text exactly as sent', () => {
    const masked = renderToStaticMarkup(createElement(ShownValue, { value: { masked: true, classification: 'confidential' }, testId: 'version-notional' }))
    expect(masked).toContain('data-testid="version-notional"')
    expect(masked).toContain('data-testid="masked-value"')
    expect(masked).toContain('masked')
    expect(masked).toContain('confidential')
    // 2^53 + 1 with 18 decimals: a float would print 9007199254740992.
    const exact = renderToStaticMarkup(createElement(ShownValue, { value: '9007199254740993.000000000000000001' }))
    expect(exact).toContain('>9007199254740993.000000000000000001<')
    expect(renderToStaticMarkup(createElement(ShownValue, { value: null }))).toContain('data-empty="true"')
  })
})

describe('Financial Domain navigation and reversibility (FE-14.1, D-02)', () => {
  afterEach(() => {
    vi.unstubAllEnvs()
    vi.resetModules()
  })

  it('is live and needs domain:read', async () => {
    const { mainWorkspaces } = await import('@/config/navigation')
    const domain = mainWorkspaces.find((w) => w.slug === 'domain')
    expect(domain?.planned).toBeUndefined()
    expect(domain?.permission).toBe('domain:read')
  })

  it('NEXT_PUBLIC_DOMAIN_UI=off restores the Phase 13 planned entry exactly', async () => {
    vi.stubEnv('NEXT_PUBLIC_DOMAIN_UI', 'off')
    vi.resetModules()
    const { mainWorkspaces } = await import('@/config/navigation')
    const domain = mainWorkspaces.find((w) => w.slug === 'domain')
    expect(domain).toMatchObject({
      href: '/domain',
      label: 'Financial Domain',
      description: 'Instrument types and records, assumptions and the rule registry.',
      planned: { phase: 'Feature 03', workstream: 'WS3' },
    })
    expect(domain?.permission).toBeUndefined()
  })
})

/**
 * Amounts, rates and dates are decimal text from the API: the domain code is grepped for every
 * way JavaScript turns text into a float (as FE-12.1), and for hand-written payload types.
 */
describe('domain code hygiene (FE-14.1/14.2)', () => {
  const root = join(__dirname, '..', '..', '..')
  const files = [
    'components/domain/domain.ts',
    'components/domain/ShownValue.tsx',
    'components/domain/InstrumentCatalogue.tsx',
    'components/domain/InstrumentView.tsx',
    'components/domain/ProjectToDomain.tsx',
    'components/domain/DomainGate.tsx',
  ]
  const PARSING = [/parseFloat/, /parseInt/, /\bNumber\s*\(/, /\bNumber\./, /\bMath\./, /\.toFixed\s*\(/, /NumberFormat/, /\bBigInt\s*\(/, /(^|[=(,:?!&|{]|return)\s*\+\s*[A-Za-z_$(]/m]
  // A payload field declared with a primitive type is a hand-written mirror of the API.
  const HAND_WRITTEN = /\b(projection_id|instrument_id|canonical_record_id|accepted_count|projected_count|notional|business_key|cashflow_event_id|source_ordinal)\??\s*:\s*(string|number|boolean)\b/

  it.each(files)('%s never parses a value to a number', (file) => {
    const source = readFileSync(join(root, file), 'utf8')
    for (const pattern of PARSING) expect(source, `${file} matches ${pattern}`).not.toMatch(pattern)
  })

  it.each([...files, '../e2e/domain.spec.ts'])('%s declares no hand-written payload type', (file) => {
    expect(readFileSync(join(root, file), 'utf8')).not.toMatch(HAND_WRITTEN)
  })

  it('the hand-written-type grep would catch one', () => {
    expect(HAND_WRITTEN.test('type P = { projection_id: string }')).toBe(true)
    expect(HAND_WRITTEN.test('interface I { notional?: string }')).toBe(true)
  })

  it('the catalogue and the detail only read; the projection panel reads and POSTs', () => {
    const calls = (file: string) => new Set(readFileSync(join(root, file), 'utf8').match(/api\.[A-Z]+\(/g) ?? [])
    expect(calls('components/domain/InstrumentCatalogue.tsx')).toEqual(new Set(['api.GET(']))
    expect(calls('components/domain/InstrumentView.tsx')).toEqual(new Set(['api.GET(']))
    expect(calls('components/domain/ProjectToDomain.tsx')).toEqual(new Set(['api.GET(', 'api.POST(']))
  })
})
