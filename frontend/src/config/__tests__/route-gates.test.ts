import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative, sep } from 'node:path'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Schemas } from '@/api/client'

/*
 * FE-35.5 Done-when: every (platform) route is gated by its nav permission, /advanced/* included.
 * The test enumerates the route files (every page.tsx under app/(platform)), resolves each one's
 * nav entry the way the (platform) layout's NavPermissionGate does, and renders the gate for it
 * with and without that permission. A route added later fails here until it is listed below.
 */

type Permission = Schemas['Permission']

const state = vi.hoisted(() => ({ pathname: '/', granted: [] as string[] }))
vi.mock('next/navigation', () => ({ usePathname: () => state.pathname, useRouter: () => ({ push: vi.fn(), replace: vi.fn() }) }))
vi.mock('@/components/auth/SessionProvider', () => ({ useCurrentSession: () => ({ has: (p: string) => state.granted.includes(p) }) }))

/** frontend/src/app/(platform) */
const PLATFORM = join(__dirname, '..', '..', 'app', '(platform)')

/** The route of every page.tsx under app/(platform): "/domain/rules/[ruleId]". */
function routeFiles(dir: string = PLATFORM): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) return routeFiles(path)
    if (name !== 'page.tsx') return []
    const route = relative(PLATFORM, dir).split(sep).filter((s) => s && !/^\(.*\)$/.test(s))
    return [`/${route.join('/')}`.replace(/\/$/, '') || '/']
  })
}

/** Every route and the nav permission that gates it; null: the nav entry is open to every signed-in user. */
const EXPECTED: Record<string, Permission | null> = {
  '/dashboard': null,
  '/schemas': 'schema:read',
  '/schemas/[schemaId]': 'schema:read',
  '/schemas/proposals': 'schema:read',
  '/schemas/proposals/[proposalId]': 'schema:read',
  '/schemas/proposals/new': 'schema:read',
  '/ingestion': 'source:read',
  '/ingestion/imports/[importId]': 'source:read',
  '/ingestion/imports/new': 'source:read',
  '/ingestion/mappings/[mappingId]': 'source:read',
  '/ingestion/mappings/new': 'source:read',
  '/ingestion/sources/[sourceId]': 'source:read',
  '/ingestion/sources/new': 'source:read',
  '/domain': 'domain:read',
  '/domain/instruments/[instrumentId]': 'domain:read',
  '/domain/counterparties': 'domain:read',
  '/domain/counterparties/[counterpartyId]': 'domain:read',
  '/domain/counterparties/exposures/[exposureId]': 'domain:read',
  '/domain/ratings': 'domain:read',
  '/domain/legal-entities': 'domain:read',
  '/domain/legal-entities/[legalEntityId]': 'domain:read',
  // The registry tabs carry their own nav permission, which wins over the workspace's.
  '/domain/rules': 'rule:read',
  '/domain/rules/[ruleId]': 'rule:read',
  '/domain/rules/[ruleId]/propose': 'rule:read',
  '/domain/modules': 'rule:read',
  '/domain/assumption-sets': 'rule:read',
  '/domain/assumption-sets/[assumptionSetId]': 'rule:read',
  '/calculations': 'calc:read',
  '/calculations/new': 'calc:read',
  '/calculations/runs/[runId]': 'calc:read',
  '/advanced/stress-explorer': 'calc:read',
  '/advanced/stress-explorer/new': 'calc:read',
  '/advanced/stress-explorer/[scenarioId]': 'calc:read',
  '/advanced/stress-explorer/[scenarioId]/versions/new': 'calc:read',
  '/advanced/stress-explorer/[scenarioId]/versions/[version]/edit': 'calc:read',
  '/advanced/stress-explorer/[scenarioId]/runs': 'calc:read',
  '/advanced/stress-explorer/[scenarioId]/runs/[scenarioRunId]': 'calc:read',
  '/advanced/liquidity-intelligence': 'calc:read',
  '/audit': 'audit:read',
  // Access & Roles shows everyone their own identity; /access/users gates admin:read on its page.
  '/access': null,
  '/access/users': null,
}
/** Routes that only redirect (no content of their own), and where to. The target is gated. */
const REDIRECTS: Record<string, string> = { '/advanced': '/advanced/stress-explorer' }
/** The dynamic planned-workspace route: its pages are the planned workspaces (open, "planned"). */
const PLANNED_ROUTE = '/[workspace]'

const sample = (route: string) => route.replace(/\[version\]/g, '2').replace(/\[[^\]]+\]/g, '6f1c0f7e-0000-4000-8000-000000000002')
const PERMISSIONS = (): Permission[] => {
  const openapi = JSON.parse(readFileSync(join(__dirname, '..', '..', '..', '..', 'backend', 'openapi.json'), 'utf8'))
  return openapi.components.schemas.Permission.enum
}

async function gate() {
  const { NavPermissionGate } = await import('@/components/auth/NavPermissionGate')
  return (pathname: string, granted: string[]) => {
    state.pathname = pathname
    state.granted = granted
    return renderToStaticMarkup(createElement(NavPermissionGate, null, createElement('p', { 'data-testid': 'page' }, 'page')))
  }
}

afterEach(() => {
  vi.unstubAllEnvs()
  vi.resetModules()
})

describe('every (platform) route is gated by its nav permission (FE-35.5 Done-when)', () => {
  const routes = routeFiles().sort()

  it('enumerates the route files, and every one is classified here', () => {
    expect(routes.length).toBeGreaterThan(40)
    expect(routes).toEqual([...Object.keys(EXPECTED), ...Object.keys(REDIRECTS), PLANNED_ROUTE].sort())
  })

  it('the (platform) layout wraps every route in the gate', () => {
    expect(readFileSync(join(PLATFORM, 'layout.tsx'), 'utf8')).toContain('<NavPermissionGate>{children}</NavPermissionGate>')
  })

  it.each(Object.entries(EXPECTED))('%s resolves to its nav entry (%s)', async (route, permission) => {
    const { navEntryFor } = await import('@/config/navigation')
    const entry = navEntryFor(sample(route))
    expect(entry, route).toBeDefined()
    expect(entry?.permission ?? null).toBe(permission)
  })

  it.each(Object.entries(EXPECTED).filter(([, p]) => p !== null) as [string, Permission][])(
    '%s shows the 403 state without %s, and the page with it',
    async (route, permission) => {
      const render = await gate()
      const others = PERMISSIONS().filter((p) => p !== permission)
      const denied = render(sample(route), others)
      expect(denied).toContain('data-testid="route-gate"')
      expect(denied).toContain(`data-permission="${permission}"`)
      expect(denied).toContain("You don&#x27;t have access to this")
      expect(denied).not.toContain('data-testid="page"')
      expect(render(sample(route), [permission])).toBe('<p data-testid="page">page</p>')
    },
  )

  it.each(Object.entries(EXPECTED).filter(([, p]) => p === null))('%s is open to every signed-in user (no nav permission)', async (route) => {
    const render = await gate()
    expect(render(sample(route), [])).toBe('<p data-testid="page">page</p>')
  })

  it('the planned workspaces behind /[workspace] are nav entries, open and "planned"', async () => {
    const { adminWorkspaces, mainWorkspaces, navEntryFor } = await import('@/config/navigation')
    const slugs = [...mainWorkspaces, ...adminWorkspaces].filter((w) => w.planned).map((w) => w.slug)
    expect(slugs.length).toBeGreaterThan(0)
    // The page builds exactly these (generateStaticParams, dynamicParams = false: any other slug is a 404).
    expect(readFileSync(join(PLATFORM, '[workspace]', 'page.tsx'), 'utf8')).toMatch(/\.filter\(\(w\) => w\.planned\)[\s\S]*dynamicParams = false/)
    const render = await gate()
    for (const slug of slugs) {
      expect(navEntryFor(`/${slug}`)?.href).toBe(`/${slug}`)
      expect(navEntryFor(`/${slug}`)?.permission).toBeUndefined()
      expect(render(`/${slug}`, [])).toBe('<p data-testid="page">page</p>')
    }
  })

  it.each(Object.entries(REDIRECTS))('%s only redirects, to the gated %s', async (route, target) => {
    const page = readFileSync(join(PLATFORM, ...route.split('/').filter(Boolean), 'page.tsx'), 'utf8')
    expect(page).toContain(`redirect('${target}')`)
    expect(page).not.toMatch(/return\s*\(|<[A-Z]/)
    const { navEntryFor } = await import('@/config/navigation')
    expect(navEntryFor(route)).toBeUndefined()
    expect(navEntryFor(target)?.permission).toBe('calc:read')
  })

  it('a tab wins over its workspace only on a segment boundary', async () => {
    const { navEntryFor } = await import('@/config/navigation')
    expect(navEntryFor('/domain/rules')?.permission).toBe('rule:read')
    expect(navEntryFor('/domain/rulesets')?.permission).toBe('domain:read')
    expect(navEntryFor('/auditing')).toBeUndefined()
  })
})

describe('switches (D-02)', () => {
  it('NEXT_PUBLIC_ROUTE_GATES_UI=off renders every route as before, gate or no gate', async () => {
    vi.stubEnv('NEXT_PUBLIC_ROUTE_GATES_UI', 'off')
    const render = await gate()
    for (const route of Object.keys(EXPECTED)) expect(render(sample(route), []), route).toBe('<p data-testid="page">page</p>')
  })

  it('with NEXT_PUBLIC_STRESS_EXPLORER_UI=off /advanced/* has no nav entry, so its own 404 (AdvancedGate) stands', async () => {
    vi.stubEnv('NEXT_PUBLIC_STRESS_EXPLORER_UI', 'off')
    const { navEntryFor } = await import('@/config/navigation')
    expect(navEntryFor('/advanced/stress-explorer')).toBeUndefined()
    expect(navEntryFor('/advanced/liquidity-intelligence')).toBeUndefined()
    const render = await gate()
    expect(render('/advanced/stress-explorer', [])).toBe('<p data-testid="page">page</p>')
  })

  it('with NEXT_PUBLIC_CALCULATIONS_UI=off /calculations is the open "planned" entry, as in Phase 14', async () => {
    vi.stubEnv('NEXT_PUBLIC_CALCULATIONS_UI', 'off')
    const { navEntryFor } = await import('@/config/navigation')
    expect(navEntryFor('/calculations/runs/x')?.permission).toBeUndefined()
  })
})
