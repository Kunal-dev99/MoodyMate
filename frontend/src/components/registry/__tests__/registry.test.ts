import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ModuleCard } from '@/components/registry/ModuleRegistry'
import { ParameterValues } from '@/components/registry/ParameterView'
import { activeSection } from '@/components/registry/RegistryTabs'
import {
  CATEGORY_TEXT,
  IMPLEMENTATION_STATUSES,
  RULE_VERSION_STATUSES,
  bucketRows,
  defaultDiffPair,
  defaultVersion,
  diffParameters,
  flattenParameters,
  groupModules,
  kindText,
  leafText,
  parameterKind,
  usedByText,
  type ModuleVersion,
  type RuleVersion,
} from '@/components/registry/registry'
import type { WorkspaceSection } from '@/config/navigation'

// The OpenAPI document the types are generated from: enums are tested against it (D-23).
const openapi = JSON.parse(readFileSync(fileURLToPath(new URL('../../../../../backend/openapi.json', import.meta.url)), 'utf8'))
const enumOf = (name: string): string[] => [...openapi.components.schemas[name].enum].sort()

const bucket = (code: string, lower: number, upper: number | null) => ({
  code,
  label: upper === null ? `(${lower},inf)` : `(${lower},${upper}]`,
  lower_exclusive: lower,
  upper_inclusive: upper,
})
const SEEDED = { buckets: [bucket('B01', 0, 1), bucket('B02', 1, 7), bucket('B03', 7, 30), bucket('B04', 30, null)] }

const version = (n: number, over: Partial<RuleVersion> = {}): RuleVersion => ({
  rule_version_id: `00000000-0000-4000-8000-00000000000${n}`,
  rule_id: '11111111-1111-4111-8111-111111111111',
  rule_code: 'maturity_buckets',
  version: n,
  status: 'ACTIVE',
  parameters: SEEDED,
  parameters_sha256: 'a'.repeat(64),
  change_summary: null,
  authored_by: '0018_calculations',
  author_user_id: null,
  approved_by: '0018_calculations',
  approved_at: '2026-10-17T10:00:00Z',
  decided_by: null,
  decided_at: null,
  decision_comment: null,
  superseded_at: null,
  effective_from: null,
  effective_to: null,
  provisional: true,
  illustrative: true,
  seeded: true,
  created_at: '2026-10-17T10:00:00Z',
  run_count: 0,
  runs: [],
  ...over,
})

/** A PLANNED module version, as GET /calculation-modules would list an extension point (Phase 18). */
const PLANNED: ModuleVersion = {
  module_id: '22222222-2222-4222-8222-222222222222',
  module_version_id: '33333333-3333-4333-8333-333333333333',
  code: 'irrbb_eve',
  name: 'IRRBB economic value (planned)',
  description: 'Declared extension point for interest-rate risk in the banking book.',
  version: 1,
  implementation_status: 'PLANNED',
  runnable: false,
  engine_version: 'none',
  input_contract: { schemas: [{ schema_code: 'instrument', required: true, fields: ['instrument_id'] }], rules: [], assumption_keys: [], parameters: [] },
  output_contract: { results: [], intermediates: [], warning_codes: [] },
  rules: [],
  assumption_set: null,
  provisional: true,
  illustrative: true,
  statement: 'Illustrative prototype. Not a regulatory calculation; no parity with any vendor system is claimed.',
  validation_status: 'ILLUSTRATIVE',
  created_at: '2026-10-17T10:00:00Z',
}

const AVAILABLE: ModuleVersion = { ...PLANNED, code: 'maturity_ladder', name: 'Maturity ladder', implementation_status: 'AVAILABLE', runnable: true, engine_version: 'mm-calc 1.0' }

describe('registry vocabularies follow the API enums (FE-16.1)', () => {
  it('styles every rule-version status, category and implementation status the backend has', () => {
    expect([...RULE_VERSION_STATUSES].sort()).toEqual(enumOf('RuleVersionStatus'))
    expect(Object.keys(CATEGORY_TEXT).sort()).toEqual(enumOf('RuleCategory'))
    expect([...IMPLEMENTATION_STATUSES].sort()).toEqual(enumOf('ImplementationStatus'))
  })

  it('names the closed parameter kinds and shows an unknown one as itself', () => {
    expect(parameterKind({ kind: 'DAY_BUCKETS' })).toBe('DAY_BUCKETS')
    expect(parameterKind({})).toBeNull()
    expect(kindText('ENUM_MAP')).toMatch(/key vocabulary/)
    expect(kindText('FORMULA')).toBe('Unknown kind FORMULA')
  })

  it('says how many runs use a version', () => {
    expect(usedByText(0)).toBe('Not used by any run')
    expect(usedByText(1)).toBe('Used by 1 run')
    expect(usedByText(12)).toBe('Used by 12 runs')
  })
})

describe('parameters are shown and compared as exact text (FE-16.1)', () => {
  it('keeps decimal text as the server sent it', () => {
    expect(leafText('0.10')).toBe('0.10')
    expect(leafText('1E+2')).toBe('1E+2')
    expect(leafText(null)).toBe('null')
    expect(diffParameters({ value: '0.10' }, { value: '0.1' })).toEqual([{ path: 'value', change: 'changed', from: '0.10', to: '0.1' }])
  })

  it('lays out DAY_BUCKETS with the last bucket open', () => {
    expect(bucketRows(SEEDED)?.map((b) => b.interval)).toEqual(['(0, 1]', '(1, 7]', '(7, 30]', '(30, ∞)'])
    expect(bucketRows({ map: {} })).toBeNull()
  })

  it('keys buckets by code, so a moved boundary is one change and a new bucket is an addition', () => {
    const next = { buckets: [bucket('B01', 0, 1), bucket('B02', 1, 14), bucket('B03', 14, 30), bucket('B04', 30, 60), bucket('B05', 60, null)] }
    expect(flattenParameters(SEEDED).get('buckets[B02].upper_inclusive')).toBe('7')
    const changes = diffParameters(SEEDED, next)
    expect(changes).toContainEqual({ path: 'buckets[B02].upper_inclusive', change: 'changed', from: '7', to: '14' })
    expect(changes).toContainEqual({ path: 'buckets[B04].upper_inclusive', change: 'changed', from: 'null', to: '60' })
    expect(changes).toContainEqual({ path: 'buckets[B05].code', change: 'added', from: null, to: 'B05' })
    expect(changes.some((c) => c.path.startsWith('buckets[B01]'))).toBe(false)
    expect(diffParameters({ map: { BOND: 'INFLOW', LOAN: 'INFLOW' } }, { map: { BOND: 'INFLOW' } })).toEqual([
      { path: 'map.LOAN', change: 'removed', from: 'INFLOW', to: null },
    ])
    expect(diffParameters(SEEDED, SEEDED)).toEqual([])
  })

  it('opens on the ACTIVE version and compares it with the one before', () => {
    const versions = [version(1, { status: 'SUPERSEDED' }), version(2), version(3, { status: 'IN_REVIEW' })]
    expect(defaultVersion(versions)?.version).toBe(2)
    expect(defaultDiffPair(versions)).toEqual({ from: 1, to: 2 })
    expect(defaultDiffPair([version(1), version(2, { status: 'DRAFT' })])).toEqual({ from: 1, to: 2 })
    expect(defaultDiffPair([version(1)])).toBeNull()
    expect(defaultVersion([])).toBeNull()
  })

  it('renders each parameter shape with the server text, and a masked leaf as "masked"', () => {
    const buckets = renderToStaticMarkup(createElement(ParameterValues, { parameters: SEEDED }))
    expect(buckets).toContain('data-testid="parameter-buckets"')
    expect(buckets).toContain('(30, ∞)')
    const map = renderToStaticMarkup(createElement(ParameterValues, { parameters: { map: { LOAN: '0.50', BOND: { masked: true, classification: 'confidential' } } } }))
    expect(map.indexOf('BOND')).toBeLessThan(map.indexOf('LOAN'))
    expect(map).toContain('0.50')
    expect(map).toContain('data-testid="masked-value"')
    expect(renderToStaticMarkup(createElement(ParameterValues, { parameters: { value: 'EXCLUDE_WITH_WARNING' } }))).toContain('EXCLUDE_WITH_WARNING')
  })
})

describe('modules: AVAILABLE and PLANNED are shown apart, PLANNED never as ready (FE-16.1)', () => {
  it('groups module versions by implementation status', () => {
    const groups = groupModules([PLANNED, AVAILABLE])
    expect(groups.AVAILABLE.map((m) => m.code)).toEqual(['maturity_ladder'])
    expect(groups.PLANNED.map((m) => m.code)).toEqual(['irrbb_eve'])
  })

  it('renders a PLANNED module as a planned extension point (snapshot)', () => {
    const html = renderToStaticMarkup(createElement(ModuleCard, { module: PLANNED }))
    expect(html).toMatchSnapshot()
    expect(html).toContain('data-status="PLANNED"')
    expect(html).toContain('not production-ready')
    // FE-30.1 (D-56): a PLANNED version carries its validation status too, ILLUSTRATIVE like every current one.
    expect(html).toContain('data-testid="module-validation-status" data-validation-status="ILLUSTRATIVE"')
    expect(html).toContain('Illustrative — not validated')
    expect(html).not.toMatch(/Runnable now|Available|module-runnable|Engine/)
  })

  it('a PLANNED row carries the shared PlannedModuleBadge and its declared contracts (FE-18.1)', () => {
    const html = renderToStaticMarkup(createElement(ModuleCard, { module: PLANNED }))
    expect(html).toContain('data-testid="planned-module-badge"')
    expect(html).toContain('Planned — extension point')
    expect(html).toContain('module_not_available')
    expect(html).not.toContain('data-testid="module-implementation"')
    expect(html).toContain('data-testid="module-contracts"')
    expect(html).toContain('instrument (required): instrument_id')
  })

  it('renders an AVAILABLE module as an illustrative prototype, with whether it can run now', () => {
    const html = renderToStaticMarkup(createElement(ModuleCard, { module: AVAILABLE }))
    expect(html).toContain('Available · illustrative prototype')
    expect(html).toContain('data-testid="illustrative-badge"')
    expect(html).toContain('Runnable now')
    // The server's statement shows once per page, above the groups, not on every card.
    expect(html).not.toContain('no parity with any vendor system is claimed')
    expect(html).toContain('Engine version')
    // Unchanged by FE-18.1: the implementation pill, no planned badge, no contracts block.
    expect(html).toContain('data-testid="module-implementation"')
    expect(html).not.toMatch(/planned-module-badge|module-contracts/)
  })
})

describe('the /domain tabs (FE-16.1)', () => {
  const sections: WorkspaceSection[] = [
    { slug: 'instruments', href: '/domain', label: 'Instruments', permission: 'domain:read' },
    { slug: 'rules', href: '/domain/rules', label: 'Rules', permission: 'rule:read' },
    { slug: 'assumption-sets', href: '/domain/assumption-sets', label: 'Assumption sets', permission: 'rule:read' },
  ]
  it('marks the section a path belongs to', () => {
    expect(activeSection('/domain', sections)).toBe('instruments')
    expect(activeSection('/domain/instruments/abc', sections)).toBe('instruments')
    expect(activeSection('/domain/rules', sections)).toBe('rules')
    expect(activeSection('/domain/rules/abc/propose', sections)).toBe('rules')
    expect(activeSection('/domain/assumption-sets/abc', sections)).toBe('assumption-sets')
    expect(activeSection('/domain/rulesets', sections)).toBe('instruments')
  })
})

describe('registry reversibility (FE-16.1, D-02)', () => {
  // FE-31.1: these assert the Phase 30 tab strip and domain entry, which NEXT_PUBLIC_DOMAIN_LINKS_UI=off restores exactly.
  beforeEach(() => vi.stubEnv('NEXT_PUBLIC_DOMAIN_LINKS_UI', 'off'))
  afterEach(() => {
    vi.unstubAllEnvs()
    vi.resetModules()
    vi.doUnmock('next/navigation')
  })

  it('lists the registry tabs and names the registry while NEXT_PUBLIC_RULES_UI is on', async () => {
    // FE-19.1 adds a Counterparties tab behind NEXT_PUBLIC_CREDIT_DOMAIN_UI (on by default).
    // FE-20.1 adds a Ratings tab behind NEXT_PUBLIC_RATINGS_UI; off, this Phase 19 list exactly.
    vi.stubEnv('NEXT_PUBLIC_RATINGS_UI', 'off')
    vi.resetModules()
    const on = await import('@/config/navigation')
    expect(on.domainSections.map((s) => s.href)).toEqual([
      '/domain',
      '/domain/counterparties',
      '/domain/rules',
      '/domain/modules',
      '/domain/assumption-sets',
    ])
    expect(on.domainSections.find((s) => s.slug === 'counterparties')?.permission).toBe('domain:read')
    // Off: the Phase 18 list exactly.
    vi.stubEnv('NEXT_PUBLIC_CREDIT_DOMAIN_UI', 'off')
    vi.resetModules()
    const { domainSections, mainWorkspaces } = await import('@/config/navigation')
    expect(domainSections).toEqual([
      { slug: 'instruments', href: '/domain', label: 'Instruments', permission: 'domain:read' },
      { slug: 'rules', href: '/domain/rules', label: 'Rules', permission: 'rule:read' },
      { slug: 'modules', href: '/domain/modules', label: 'Modules', permission: 'rule:read' },
      { slug: 'assumption-sets', href: '/domain/assumption-sets', label: 'Assumption sets', permission: 'rule:read' },
    ])
    expect(domainSections.filter((s) => s.slug !== 'instruments').every((s) => s.permission === 'rule:read')).toBe(true)
    expect(mainWorkspaces.find((w) => w.slug === 'domain')?.description).toMatch(/registry/)
  })

  it('NEXT_PUBLIC_RULES_UI=off restores the Phase 15 domain entry exactly and drops the tabs', async () => {
    vi.stubEnv('NEXT_PUBLIC_RULES_UI', 'off')
    vi.stubEnv('NEXT_PUBLIC_CREDIT_DOMAIN_UI', 'off')
    vi.resetModules()
    const { domainSections, mainWorkspaces } = await import('@/config/navigation')
    expect(domainSections).toEqual([])
    expect(mainWorkspaces.find((w) => w.slug === 'domain')).toMatchObject({
      href: '/domain',
      label: 'Financial Domain',
      description: 'Instrument catalogue: projected versions, attributes, cash flows and their lineage to imports.',
      permission: 'domain:read',
    })
  })

  it('the registry routes are a 404 while off, and render while on', async () => {
    const notFound = vi.fn(() => {
      throw new Error('NEXT_NOT_FOUND')
    })
    vi.doMock('next/navigation', () => ({ notFound }))
    const child = createElement('p', null, 'rules')

    vi.resetModules()
    const on = await import('@/components/registry/RegistryGate')
    expect(renderToStaticMarkup(createElement(on.RegistryGate, null, child))).toBe('<p>rules</p>')
    expect(notFound).not.toHaveBeenCalled()

    vi.stubEnv('NEXT_PUBLIC_RULES_UI', 'off')
    vi.resetModules()
    const off = await import('@/components/registry/RegistryGate')
    expect(() => renderToStaticMarkup(createElement(off.RegistryGate, null, child))).toThrow('NEXT_NOT_FOUND')
    expect(notFound).toHaveBeenCalledTimes(1)
  })
})

/**
 * Rule parameters are decimal text from the API: the registry code is grepped for every way
 * JavaScript turns text into a float (as FE-14.1/15.1), for hand-written payload types, and for
 * writes (the registry only reads; FE-16.2 owns the writes).
 */
describe('registry code hygiene (FE-16.1)', () => {
  const root = join(__dirname, '..', '..', '..')
  const files = [
    'components/registry/registry.ts',
    'components/registry/AssumptionSets.tsx',
    'components/registry/ModuleRegistry.tsx',
    'components/registry/ParameterView.tsx',
    'components/registry/RegistryBadges.tsx',
    'components/registry/RegistryGate.tsx',
    'components/registry/RegistryTabs.tsx',
    'components/registry/RuleList.tsx',
    'components/registry/RuleView.tsx',
    'app/(platform)/domain/rules/page.tsx',
    'app/(platform)/domain/rules/[ruleId]/page.tsx',
    'app/(platform)/domain/modules/page.tsx',
    'app/(platform)/domain/assumption-sets/page.tsx',
    'app/(platform)/domain/assumption-sets/[assumptionSetId]/page.tsx',
  ]
  const PARSING = [/parseFloat/, /parseInt/, /\bNumber\s*\(/, /\bNumber\./, /\bMath\./, /\.toFixed\s*\(/, /NumberFormat/, /\bBigInt\s*\(/, /(^|[=(,:?!&|{]|return)\s*\+\s*[A-Za-z_$(]/m]
  const HAND_WRITTEN =
    /\b(rule_id|rule_version_id|assumption_set_id|module_version_id|parameters_sha256|content_sha256|run_count|run_id|reporting_date|version_count|active_version)\??\s*:\s*(string|number|boolean)\b/

  it.each(files)('%s never parses a value to a number', (file) => {
    const source = readFileSync(join(root, file), 'utf8')
    for (const pattern of PARSING) expect(source, `${file} matches ${pattern}`).not.toMatch(pattern)
  })

  it.each([...files, '../e2e/registry.spec.ts'])('%s declares no hand-written payload type', (file) => {
    expect(readFileSync(join(root, file), 'utf8')).not.toMatch(HAND_WRITTEN)
  })

  it('the registry views only read', () => {
    for (const file of files) {
      const calls = new Set(readFileSync(join(root, file), 'utf8').match(/api\.[A-Z]+\(/g) ?? [])
      for (const c of calls) expect(c, file).toBe('api.GET(')
    }
  })

  it('the rule page mounts the FE-16.2 actions with its in-place refresh', () => {
    expect(readFileSync(join(root, 'components/registry/RuleView.tsx'), 'utf8')).toMatch(/<RuleVersionActions ruleId=\{detail\.rule_id\} onChanged=\{refresh\} \/>/)
  })

  it('the greps would catch a violation', () => {
    expect(HAND_WRITTEN.test('type R = { rule_version_id: string }')).toBe(true)
    expect(PARSING.some((p) => p.test('const x = parseFloat(v.parameters.value)'))).toBe(true)
    expect(PARSING.some((p) => p.test('const x = +v.parameters.value'))).toBe(true)
  })
})
