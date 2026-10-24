import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Schemas } from '@/api/client'
import { FxConsolidationNotice } from '@/components/calculations/FxConsolidationNotice'
import { ContractTerms, VersionsTable } from '@/components/domain/TermsParts'
import type { InstrumentVersion, Projection } from '@/components/domain/domain'
import {
  EFFECTIVE_FROM_NOT_WRITTEN,
  FX_BLOCKED_NOTICE,
  TERMS_NOT_IN_CONTRACT,
  TERMS_NOT_WRITTEN,
  TERM_FIELDS,
  UNKNOWN_VALUE_TEXT,
  rateKind,
  termsState,
  termsSummary,
  type InstrumentTerms,
} from '@/components/domain/terms'

/*
 * FE-32.1 (D-61): the instrument detail's "Contract terms" panel (fixed, floating, absent and
 * unknown terms) with PROVISIONAL badges, the versions table ("Effective from" beside "Imported
 * at"), the FX consolidation notice of the ladder and LCR views, and
 * NEXT_PUBLIC_INSTRUMENT_TERMS_UI=off restoring the Phase 31 UI.
 */

const openapi = JSON.parse(readFileSync(fileURLToPath(new URL('../../../../../backend/openapi.json', import.meta.url)), 'utf8'))
const text = (html: string) => html.replace(/<[^>]+>|<!-- -->/g, '')
const masked = { masked: true as const, classification: 'confidential' }

const terms = (over: Partial<InstrumentTerms> = {}): InstrumentTerms => ({
  term_version_id: '99999999-9999-4999-8999-999999999999',
  rate_type: null,
  reference_index: null,
  spread: null,
  reset_frequency: null,
  next_reset_date: null,
  cap_rate: null,
  floor_rate: null,
  day_count: null,
  payment_frequency: null,
  lifecycle_state: null,
  rate_unit: 'percent',
  unknown_fields: [],
  provisional: true,
  created_at: '2026-10-24T08:00:00Z',
  ...over,
})

const FIXED = terms({ rate_type: 'FIXED', day_count: 'ACT_365F', payment_frequency: 'P3M', lifecycle_state: 'ACTIVE' })
const FLOATING = terms({
  rate_type: 'FLOATING',
  reference_index: 'EURIBOR_3M',
  spread: '1.250000',
  reset_frequency: 'P3M',
  next_reset_date: '2027-01-15',
  cap_rate: '6.000000',
  floor_rate: '0.000000',
  day_count: 'ACT_360',
  payment_frequency: 'P3M',
  lifecycle_state: 'ACTIVE',
})

const version = (over: Partial<InstrumentVersion> = {}): InstrumentVersion => ({
  version_id: '11111111-1111-4111-8111-111111111111',
  version: 1,
  import_id: '22222222-2222-4222-8222-222222222222',
  projection_id: '33333333-3333-4333-8333-333333333333',
  canonical_record_id: '44444444-4444-4444-8444-444444444444',
  source_record_id: '55555555-5555-4555-8555-555555555555',
  source_ordinal: 1,
  schema_version_id: '66666666-6666-4666-8666-666666666666',
  schema_version: 3,
  payload_sha256: 'a'.repeat(64),
  projected_at: '2026-10-24T08:00:00Z',
  instrument_type: 'LOAN',
  instrument_type_known: true,
  currency: 'EUR',
  notional: '1000000.00',
  interest_rate: '7.250000',
  interest_rate_unit: 'percent',
  maturity_date: '2030-06-30',
  maturity_typed: true,
  effective_from: '2026-01-01',
  imported_at: '2026-10-24T07:59:00Z',
  terms_typed: true,
  terms: FIXED,
  attributes: [],
  cash_flows: [],
  ...over,
})

const panel = (v: InstrumentVersion) => renderToStaticMarkup(createElement(ContractTerms, { version: v }))

describe('contract terms on the instrument detail (FE-32.1, D-61 §1-§4)', () => {
  it('shows every term field of the API, in the order of the panel', () => {
    const apiFields = Object.keys(openapi.components.schemas.InstrumentTermsOut.properties)
    for (const f of TERM_FIELDS) expect(apiFields).toContain(f.key)
    // Every term value the API sends is on the panel (the rest are ids, unit, flags and time).
    const meta = ['term_version_id', 'rate_unit', 'unknown_fields', 'provisional', 'created_at']
    expect(apiFields.filter((k) => !meta.includes(k)).sort()).toEqual(TERM_FIELDS.map((f) => f.key).sort())
  })

  it('a fixed loan: FIXED, ACT_365F, P3M as sent, labelled fixed, with PROVISIONAL badges and no "not in vocabulary"', () => {
    const state = termsState(version())
    expect(state).toMatchObject({ kind: 'recorded', rate: 'FIXED', unknown: [] })
    const html = panel(version())
    expect(html).toContain('data-state="recorded"')
    expect(html).toContain('data-rate="FIXED"')
    const t = text(html)
    expect(t).toContain('Fixed rate')
    for (const v of ['FIXED', 'ACT_365F', 'P3M', 'ACTIVE']) expect(t).toContain(v)
    expect(html).toContain('data-testid="provisional-badge"')
    expect(html).not.toContain('data-testid="term-unknown"')
    // Fields the row does not state are said to be so, never defaulted.
    expect(html).toMatch(/data-testid="term-reference_index" data-empty="true">Not stated/)
    expect(termsSummary(state)).toBe('Fixed rate')
  })

  it('a floating loan: index, spread, P3M reset, next reset, cap and floor as exact text with the pinned rate unit', () => {
    const html = panel(version({ terms: FLOATING }))
    expect(html).toContain('data-rate="FLOATING"')
    const t = text(html)
    expect(t).toContain('Floating rate')
    for (const v of ['EURIBOR_3M', '1.250000', 'P3M', '2027-01-15', '6.000000', '0.000000', 'ACT_360']) expect(t).toContain(v)
    // The decimal terms carry the unit; the server's text is never re-formatted.
    expect(html).toMatch(/data-testid="term-spread"[^>]*>1\.250000<\/span><span class="text-muted-foreground">percent/)
    expect(html).toMatch(/data-testid="term-floor_rate"[^>]*>0\.000000</)
    expect(html).not.toContain('data-testid="term-unknown"')
  })

  it('absent terms: a contract without terms, and a rate-terms version with no term row (inconsistent), each say so', () => {
    const v1 = version({ terms_typed: false, terms: null, schema_version: 2 })
    expect(termsState(v1)).toEqual({ kind: 'not-in-contract' })
    const notInContract = panel(v1)
    expect(notInContract).toContain('data-state="not-in-contract"')
    expect(text(notInContract)).toBe(`Contract terms${TERMS_NOT_IN_CONTRACT}`)
    expect(notInContract).not.toContain('provisional-badge')

    const inconsistent = version({ terms: null })
    expect(termsState(inconsistent)).toEqual({ kind: 'not-written' })
    const notWritten = panel(inconsistent)
    expect(notWritten).toContain('data-state="not-written"')
    expect(text(notWritten)).toContain(TERMS_NOT_WRITTEN)
    expect(notWritten).toContain('TERMS_INCONSISTENT')
    // Never shown as a fixed or floating rate.
    expect(text(notWritten)).not.toMatch(/Fixed rate|Floating rate/)
    expect(termsSummary(termsState(inconsistent))).toBe('Not recorded (inconsistent)')
    expect(termsSummary(termsState(v1))).toBe('Not in contract')
  })

  it('unknown vocabulary values are kept as given and flagged by field, as the server named them', () => {
    const unknown = terms({ rate_type: 'STEPPED', day_count: 'ACT_999', payment_frequency: 'P3M', unknown_fields: ['rate_type', 'day_count'] })
    expect(rateKind(unknown)).toBe('unknown')
    const html = panel(version({ terms: unknown }))
    expect(html).toContain('data-rate="unknown"')
    expect(text(html)).toContain('STEPPED')
    expect(text(html)).toContain('ACT_999')
    expect(html.match(/data-testid="term-unknown"/g)).toHaveLength(2)
    expect(html).toContain('data-field="day_count" title="Outside the seeded')
    expect(text(html)).toContain(UNKNOWN_VALUE_TEXT)
    expect(text(html)).not.toMatch(/Fixed rate|Floating rate/)
    // Only the server's list flags a value: an unflagged one is shown as given, without the badge.
    expect(rateKind(terms({ rate_type: 'STEPPED' }))).toBe('other')
    expect(rateKind(terms({ rate_type: 'FIXED', unknown_fields: ['rate_type'] }))).toBe('unknown')
  })

  it('masked term values show the marker, never a value or "not stated"', () => {
    const hidden = terms({ rate_type: masked, spread: masked })
    expect(rateKind(hidden)).toBe('masked')
    const html = panel(version({ terms: hidden }))
    expect(html).toContain('data-rate="masked"')
    expect(html).toMatch(/data-testid="term-spread" data-masked="true"/)
    expect(html).not.toMatch(/data-testid="term-spread" data-empty/)
  })
})

describe('versions table: effective from beside imported at (FE-32.1, D-61 §5)', () => {
  it('lists every version in server order with both dates kept apart, and its terms state', () => {
    // No term row written (inconsistent terms): `effective_from` lives on the term row, so it is null too.
    const later = version({
      version_id: 'later',
      version: 2,
      effective_from: null,
      imported_at: '2026-10-24T09:00:00Z',
      terms: null,
    })
    const html = renderToStaticMarkup(createElement(VersionsTable, { versions: [version(), later] }))
    expect(text(html)).toContain('Effective from')
    expect(text(html)).toContain('Imported at')
    const rows = html.match(/data-testid="versions-row" data-version="(\d)"/g) ?? []
    expect(rows.map((r) => r.slice(-2, -1))).toEqual(['1', '2'])
    expect(html).toContain('data-value="2026-10-24T09:00:00Z"')
    expect(text(html)).toContain('2026-01-01')
    expect(EFFECTIVE_FROM_NOT_WRITTEN).toBe('Not recorded: terms inconsistent')
    expect(html).toContain(`data-testid="version-effective-from" data-empty="true">${EFFECTIVE_FROM_NOT_WRITTEN}`)
    expect(html.match(/data-testid="version-effective-from" data-empty="true">Not stated/g)).toBeNull()
    expect(html).toMatch(/data-testid="version-terms-summary" data-state="not-written"/)
  })

  it('a masked or absent effective date is its own state', () => {
    const html = renderToStaticMarkup(
      createElement(VersionsTable, { versions: [version({ effective_from: masked }), version({ version_id: 'x', version: 2, effective_from: null })] }),
    )
    expect(html).toMatch(/data-testid="version-effective-from" data-masked="true"/)
    expect(html).toMatch(/data-testid="version-effective-from" data-empty="true">Not stated/)
  })
})

describe('FX consolidation notice on the ladder and LCR views (FE-32.1, D-61 §6)', () => {
  it('says per-currency only and blocked, with the context value kept; an absent value still shows it', () => {
    expect(FX_BLOCKED_NOTICE).toBe(
      'Per-currency results only; FX consolidation is blocked until an approved rate source and conversion policy exist',
    )
    expect(openapi.components.schemas.ResultContext.properties.consolidation.const).toBe('BLOCKED')
    const html = renderToStaticMarkup(createElement(FxConsolidationNotice, { consolidation: 'BLOCKED', source: 'liquidity' }))
    expect(text(html)).toBe(FX_BLOCKED_NOTICE)
    expect(html).toContain('data-consolidation="BLOCKED"')
    const none = renderToStaticMarkup(createElement(FxConsolidationNotice, { consolidation: undefined, source: 'maturity-ladder' }))
    expect(text(none)).toBe(FX_BLOCKED_NOTICE)
    expect(none).toContain('data-consolidation="unknown"')
  })

  it('both result views render it behind the switch, after the result context, and nothing converts', () => {
    const dir = fileURLToPath(new URL('../../calculations/', import.meta.url))
    for (const [file, source] of [
      ['LiquidityView.tsx', 'liquidity'],
      ['MaturityLadderView.tsx', 'maturity-ladder'],
    ]) {
      const src = readFileSync(join(dir, file), 'utf8')
      expect(src).toContain(`{INSTRUMENT_TERMS_UI_ENABLED && <FxConsolidationNotice consolidation=`)
      expect(src).toContain(`source="${source}" />}`)
    }
    // No FX read or conversion in the result views or the notice (D-61 §6).
    for (const file of ['LiquidityView.tsx', 'MaturityLadderView.tsx', 'FxConsolidationNotice.tsx'])
      expect(readFileSync(join(dir, file), 'utf8')).not.toMatch(/fx_rate|\/api\/v1\/fx|\bconvert\w*\(|\bfxRate/i)
  })
})

const context = (): Schemas['ResultContext'] => ({
  as_of_date: '2026-12-31',
  consolidation: 'BLOCKED',
  currency: { basis: 'PER_CURRENCY_NO_FX', codes: ['EUR'] },
  methodology: { module_code: 'lcr_hqla', module_version: 1, engine_version: '1', rule_versions: [] },
  units: { amount: 'exact decimal text', days: 'calendar days', ratio: 'percent' },
  validation_status: 'ILLUSTRATIVE',
  statement: 'Illustrative prototype.',
})

const termsProjection = (): Projection => ({
  projection_id: '11111111-1111-4111-8111-111111111111',
  import_id: '22222222-2222-4222-8222-222222222222',
  import_status: 'COMPLETED',
  schema_code: 'instrument',
  schema_version_id: '33333333-3333-4333-8333-333333333333',
  schema_version: 3,
  projector_version: 3,
  accepted_count: 6,
  projected_count: 6,
  summary: {
    records: 6,
    rows_projected: 6,
    new_identities: 5,
    attribute_values: 0,
    type_not_projected: {},
    unknown_values: { day_count: 1 },
    canonical_only_fields: [],
    term_rows: 3,
    terms_inconsistent: 3,
  },
  projected_at: '2026-10-24T08:00:00Z',
  projected_by: 'user:steward',
})

describe('NEXT_PUBLIC_INSTRUMENT_TERMS_UI (FE-32.1, D-02)', () => {
  afterEach(() => {
    vi.unstubAllEnvs()
    vi.resetModules()
  })

  it('on: the context panel shows the server consolidation value and the projection summary its term counts', async () => {
    vi.resetModules()
    expect((await import('@/config/features')).INSTRUMENT_TERMS_UI_ENABLED).toBe(true)
    const { ResultContextPanel } = await import('@/components/calculations/ResultContextPanel')
    const html = renderToStaticMarkup(createElement(ResultContextPanel, { context: context(), source: 'liquidity', compact: true }))
    expect(html).toMatch(/data-testid="context-consolidation"><span class="font-mono">BLOCKED/)
    const { ProjectionSummaryView } = await import('@/components/domain/ProjectToDomain')
    const summary = renderToStaticMarkup(createElement(ProjectionSummaryView, { projection: termsProjection(), origin: 'created' }))
    expect(summary).toMatch(/data-testid="projection-term-rows"><span class="font-mono">3/)
    expect(summary).toMatch(/data-testid="projection-terms-inconsistent"><span class="font-mono">3/)
  })

  it('on: a projector 1 or 2 summary (no term counts) shows none, as in Phase 31', async () => {
    vi.resetModules()
    const { ProjectionSummaryView } = await import('@/components/domain/ProjectToDomain')
    const p = termsProjection()
    const { term_rows: _r, terms_inconsistent: _i, ...older } = p.summary
    const html = renderToStaticMarkup(createElement(ProjectionSummaryView, { projection: { ...p, projector_version: 2, summary: older }, origin: 'created' }))
    expect(html).not.toContain('projection-term-rows')
    expect(html).not.toContain('projection-terms-inconsistent')
  })

  it('off: the Phase 31 context panel and projection summary return exactly; the nav is unchanged', async () => {
    vi.resetModules()
    const navOn = await import('@/config/navigation')
    const navOnJson = JSON.stringify([navOn.mainWorkspaces, navOn.advancedWorkspaces, navOn.adminWorkspaces, navOn.domainSections])
    vi.stubEnv('NEXT_PUBLIC_INSTRUMENT_TERMS_UI', 'off')
    vi.resetModules()
    expect((await import('@/config/features')).INSTRUMENT_TERMS_UI_ENABLED).toBe(false)
    const { ResultContextPanel } = await import('@/components/calculations/ResultContextPanel')
    const html = renderToStaticMarkup(createElement(ResultContextPanel, { context: context(), source: 'liquidity', compact: true }))
    expect(html).not.toContain('context-consolidation')
    expect(html).toContain('data-testid="context-currency-basis"')
    const { ProjectionSummaryView } = await import('@/components/domain/ProjectToDomain')
    const summary = renderToStaticMarkup(createElement(ProjectionSummaryView, { projection: termsProjection(), origin: 'created' }))
    expect(summary).not.toContain('projection-term-rows')
    expect(summary).not.toContain('projection-terms-inconsistent')
    // FE-32.1 adds no nav entry: the switch leaves the sidebar and tabs as they are.
    const navOff = await import('@/config/navigation')
    expect(JSON.stringify([navOff.mainWorkspaces, navOff.advancedWorkspaces, navOff.adminWorkspaces, navOff.domainSections])).toBe(navOnJson)
  })

  it('the instrument detail gates the terms panel and the versions table on the switch', () => {
    const src = readFileSync(fileURLToPath(new URL('../InstrumentView.tsx', import.meta.url)), 'utf8')
    expect(src).toContain('{INSTRUMENT_TERMS_UI_ENABLED && <ContractTerms version={v} />}')
    expect(src).toMatch(/\{INSTRUMENT_TERMS_UI_ENABLED && \(\s*<PageSection\s+icon=\{History\}\s+title="Versions"/)
  })
})
