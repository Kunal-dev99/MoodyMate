import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { ApiError } from '@/api/client'
import { ProjectionSummaryView } from '@/components/domain/ProjectToDomain'
import {
  CREDIT_SCHEMA_CODES,
  DOMAIN_SCHEMA_CODES,
  browseLink,
  canProject,
  counterpartyCatalogueHref,
  isCreditSchemaCode,
  isDomainSchemaCode,
  projectableSchemaCodes,
  projectionRefusal,
  rowLabel,
  rowsLabel,
  type Projection,
} from '@/components/domain/domain'

/*
 * FE-19.2: "Project to domain" (FE-14.2) for the two Feature 04 schema codes (D-37), taken from
 * the OpenAPI enum, and off with NEXT_PUBLIC_CREDIT_DOMAIN_UI (D-02). The server decides (D-04).
 */

const openapi = JSON.parse(readFileSync(fileURLToPath(new URL('../../../../../backend/openapi.json', import.meta.url)), 'utf8'))
const enumOf = (name: string): string[] => [...openapi.components.schemas[name].enum].sort()

const manifest = { manifest_id: 'm-1' } as unknown as NonNullable<Parameters<typeof canProject>[0]['manifest']>
const IMPORT = '22222222-2222-4222-8222-222222222222'

const projection = (over: Partial<Projection> = {}): Projection => ({
  projection_id: '11111111-1111-4111-8111-111111111111',
  import_id: IMPORT,
  import_status: 'COMPLETED',
  schema_code: 'credit_exposure',
  schema_version_id: '33333333-3333-4333-8333-333333333333',
  schema_version: 1,
  projector_version: 1,
  accepted_count: 14,
  projected_count: 14,
  summary: {
    records: 14,
    rows_projected: 14,
    new_identities: 14,
    attribute_values: 0,
    type_not_projected: {},
    unknown_values: { collateral_type: 1 },
    canonical_only_fields: [],
  },
  projected_at: '2026-10-19T10:00:00Z',
  projected_by: 'user:steward',
  ...over,
})

describe('the credit schema codes (FE-19.2, D-37)', () => {
  it('come from the OpenAPI DomainSchemaCode enum, which now lists them', () => {
    expect(enumOf('DomainSchemaCode')).toEqual(expect.arrayContaining(['counterparty', 'credit_exposure']))
    expect([...DOMAIN_SCHEMA_CODES].sort()).toEqual(enumOf('DomainSchemaCode'))
    for (const code of CREDIT_SCHEMA_CODES) {
      expect(enumOf('DomainSchemaCode')).toContain(code)
      expect(isDomainSchemaCode(code)).toBe(true)
      expect(isCreditSchemaCode(code)).toBe(true)
    }
    expect(isCreditSchemaCode('instrument')).toBe(false)
    expect(isCreditSchemaCode(null)).toBe(false)
    expect(rowLabel('counterparty')).toBe('counterparty version')
    expect(rowsLabel('credit_exposure')).toBe('credit-exposure versions')
  })

  it('offers the action for a validated counterparty or credit_exposure import', () => {
    for (const schema_code of ['counterparty', 'credit_exposure']) {
      expect(canProject({ status: 'COMPLETED', manifest, schema_code }, true), schema_code).toBe(true)
      expect(canProject({ status: 'QUARANTINED', manifest, schema_code }, true), schema_code).toBe(true)
      expect(canProject({ status: 'COMPLETED', manifest: null, schema_code }, true), schema_code).toBe(false)
      expect(canProject({ status: 'FAILED', manifest, schema_code }, true), schema_code).toBe(false)
    }
  })

  it('NEXT_PUBLIC_CREDIT_DOMAIN_UI=off restores the Phase 18 action: no credit codes, the three Feature 03 codes unchanged', () => {
    expect(projectableSchemaCodes(false).sort()).toEqual(['cashflow_event', 'instrument', 'position'])
    expect(projectableSchemaCodes(true).sort()).toEqual(enumOf('DomainSchemaCode'))
    for (const schema_code of ['counterparty', 'credit_exposure'])
      expect(canProject({ status: 'COMPLETED', manifest, schema_code }, false), schema_code).toBe(false)
    for (const schema_code of ['instrument', 'position', 'cashflow_event'])
      expect(canProject({ status: 'COMPLETED', manifest, schema_code }, false), schema_code).toBe(true)
  })

  it('names the server refusal when credit projection is switched off (409 projection_unsupported)', () => {
    const r = projectionRefusal(
      new ApiError(409, { code: 'projection_unsupported', message: 'off', details: null, correlation_id: 'cid-19', retryable: false }),
    )
    expect(r).toMatchObject({ title: 'This import cannot be projected', status: 409, code: 'projection_unsupported', cid: 'cid-19' })
  })
})

describe('the credit projection summary (FE-19.2)', () => {
  it('shows the server counts for a credit_exposure projection, labelled illustrative, with no catalogue link', () => {
    const html = renderToStaticMarkup(createElement(ProjectionSummaryView, { projection: projection(), origin: 'created' }))
    expect(html).toMatch(/data-testid="projection-accepted"[^>]*>14</)
    expect(html).toMatch(/data-testid="projection-projected"[^>]*>14</)
    expect(html).toContain('data-conserves="true"')
    expect(html).toContain('Projected credit-exposure versions')
    expect(html).toContain('one credit-exposure version per accepted canonical record')
    expect(html).toContain('data-testid="unknown-value" data-field="collateral_type" data-count="1"')
    expect(html).toContain('data-testid="projection-credit-schema"')
    expect(html).not.toContain('projection-browse')
  })

  it('links a counterparty projection to the Counterparties catalogue for its import, only while the credit UI is on', () => {
    expect(browseLink(projection({ schema_code: 'counterparty' }), true)).toEqual({
      href: counterpartyCatalogueHref(IMPORT),
      label: 'Browse the counterparties this import projected',
    })
    expect(counterpartyCatalogueHref(IMPORT)).toBe(`/domain/counterparties?import_id=${IMPORT}`)
    expect(browseLink(projection({ schema_code: 'counterparty' }), false)).toBeNull()
    expect(browseLink(projection({ schema_code: 'credit_exposure' }), true)).toBeNull()
    expect(browseLink(projection({ schema_code: 'instrument' }), false)?.href).toBe(`/domain?import_id=${IMPORT}`)
  })

  it('does not label a Feature 03 projection as a credit schema', () => {
    const html = renderToStaticMarkup(createElement(ProjectionSummaryView, { projection: projection({ schema_code: 'position' }), origin: 'existing' }))
    expect(html).not.toContain('projection-credit-schema')
  })
})
