import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'

/*
 * FE-24.1: the sidebar's "Advanced" section (the Agentic Stress-Test Explorer), right after the
 * main nav (so directly below "AI Assistant") and before "Administration", behind
 * NEXT_PUBLIC_STRESS_EXPLORER_UI. Off, the Phase 23 sidebar exactly and /advanced is a 404 (D-02).
 */

/** frontend/src */
const root = join(__dirname, '..', '..', '..')
const text = (html: string) => html.replace(/<style[^>]*>.*?<\/style>/g, '').replace(/<!-- -->/g, '').replace(/<[^>]+>/g, '\n').replace(/&amp;/g, '&')
/** The sidebar's visible texts (brand, headings and nav labels, not the user footer), in render order. */
const sidebarTexts = (html: string) =>
  text(html.slice(html.indexOf('<aside'), html.indexOf('<div class="border-t border-border p-2')))
    .split('\n')
    .map((t) => t.trim())
    .filter(Boolean)

/** The Phase 23 sidebar (master a290978): main workspaces, then Administration. */
const PHASE_23_MAIN = [
  ['/dashboard', 'Overview'],
  ['/schemas', 'Source & Schemas'],
  ['/ingestion', 'Ingestion Workbench'],
  ['/domain', 'Financial Domain'],
  ['/calculations', 'Calculations'],
  ['/scenarios', 'Reconciliation & Scenarios'],
  ['/reports', 'Reports'],
  ['/assistant', 'AI Assistant'],
]
const PHASE_23_ADMIN = [
  ['/audit', 'Audit'],
  ['/access', 'Access & Roles'],
]

const session = vi.hoisted(() => ({ granted: null as null | string[] }))

async function renderShell() {
  vi.doMock('next/navigation', () => ({ usePathname: () => '/dashboard', notFound: vi.fn() }))
  vi.doMock('@/components/auth/SessionProvider', () => ({
    useCurrentSession: () => ({
      me: { display_name: 'Risk Analyst', email: 'analyst@example.test' },
      has: (p: string) => session.granted === null || session.granted.includes(p),
      signOut: async () => undefined,
      authConfig: { environment: 'local' },
    }),
  }))
  vi.doMock('@/components/layout/EnvironmentBanner', () => ({ EnvironmentBanner: () => null }))
  vi.doMock('@/components/layout/header', () => ({ Header: () => null }))
  vi.doMock('@/components/layout/ThemeToggle', () => ({ ThemeToggle: () => null }))
  vi.resetModules()
  const { AppShell } = await import('@/components/layout/AppShell')
  return renderToStaticMarkup(createElement(AppShell, null, createElement('p', null, 'page')))
}

afterEach(() => {
  session.granted = null
  vi.unstubAllEnvs()
  for (const m of ['next/navigation', '@/components/auth/SessionProvider', '@/components/layout/EnvironmentBanner', '@/components/layout/header', '@/components/layout/ThemeToggle'])
    vi.doUnmock(m)
  vi.resetModules()
})

describe('the Advanced sidebar section (FE-24.1)', () => {
  it('orders Workspace items → "AI Assistant" → "Advanced" → "Agentic Stress-Test Explorer" → "Administration"', async () => {
    const texts = sidebarTexts(await renderShell())
    expect(texts).toEqual([
      'MoodyMate',
      'Financial Risk Platform',
      'Workspace',
      ...PHASE_23_MAIN.map(([, label]) => label),
      'Advanced',
      'Agentic Stress-Test Explorer',
      'Administration',
      ...PHASE_23_ADMIN.map(([, label]) => label),
    ])
  })

  it('links the item to /advanced/stress-explorer under calc:read, with the FlaskConical icon', async () => {
    const html = await renderShell()
    expect(html).toContain('href="/advanced/stress-explorer"')
    const { advancedWorkspaces, findWorkspace } = await import('@/config/navigation')
    const { FlaskConical } = await import('lucide-react')
    expect(advancedWorkspaces.map(({ slug, href, label, permission, planned }) => ({ slug, href, label, permission, planned }))).toEqual([
      { slug: 'stress-explorer', href: '/advanced/stress-explorer', label: 'Agentic Stress-Test Explorer', permission: 'calc:read', planned: undefined },
    ])
    expect(advancedWorkspaces[0].icon).toBe(FlaskConical)
    // findWorkspace includes the Advanced section.
    expect(findWorkspace('stress-explorer')).toBe(advancedWorkspaces[0])
  })

  it('hides the heading and the item without calc:read (cosmetic; the API refuses with 403)', async () => {
    session.granted = ['schema:read', 'source:read', 'domain:read', 'audit:read']
    const texts = sidebarTexts(await renderShell())
    expect(texts).not.toContain('Advanced')
    expect(texts).not.toContain('Agentic Stress-Test Explorer')
    expect(texts).toContain('Administration')
  })

  it('the explorer shell carries the D-44 §8 banner, and its wording is the backend constant', async () => {
    const { SCENARIO_STATEMENT, StressExplorerBanner } = await import('@/components/advanced/StressExplorerBanner')
    expect(SCENARIO_STATEMENT.startsWith('Illustrative stress exploration, not a regulatory stress test')).toBe(true)
    const constants = readFileSync(join(root, '..', '..', 'backend', 'app', 'orchestration', 'constants.py'), 'utf8').replace(/"\s*\n\s*"/g, '')
    expect(constants).toContain(`SCENARIO_STATEMENT = (\n    "${SCENARIO_STATEMENT}"`)
    expect(readFileSync(join(root, '..', '..', 'backend', 'openapi.json'), 'utf8')).toContain(SCENARIO_STATEMENT)
    expect(renderToStaticMarkup(createElement(StressExplorerBanner))).toContain(SCENARIO_STATEMENT)
    // The server's labels.statement wins when a response carries one.
    expect(renderToStaticMarkup(createElement(StressExplorerBanner, { statement: 'From the server.' }))).toContain('From the server.')
    expect(readFileSync(join(root, 'app', '(platform)', 'advanced', 'stress-explorer', 'layout.tsx'), 'utf8')).toContain('<StressExplorerBanner />')
  })
})

describe('the AI Assistant placeholder relabelled (FE-24.1)', () => {
  it('is "WS5 (planned)", and no workspace still says Feature 05', async () => {
    const { findWorkspace, mainWorkspaces, adminWorkspaces, advancedWorkspaces } = await import('@/config/navigation')
    expect(findWorkspace('assistant')?.planned).toEqual({ phase: 'WS5', workstream: 'planned' })
    expect([...mainWorkspaces, ...advancedWorkspaces, ...adminWorkspaces].filter((w) => w.planned?.phase === 'Feature 05')).toEqual([])
  })

  it('the placeholder page reads "Planned for a later release." (P-12)', async () => {
    vi.doMock('@/components/auth/SessionProvider', () => ({ useCurrentSession: () => ({ has: () => true }) }))
    vi.doMock('next/navigation', () => ({ usePathname: () => '/assistant' }))
    vi.resetModules()
    const { PlannedWorkspace } = await import('@/components/common/PlannedWorkspace')
    const html = renderToStaticMarkup(createElement(PlannedWorkspace, { slug: 'assistant' }))
    expect(html.replace(/<[^>]+>|<!-- -->/g, '')).toContain('Planned for a later release.')
    expect(html).not.toContain('WS5')
    expect(html).not.toContain('Feature 05')
  })
})

describe('NEXT_PUBLIC_STRESS_EXPLORER_UI=off (D-02)', () => {
  it('has no "Advanced" heading and the nav equals the Phase 23 list', async () => {
    vi.stubEnv('NEXT_PUBLIC_STRESS_EXPLORER_UI', 'off')
    const html = await renderShell()
    const texts = sidebarTexts(html)
    expect(texts).not.toContain('Advanced')
    expect(texts).not.toContain('Agentic Stress-Test Explorer')
    expect(texts).toEqual([
      'MoodyMate',
      'Financial Risk Platform',
      'Workspace',
      ...PHASE_23_MAIN.map(([, label]) => label),
      'Administration',
      ...PHASE_23_ADMIN.map(([, label]) => label),
    ])
    expect([...html.matchAll(/href="([^"]+)"/g)].map((m) => m[1])).toEqual(['/dashboard', ...[...PHASE_23_MAIN, ...PHASE_23_ADMIN].map(([href]) => href)])
    const { advancedWorkspaces, findWorkspace } = await import('@/config/navigation')
    expect(advancedWorkspaces).toEqual([])
    expect(findWorkspace('stress-explorer')).toBeUndefined()
  })

  const notFound = vi.fn(() => {
    throw new Error('NEXT_NOT_FOUND')
  })
  const renderGate = async () => {
    vi.doMock('next/navigation', () => ({ notFound }))
    vi.resetModules()
    const { AdvancedGate } = await import('@/components/advanced/AdvancedGate')
    return () => renderToStaticMarkup(createElement(AdvancedGate, null, createElement('p', null, 'explorer')))
  }

  it('renders /advanced while on', async () => {
    notFound.mockClear()
    expect((await renderGate())()).toBe('<p>explorer</p>')
    expect(notFound).not.toHaveBeenCalled()
  })

  it('makes /advanced/stress-explorer a 404 while off', async () => {
    notFound.mockClear()
    vi.stubEnv('NEXT_PUBLIC_STRESS_EXPLORER_UI', 'off')
    expect(await renderGate()).toThrow('NEXT_NOT_FOUND')
    expect(notFound).toHaveBeenCalledTimes(1)
    // Every /advanced route sits under the gated layout.
    expect(readFileSync(join(root, 'app', '(platform)', 'advanced', 'layout.tsx'), 'utf8')).toContain('<AdvancedGate>')
  })
})
