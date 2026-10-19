import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CreditValueCell } from '@/components/credit/CreditValueCell'
import { CreditVocabularies } from '@/components/credit/CreditVocabularies'
import {
  NO_COUNTERPARTY_FILTERS,
  NO_EXPOSURE_FILTERS,
  VOCABULARY_KINDS,
  counterpartyHref,
  counterpartyQuery,
  exposureHref,
  exposureQuery,
  groupVocabularies,
  keyText,
  latestExposures,
  problemText,
  valueText,
  type ExposureVersion,
  type VocabularyEntry,
} from '@/components/credit/credit'
import { activeSection } from '@/components/registry/RegistryTabs'

/*
 * FE-19.1: the Counterparties tab under /domain. Fixtures use the generated types
 * (CreditExposureVersionOut, CreditVocabularyEntryOut); enums are tested against openapi.json.
 */

const openapi = JSON.parse(readFileSync(fileURLToPath(new URL('../../../../../backend/openapi.json', import.meta.url)), 'utf8'))
const enumOf = (name: string): string[] => [...openapi.components.schemas[name].enum].sort()

const IMPORT_ID = '22222222-2222-4222-8222-222222222222'
const MASKED = { masked: true as const, classification: 'confidential' }

const exposure = (over: Partial<ExposureVersion> = {}): ExposureVersion => ({
  credit_exposure_id: '33333333-3333-4333-8333-333333333333',
  version: 1,
  version_id: '44444444-4444-4444-8444-444444444441',
  business_key: 'EXP-0001',
  counterparty_key: 'CP-0001',
  instrument_key: null,
  as_of_date: '2026-09-30',
  currency: 'EUR',
  facility_type: 'TERM_LOAN',
  facility_type_known: true,
  drawn_amount: '1000000.00',
  undrawn_amount: '0.00',
  days_past_due: 0,
  default_flag: false,
  origination_date: '2022-03-15',
  origination_grade: 'G03',
  origination_grade_known: true,
  maturity_date: '2030-03-15',
  effective_interest_rate: '0.045000',
  effective_interest_rate_unit: 'fraction',
  collateral_type: 'REAL_ESTATE',
  collateral_type_known: true,
  collateral_value: '600000.00',
  import_id: IMPORT_ID,
  source_ordinal: 1,
  canonical_record_id: '55555555-5555-4555-8555-555555555555',
  source_record_id: '66666666-6666-4666-8666-666666666666',
  schema_version: 1,
  schema_version_id: '77777777-7777-4777-8777-777777777777',
  projection_id: '88888888-8888-4888-8888-888888888888',
  payload_sha256: 'a'.repeat(64),
  projected_at: '2026-10-19T10:00:00Z',
  ...over,
})

const entry = (vocabulary: VocabularyEntry['vocabulary'], code: string, over: Partial<VocabularyEntry> = {}): VocabularyEntry => ({
  vocabulary,
  code,
  name: code,
  rank: null,
  is_default_grade: null,
  provisional: true,
  ...over,
})

describe('credit helpers (FE-19.1)', () => {
  it('the vocabulary kinds equal the OpenAPI enum', () => {
    expect([...VOCABULARY_KINDS].sort()).toEqual(enumOf('CreditVocabularyKind'))
  })

  it('groups vocabularies in a fixed order, empty kinds kept', () => {
    const grouped = groupVocabularies([entry('facility_type', 'REVOLVER'), entry('rating_grade', 'G01', { rank: 1 })])
    expect(grouped.map(([k]) => k)).toEqual(['rating_grade', 'credit_segment', 'facility_type', 'collateral_type'])
    expect(grouped[0][1].map((e) => e.code)).toEqual(['G01'])
    expect(grouped[1][1]).toEqual([])
  })

  it('sends only well-formed counterparty filters', () => {
    expect(counterpartyQuery(NO_COUNTERPARTY_FILTERS)).toEqual({ query: {}, problems: [] })
    expect(counterpartyQuery({ segment: ' SME ', country: 'de', source_id: '', import_id: IMPORT_ID })).toEqual({
      query: { segment: 'SME', country: 'DE', import_id: IMPORT_ID },
      problems: [],
    })
    expect(counterpartyQuery({ segment: '', country: 'DEU', source_id: 'x', import_id: 'abc' }).problems).toEqual(['country', 'source_id', 'import_id'])
  })

  it('sends only well-formed exposure filters', () => {
    expect(exposureQuery(NO_EXPOSURE_FILTERS)).toEqual({ query: {}, problems: [] })
    expect(exposureQuery({ currency: 'usd', facility_type: 'REVOLVER', counterparty_key: ' CP-0002 ', import_id: '' })).toEqual({
      query: { currency: 'USD', facility_type: 'REVOLVER', counterparty_key: 'CP-0002' },
      problems: [],
    })
    expect(exposureQuery({ currency: 'EU', facility_type: '', counterparty_key: '', import_id: 'nope' }).problems).toEqual(['currency', 'import_id'])
    expect(problemText(['country', 'import_id'])).toBe('country (two letters, ISO 3166-1), import ID (a UUID)')
  })

  it('shows values as given: decimal text, integer digits, Yes/No; masked and absent are null', () => {
    expect(valueText('1000000.00')).toBe('1000000.00')
    expect(valueText('0.045000')).toBe('0.045000')
    expect(valueText(95)).toBe('95')
    expect(valueText(0)).toBe('0')
    expect(valueText(true)).toBe('Yes')
    expect(valueText(false)).toBe('No')
    expect(valueText(null)).toBeNull()
    expect(valueText(undefined)).toBeNull()
    expect(valueText(MASKED)).toBeNull()
  })

  it('keeps one row per exposure, its latest version, with the version count', () => {
    const a1 = exposure()
    const a2 = exposure({ version: 2, version_id: '44444444-4444-4444-8444-444444444442' })
    const b1 = exposure({ credit_exposure_id: '99999999-9999-4999-8999-999999999999', business_key: 'EXP-0002' })
    const rows = latestExposures([a1, b1, a2])
    expect(rows.map((r) => [r.latest.business_key, r.latest.version, r.count])).toEqual([
      ['EXP-0001', 2, 2],
      ['EXP-0002', 1, 1],
    ])
  })

  it('links under /domain/counterparties and names a masked key by its id', () => {
    expect(counterpartyHref('abc')).toBe('/domain/counterparties/abc')
    expect(exposureHref('abc')).toBe('/domain/counterparties/exposures/abc')
    expect(keyText('CP-0001', IMPORT_ID)).toBe('CP-0001')
    expect(keyText(MASKED, IMPORT_ID)).toBe('22222222…')
  })

  it('the Counterparties tab is active on its catalogue, a counterparty and an exposure', async () => {
    const { domainSections } = await import('@/config/navigation')
    expect(activeSection('/domain/counterparties', domainSections)).toBe('counterparties')
    expect(activeSection('/domain/counterparties/abc', domainSections)).toBe('counterparties')
    expect(activeSection('/domain/counterparties/exposures/abc', domainSections)).toBe('counterparties')
    expect(activeSection('/domain', domainSections)).toBe('instruments')
  })
})

describe('credit rendering (FE-19.1)', () => {
  it('renders a masked drawn amount as "masked", never blank', () => {
    const html = renderToStaticMarkup(createElement(CreditValueCell, { value: MASKED, testId: 'drawn' }))
    expect(html).toContain('data-masked="true"')
    expect(html).toContain('masked')
    expect(html).toContain('Masked by policy: confidential data')
  })

  it('renders clear values exactly, and absent ones as the empty marker', () => {
    expect(renderToStaticMarkup(createElement(CreditValueCell, { value: '250000.00' }))).toContain('>250000.00<')
    expect(renderToStaticMarkup(createElement(CreditValueCell, { value: 35 }))).toContain('>35<')
    expect(renderToStaticMarkup(createElement(CreditValueCell, { value: true }))).toContain('>Yes<')
    expect(renderToStaticMarkup(createElement(CreditValueCell, { value: null }))).toContain('data-empty="true"')
  })

  it('badges provisional vocabulary values "Provisional / illustrative", and the default grade', () => {
    const html = renderToStaticMarkup(
      createElement(CreditVocabularies, {
        items: [entry('rating_grade', 'G01', { rank: 1, is_default_grade: false }), entry('rating_grade', 'D', { rank: 9, is_default_grade: true }), entry('credit_segment', 'SME', { provisional: false })],
      }),
    )
    expect(html.match(/data-testid="provisional-badge"/g)?.length).toBe(3) // the section header and G01, D
    expect(html).toContain('Provisional / illustrative')
    expect(html).toContain('data-testid="default-grade"')
    expect(html).toContain('data-code="SME" data-provisional="false"')
  })
})

describe('placeholders relabelled (FE-19.1)', () => {
  afterEach(() => {
    vi.resetModules()
    vi.doUnmock('@/components/auth/SessionProvider')
    vi.doUnmock('next/navigation')
  })

  it('scenarios and reports are "WS4 (planned)", and no workspace still says Feature 04', async () => {
    vi.resetModules()
    const { findWorkspace, mainWorkspaces } = await import('@/config/navigation')
    for (const slug of ['scenarios', 'reports']) expect(findWorkspace(slug)?.planned).toEqual({ phase: 'WS4', workstream: 'planned' })
    expect(mainWorkspaces.filter((w) => w.planned?.phase === 'Feature 04')).toEqual([])
  })

  it('the placeholder page shows the new label', async () => {
    vi.doMock('@/components/auth/SessionProvider', () => ({ useCurrentSession: () => ({ has: () => true }) }))
    vi.doMock('next/navigation', () => ({ usePathname: () => '/reports' }))
    vi.resetModules()
    const { PlannedWorkspace } = await import('@/components/common/PlannedWorkspace')
    const html = renderToStaticMarkup(createElement(PlannedWorkspace, { slug: 'reports' }))
    expect(html).toContain('Scheduled for <strong>WS4</strong> (planned)')
    expect(html.replace(/<[^>]+>|<!-- -->/g, '')).toContain('Scheduled for WS4 (planned)')
    expect(html).not.toContain('(planned) (')
    expect(html).not.toContain('Feature 04')
  })
})

describe('credit reversibility (FE-19.1, D-02)', () => {
  afterEach(() => {
    vi.unstubAllEnvs()
    vi.resetModules()
    vi.doUnmock('next/navigation')
  })

  it('on: a Counterparties tab (domain:read) after Instruments, and the domain entry names it', async () => {
    vi.resetModules()
    const { domainSections, mainWorkspaces } = await import('@/config/navigation')
    expect(domainSections.map((s) => s.slug)).toEqual(['instruments', 'counterparties', 'rules', 'modules', 'assumption-sets'])
    expect(domainSections.find((s) => s.slug === 'counterparties')).toEqual({
      slug: 'counterparties',
      href: '/domain/counterparties',
      label: 'Counterparties',
      permission: 'domain:read',
    })
    expect(mainWorkspaces.find((w) => w.slug === 'domain')?.description).toMatch(/Counterparties and credit exposures/)
  })

  it('NEXT_PUBLIC_CREDIT_DOMAIN_UI=off restores the Phase 18 tabs and domain entry exactly', async () => {
    vi.stubEnv('NEXT_PUBLIC_CREDIT_DOMAIN_UI', 'off')
    vi.resetModules()
    const { domainSections, mainWorkspaces } = await import('@/config/navigation')
    expect(domainSections.map((s) => s.href)).toEqual(['/domain', '/domain/rules', '/domain/modules', '/domain/assumption-sets'])
    expect(mainWorkspaces.find((w) => w.slug === 'domain')?.description).toBe(
      'Instrument catalogue with its lineage to imports, and the registry: methodology rules and their versions, calculation modules and assumption sets.',
    )
  })

  it('with the registry off, the tab strip is Instruments and Counterparties; both off, none (Phase 15)', async () => {
    vi.stubEnv('NEXT_PUBLIC_RULES_UI', 'off')
    vi.resetModules()
    expect((await import('@/config/navigation')).domainSections.map((s) => s.slug)).toEqual(['instruments', 'counterparties'])
    vi.stubEnv('NEXT_PUBLIC_CREDIT_DOMAIN_UI', 'off')
    vi.resetModules()
    expect((await import('@/config/navigation')).domainSections).toEqual([])
  })

  it('with the domain workspace planned, no tabs at all', async () => {
    vi.stubEnv('NEXT_PUBLIC_DOMAIN_UI', 'off')
    vi.resetModules()
    expect((await import('@/config/navigation')).domainSections).toEqual([])
  })

  it('the /domain/counterparties routes are a 404 while off, and render while on', async () => {
    const notFound = vi.fn(() => {
      throw new Error('NEXT_NOT_FOUND')
    })
    vi.doMock('next/navigation', () => ({ notFound }))
    const child = createElement('p', null, 'counterparties')

    vi.resetModules()
    const on = await import('@/components/credit/CreditGate')
    expect(renderToStaticMarkup(createElement(on.CreditGate, null, child))).toBe('<p>counterparties</p>')
    expect(notFound).not.toHaveBeenCalled()

    vi.stubEnv('NEXT_PUBLIC_CREDIT_DOMAIN_UI', 'off')
    vi.resetModules()
    const off = await import('@/components/credit/CreditGate')
    expect(() => renderToStaticMarkup(createElement(off.CreditGate, null, child))).toThrow('NEXT_NOT_FOUND')
    expect(notFound).toHaveBeenCalledTimes(1)
  })
})

/**
 * Amounts, rates and dates are decimal text from the API: the credit code is grepped for every
 * way JavaScript turns text into a float (as FE-14.1), for hand-written payload types, and for
 * writes (the Counterparties tab only reads).
 */
describe('credit code hygiene (FE-19.1)', () => {
  const root = join(__dirname, '..', '..', '..')
  const files = [
    'components/credit/credit.ts',
    'components/credit/CreditGate.tsx',
    'components/credit/CreditParts.tsx',
    'components/credit/CreditValueCell.tsx',
    'components/credit/CreditVocabularies.tsx',
    'components/credit/CounterpartyCatalogue.tsx',
    'components/credit/CounterpartyView.tsx',
    'components/credit/ExposureView.tsx',
    'app/(platform)/domain/counterparties/layout.tsx',
    'app/(platform)/domain/counterparties/page.tsx',
    'app/(platform)/domain/counterparties/[counterpartyId]/page.tsx',
    'app/(platform)/domain/counterparties/exposures/[exposureId]/page.tsx',
  ]
  const PARSING = [/parseFloat/, /parseInt/, /\bNumber\s*\(/, /\bNumber\./, /\bMath\./, /\.toFixed\s*\(/, /NumberFormat/, /\bBigInt\s*\(/, /(^|[=(,:?!&|{]|return)\s*\+\s*[A-Za-z_$(]/m]
  // A payload field declared with a primitive type is a hand-written mirror of the API.
  const HAND_WRITTEN =
    /\b(counterparty_id|credit_exposure_id|counterparty_name|drawn_amount|undrawn_amount|collateral_value|days_past_due|default_flag|business_key|canonical_record_id|source_ordinal|vocabulary)\??\s*:\s*(string|number|boolean)\b/

  it.each(files)('%s never parses a value to a number', (file) => {
    const source = readFileSync(join(root, file), 'utf8')
    for (const pattern of PARSING) expect(source, `${file} matches ${pattern}`).not.toMatch(pattern)
  })

  it.each([...files, '../e2e/counterparties.spec.ts'])('%s declares no hand-written payload type', (file) => {
    expect(readFileSync(join(root, file), 'utf8')).not.toMatch(HAND_WRITTEN)
  })

  it('the hand-written-type grep would catch one', () => {
    expect(HAND_WRITTEN.test('type P = { drawn_amount: string }')).toBe(true)
    expect(HAND_WRITTEN.test('interface I { counterparty_id?: string }')).toBe(true)
  })

  it('the Counterparties tab only reads', () => {
    for (const file of files) {
      const calls = new Set(readFileSync(join(root, file), 'utf8').match(/api\.[A-Z]+\(/g) ?? [])
      expect([...calls].filter((c) => c !== 'api.GET('), file).toEqual([])
    }
  })
})
