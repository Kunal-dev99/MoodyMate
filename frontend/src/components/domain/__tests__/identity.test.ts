import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, api } from '@/api/client'
import { LinkedIdentity, MaturityValue } from '@/components/domain/IdentityParts'
import {
  LINK_UNRESOLVED_TEXT,
  MATURITY_MISSING,
  MATURITY_NOT_STATED,
  SOURCE_KIND_LABELS,
  TARGET_KIND_LABELS,
  fetchSourceLinks,
  linkCountRows,
  linkStateOf,
  maturityState,
  targetHref,
  type IdentityLink,
  type IdentityLinkPage,
} from '@/components/domain/identity'

/*
 * FE-31.1 (D-57, D-58): the instrument detail's typed maturity or "Not stated (schema v1 free
 * text)"; the cash-flow, exposure (and position, via the legal entity) details' linked identity
 * or "Unresolved (text key)"; the Legal entities tab, and NEXT_PUBLIC_DOMAIN_LINKS_UI=off
 * restoring the Phase 30 nav exactly.
 */

const openapi = JSON.parse(readFileSync(fileURLToPath(new URL('../../../../../backend/openapi.json', import.meta.url)), 'utf8'))
const enumOf = (name: string): string[] => [...openapi.components.schemas[name].enum].sort()
const text = (html: string) => html.replace(/<[^>]+>|<!-- -->/g, '')
const masked = { masked: true as const, classification: 'internal' }

const link = (over: Partial<IdentityLink> = {}): IdentityLink => ({
  link_id: '00000000-0000-4000-8000-000000000001',
  source_kind: 'CASHFLOW_EVENT',
  source_row_id: 'row-1',
  target_kind: 'INSTRUMENT',
  target_id: '11111111-2222-4333-8444-555555555555',
  method: 'BUSINESS_KEY_UNIQUE',
  resolver_version: 1,
  created_by: 'system',
  created_at: '2026-10-24T08:00:00Z',
  ...over,
})

describe('typed maturity on the instrument detail (FE-31.1, D-57)', () => {
  it('a typed contract shows the server ISO text as sent, labelled typed', () => {
    expect(maturityState({ maturity_typed: true, maturity_date: '2030-06-30' })).toEqual({ kind: 'typed', date: '2030-06-30' })
    const html = renderToStaticMarkup(createElement(MaturityValue, { version: { maturity_typed: true, maturity_date: '2030-06-30' }, testId: 'm' }))
    expect(html).toContain('data-state="typed"')
    expect(text(html)).toContain('2030-06-30')
    expect(text(html)).toContain('Typed date')
    expect(text(html)).not.toContain(MATURITY_NOT_STATED)
  })

  it('instrument v1 (free text, TYPE_NOT_PROJECTED) shows "Not stated (schema v1 free text)"', () => {
    expect(MATURITY_NOT_STATED).toBe('Not stated (schema v1 free text)')
    expect(maturityState({ maturity_typed: false, maturity_date: null })).toEqual({ kind: 'v1-free-text' })
    const html = renderToStaticMarkup(createElement(MaturityValue, { version: { maturity_typed: false, maturity_date: null }, testId: 'm' }))
    expect(html).toContain('data-state="v1-free-text"')
    expect(text(html)).toBe(MATURITY_NOT_STATED)
  })

  it('a typed contract with no maturity (NULL, counted) and a masked one are their own states, never a date', () => {
    expect(maturityState({ maturity_typed: true, maturity_date: null })).toEqual({ kind: 'typed-missing' })
    const missing = renderToStaticMarkup(createElement(MaturityValue, { version: { maturity_typed: true, maturity_date: null } }))
    expect(text(missing)).toBe(MATURITY_MISSING)
    expect(maturityState({ maturity_typed: true, maturity_date: masked })).toEqual({ kind: 'masked', value: masked })
    expect(maturityState({ maturity_typed: false, maturity_date: masked }).kind).toBe('masked')
    const hidden = renderToStaticMarkup(createElement(MaturityValue, { version: { maturity_typed: true, maturity_date: masked } }))
    expect(hidden).toContain('data-masked="true"')
    expect(text(hidden)).toContain('masked')
  })
})

describe('linked identity on the cash-flow, exposure and position details (FE-31.1, D-58)', () => {
  it('names every source and target kind of the API', () => {
    expect(Object.keys(SOURCE_KIND_LABELS).sort()).toEqual(enumOf('IdentitySourceKind'))
    expect(Object.keys(TARGET_KIND_LABELS).sort()).toEqual(enumOf('IdentityTargetKind'))
  })

  it('a linked row shows the target kind and id, linking to its own page', () => {
    const html = renderToStaticMarkup(createElement(LinkedIdentity, { state: { kind: 'ready', link: link() } }))
    expect(html).toContain('data-state="linked"')
    expect(html).toContain('data-target-kind="INSTRUMENT"')
    expect(html).toContain('href="/domain/instruments/11111111-2222-4333-8444-555555555555"')
    expect(text(html)).toBe('Instrument 11111111')
    expect(targetHref('COUNTERPARTY', 'cp-1', true)).toBe('/domain/counterparties/cp-1')
    expect(targetHref('COUNTERPARTY', 'cp-1', false)).toBeNull()
    expect(targetHref('LEGAL_ENTITY', 'le-1')).toBe('/domain/legal-entities/le-1')
  })

  it('a row without a link shows "Unresolved (text key)"', () => {
    expect(LINK_UNRESOLVED_TEXT).toBe('Unresolved (text key)')
    const html = renderToStaticMarkup(createElement(LinkedIdentity, { state: { kind: 'ready', link: null } }))
    expect(html).toContain('data-state="unresolved"')
    expect(text(html)).toBe(LINK_UNRESOLVED_TEXT)
    // A row the map does not hold has no link: unresolved, as the server answered.
    expect(linkStateOf({ kind: 'ready', value: new Map([['row-1', link()]]) }, 'row-2')).toEqual({ kind: 'ready', link: null })
  })

  it('a refused (403) or failed read is shown as such, never as unresolved (D-04)', () => {
    const forbidden = renderToStaticMarkup(createElement(LinkedIdentity, { state: { kind: 'forbidden', status: 403, cid: 'cid-31' } }))
    expect(forbidden).toContain('data-state="forbidden"')
    expect(text(forbidden)).not.toContain(LINK_UNRESOLVED_TEXT)
    const failed = renderToStaticMarkup(createElement(LinkedIdentity, { state: { kind: 'failed', status: 500, cid: null } }))
    expect(failed).toContain('data-state="failed"')
    expect(linkStateOf({ kind: 'forbidden', status: 403, cid: null }, 'row-1').kind).toBe('forbidden')
  })

  it('a legal entity lists a count for every source kind, zero kept', () => {
    expect(linkCountRows({ POSITION_VERSION: 3 })).toEqual([
      ['POSITION_VERSION', 3],
      ['CASHFLOW_EVENT', 0],
      ['CREDIT_EXPOSURE', 0],
    ])
  })
})

describe('reading links (GET /identity-links only)', () => {
  afterEach(() => vi.restoreAllMocks())
  const page = (items: IdentityLink[], total = items.length): IdentityLinkPage => ({ items, total, limit: 200, offset: 0 })
  const ok = (body: unknown) => ({ data: body, error: undefined, response: new Response(null, { status: 200 }) }) as never

  it('one read of the links to the identity, then a read per row it does not cover; no link is null', async () => {
    const get = vi.spyOn(api, 'GET').mockImplementation((async (_path: string, init: { params: { query: Record<string, unknown> } }) => {
      const q = init.params.query
      if (q.target_id) return ok(page([link({ source_row_id: 'row-1' })]))
      if (q.source_id === 'row-2') return ok(page([link({ source_row_id: 'row-2', target_id: 'other-instrument' })]))
      return ok(page([]))
    }) as never)
    const links = await fetchSourceLinks('CASHFLOW_EVENT', 'INSTRUMENT', ['row-1', 'row-2', 'row-3'], undefined, 'this-instrument')
    expect([...links.keys()]).toEqual(['row-1', 'row-2', 'row-3'])
    expect(links.get('row-1')?.source_row_id).toBe('row-1')
    expect(links.get('row-2')?.target_id).toBe('other-instrument')
    expect(links.get('row-3')).toBeNull()
    expect(get).toHaveBeenCalledTimes(3)
    for (const call of get.mock.calls) expect(call[0]).toBe('/api/v1/identity-links')
    expect(get.mock.calls[0][1]).toMatchObject({ params: { query: { source_kind: 'CASHFLOW_EVENT', target_kind: 'INSTRUMENT', target_id: 'this-instrument' } } })
  })

  it('pages through the links to an identity until the total', async () => {
    const all = Array.from({ length: 250 }, (_, i) => link({ link_id: `l-${i}`, source_row_id: `row-${i}` }))
    vi.spyOn(api, 'GET').mockImplementation((async (_path: string, init: { params: { query: { offset: number; limit: number } } }) => {
      const { offset, limit } = init.params.query
      return ok({ items: all.slice(offset, offset + limit), total: all.length, limit, offset })
    }) as never)
    const links = await fetchSourceLinks('CASHFLOW_EVENT', 'INSTRUMENT', ['row-0', 'row-249'], undefined, 'x')
    expect(links.get('row-249')?.link_id).toBe('l-249')
  })

  it('a 403 rejects with the ApiError (the rows show "not visible", not "unresolved")', async () => {
    vi.spyOn(api, 'GET').mockResolvedValue({
      data: undefined,
      error: { code: 'forbidden', message: 'no', details: null, correlation_id: 'cid', retryable: false },
      response: new Response(null, { status: 403 }),
    } as never)
    await expect(fetchSourceLinks('CREDIT_EXPOSURE', 'COUNTERPARTY', ['v-1'])).rejects.toBeInstanceOf(ApiError)
  })

  it('no rows, no request', async () => {
    const get = vi.spyOn(api, 'GET')
    expect((await fetchSourceLinks('CREDIT_EXPOSURE', 'COUNTERPARTY', [])).size).toBe(0)
    expect(get).not.toHaveBeenCalled()
  })
})

/** The Phase 30 Financial Domain entry and tab strip (every earlier switch on), as NEXT_PUBLIC_DOMAIN_LINKS_UI=off restores them. */
const PHASE_30_DESCRIPTION =
  'Instrument catalogue with its lineage to imports, and the registry: methodology rules and their versions, calculation modules and assumption sets. Counterparties and credit exposures with their versions and lineage. Obligor ratings with maker-checker approval.'
const PHASE_30_TABS = ['/domain', '/domain/counterparties', '/domain/ratings', '/domain/rules', '/domain/modules', '/domain/assumption-sets']

describe('legal entities and reversibility (FE-31.1, D-02)', () => {
  afterEach(() => {
    vi.unstubAllEnvs()
    vi.resetModules()
    vi.doUnmock('next/navigation')
  })

  it('on: a Legal entities tab (domain:read) under Financial Domain, after the credit tabs, and the entry names it', async () => {
    vi.resetModules()
    const { domainSections, mainWorkspaces, findWorkspace } = await import('@/config/navigation')
    expect(domainSections.map((s) => s.href)).toEqual([...PHASE_30_TABS.slice(0, 3), '/domain/legal-entities', ...PHASE_30_TABS.slice(3)])
    expect(domainSections.find((s) => s.slug === 'legal-entities')).toEqual({
      slug: 'legal-entities',
      href: '/domain/legal-entities',
      label: 'Legal entities',
      permission: 'domain:read',
    })
    expect(mainWorkspaces.find((w) => w.slug === 'domain')?.description).toBe(
      `${PHASE_30_DESCRIPTION} Legal entities and the identity links of positions, cash flows and exposures.`,
    )
    expect(findWorkspace('domain')?.planned).toBeUndefined()
    const { activeSection } = await import('@/components/registry/RegistryTabs')
    expect(activeSection('/domain/legal-entities', domainSections)).toBe('legal-entities')
    expect(activeSection('/domain/legal-entities/abc', domainSections)).toBe('legal-entities')
  })

  it('NEXT_PUBLIC_DOMAIN_LINKS_UI=off: the Phase 30 nav returns exactly', async () => {
    vi.stubEnv('NEXT_PUBLIC_DOMAIN_LINKS_UI', 'off')
    vi.resetModules()
    const { domainSections, mainWorkspaces, advancedWorkspaces, adminWorkspaces } = await import('@/config/navigation')
    expect(domainSections.map((s) => s.href)).toEqual(PHASE_30_TABS)
    expect(mainWorkspaces.find((w) => w.slug === 'domain')?.description).toBe(PHASE_30_DESCRIPTION)
    expect(JSON.stringify([...mainWorkspaces, ...advancedWorkspaces, ...adminWorkspaces])).not.toContain('legal')
    expect((await import('@/config/features')).DOMAIN_LINKS_UI_ENABLED).toBe(false)
  })

  it('with every other domain tab off, Legal entities alone still gives a tab strip; the domain planned, none', async () => {
    vi.stubEnv('NEXT_PUBLIC_RULES_UI', 'off')
    vi.stubEnv('NEXT_PUBLIC_CREDIT_DOMAIN_UI', 'off')
    vi.resetModules()
    expect((await import('@/config/navigation')).domainSections.map((s) => s.slug)).toEqual(['instruments', 'legal-entities'])
    vi.stubEnv('NEXT_PUBLIC_DOMAIN_LINKS_UI', 'off')
    vi.resetModules()
    expect((await import('@/config/navigation')).domainSections).toEqual([])
    vi.unstubAllEnvs()
    vi.stubEnv('NEXT_PUBLIC_DOMAIN_UI', 'off')
    vi.resetModules()
    expect((await import('@/config/features')).DOMAIN_LINKS_UI_ENABLED).toBe(false)
    expect((await import('@/config/navigation')).domainSections).toEqual([])
  })

  it('the /domain/legal-entities routes are a 404 while off, and render while on', async () => {
    const notFound = vi.fn(() => {
      throw new Error('NEXT_NOT_FOUND')
    })
    vi.doMock('next/navigation', () => ({ notFound }))
    const child = createElement('p', null, 'legal entities')
    vi.resetModules()
    const on = await import('@/components/domain/LinksGate')
    expect(renderToStaticMarkup(createElement(on.LinksGate, null, child))).toBe('<p>legal entities</p>')
    expect(notFound).not.toHaveBeenCalled()
    vi.stubEnv('NEXT_PUBLIC_DOMAIN_LINKS_UI', 'off')
    vi.resetModules()
    const off = await import('@/components/domain/LinksGate')
    expect(() => renderToStaticMarkup(createElement(off.LinksGate, null, child))).toThrow('NEXT_NOT_FOUND')
  })
})

/** As FE-14.1: no number parsing of server text, no hand-written payload type, and no writes. */
describe('identity code hygiene (FE-31.1)', () => {
  const root = join(__dirname, '..', '..', '..')
  const files = [
    'components/domain/identity.ts',
    'components/domain/IdentityParts.tsx',
    'components/domain/LinksGate.tsx',
    'components/domain/LegalEntityCatalogue.tsx',
    'components/domain/LegalEntityView.tsx',
    'app/(platform)/domain/legal-entities/layout.tsx',
    'app/(platform)/domain/legal-entities/page.tsx',
    'app/(platform)/domain/legal-entities/[legalEntityId]/page.tsx',
  ]
  it.each(files)('%s', (file) => {
    const src = readFileSync(join(root, file), 'utf8')
    expect(src).not.toMatch(/parseFloat|parseInt|Number\(|toFixed|new Date\(/)
    expect(src).not.toMatch(/api\.(POST|PUT|PATCH|DELETE)/)
    // Payload shapes come from the generated schema only: no hand-written Out/Page/Detail/Link/Entity type.
    expect(src).not.toMatch(/^(export )?(interface|type) \w*(Out|Page|Detail|Link|Entity)\b\s*=?\s*\{/m)
  })
})
