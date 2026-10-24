import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import { advancedWorkspaces } from '@/config/navigation'
import {
  DEMO_BANNER, LATEST, NOT_COMPUTABLE, PREVIOUS, RUNOFF_COLUMN, bridge, calculate, lcrDelta, movementText, pct, pp, ratio, shockDeposits,
} from '../liquidityIntel'
import { LiquidityIntel } from '../LiquidityIntel'

describe('liquidity intelligence prototype', () => {
  it('reconciles the LCR movement and keeps the scenario separate from the baseline', () => {
    expect(pct(calculate(PREVIOUS.inputs).lcr)).toBe('150.0000%')
    expect(pct(calculate(LATEST.inputs).lcr)).toBe('125.0000%')
    const b = bridge(PREVIOUS.inputs, LATEST.inputs)
    expect(b.reconciles).toBe(true)
    expect(b.steps.map((s) => pp(s.contribution))).toEqual(['0.0000 pp', '−22.8814 pp', '−4.1678 pp', '+2.0492 pp', '0.0000 pp'])
    expect(pct(calculate(shockDeposits(LATEST.inputs, 25)).lcr)).toBe('98.6842%')
    expect(LATEST.inputs.depositOutflows).toBe(12800)
  })

  it('zero or negative net outflows give lcr null and "not computable" (FE-28.4)', () => {
    const zero = calculate({ hqla: 15000, depositOutflows: 0, otherOutflows: 0, inflows: 500 })
    expect(zero.netOutflows).toBe(0)
    expect(zero.lcr).toBeNull()
    expect(pct(zero.lcr)).toBe(NOT_COMPUTABLE)
    expect(NOT_COMPUTABLE).toBe('not computable')
    expect(pp(lcrDelta(zero.lcr, calculate(LATEST.inputs).lcr))).toBe('not computable')
    expect(movementText(zero.lcr, calculate(LATEST.inputs).lcr)).toContain('not computable')
    // A bridge touching a non-computable run never claims to reconcile.
    const b = bridge({ hqla: 15000, depositOutflows: 0, otherOutflows: 0, inflows: 0 }, LATEST.inputs)
    expect(b.reconciles).toBe(false)
    expect(b.steps[0].contribution).toBeNull()
  })

  it('derives the movement text from the computed values, not a hard-coded figure (FE-28.4)', () => {
    const prev = calculate(PREVIOUS.inputs).lcr
    const latest = calculate(LATEST.inputs).lcr
    const d = lcrDelta(prev, latest)!
    expect(movementText(prev, latest)).toBe(`LCR fell ${(Math.abs(d) / 10000).toFixed(4)} pp, from ${pct(prev)} to ${pct(latest)}`)
    expect(movementText(prev, latest)).toBe('LCR fell 25.0000 pp, from 150.0000% to 125.0000%')
    // Change the inputs and the text follows.
    const higher = calculate({ ...LATEST.inputs, hqla: LATEST.inputs.hqla * 2 }).lcr
    expect(movementText(latest, higher)).toBe(`LCR rose ${(lcrDelta(latest, higher)! / 10000).toFixed(4)} pp, from ${pct(latest)} to ${pct(higher)}`)
    expect(movementText(latest, latest)).toBe(`LCR unchanged at ${pct(latest)}`)
    expect(RUNOFF_COLUMN).toBe('Illustrative run-off (not applied)')
  })
})

const src = (rel: string) => readFileSync(fileURLToPath(new URL(rel, import.meta.url)), 'utf8')
/** frontend/src */
const SRC = join(__dirname, '..', '..', '..')
/** Every source file behind the demo page (FE-28.4). */
const DEMO_SOURCES = ['../LiquidityIntel.tsx', '../liquidityIntel.ts', '../../../app/(platform)/advanced/liquidity-intelligence/page.tsx']

/** Backend lifecycle state names (import job, calculation run, scenario, proposal) a mock must not show. */
const PLATFORM_STATES =
  /\b(DRAFT|PENDING_VALIDATION|VALIDATING|VALIDATED|AWAITING_CONFIRMATION|PENDING_APPROVAL|SUBMITTED|IN_REVIEW|APPROVED|REJECTED|QUEUED|RUNNING|SUCCEEDED|SUCCEEDED_WITH_WARNINGS|FAILED|CANCELLED)\b/

describe('demo credibility (FE-28.4)', () => {
  it('guards every ratio against a zero denominator', () => {
    expect(ratio(5, 0)).toBeNull()
    expect(ratio(0, 0)).toBeNull()
    expect(ratio(5, Number.NaN)).toBeNull()
    expect(ratio(1, 4)).toBe(0.25)
  })

  it('no "verified" or "exact" claim, platform lifecycle state or hard-coded fingerprint remains in the demo page', () => {
    for (const file of DEMO_SOURCES) {
      const text = src(file)
      expect(text, file).not.toMatch(/verif/i)
      expect(text, file).not.toMatch(/exact/i)
      expect(text, file).not.toMatch(/reproduces it/i)
      // Q20: the walkthrough uses mock-only step labels, never the platform's lifecycle states.
      expect(text, file).not.toMatch(PLATFORM_STATES)
    }
    for (const fp of ['9f3c1a07', '4be0d915', 'c71e0b42']) expect(src('../LiquidityIntel.tsx') + src('../liquidityIntel.ts')).not.toContain(fp)
  })

  it('the page renders the illustrative-mock banner and no "verified"/"exact" text', () => {
    expect(DEMO_BANNER).toBe('Illustrative mock: browser arithmetic on synthetic data, not the calculation engine')
    const html = renderToStaticMarkup(createElement(LiquidityIntel))
    expect(html).toContain(DEMO_BANNER)
    expect(html).not.toMatch(/verif|exact/i)
    expect(html).not.toMatch(PLATFORM_STATES)
  })
})

describe('the explorer demo retired (FE-35.6 Done-when)', () => {
  // Built from parts so this file does not match itself.
  const RETIRED = new RegExp(['agent', 'demo'].join('[-_ ]?'), 'i')
  const files = (dir: string): string[] =>
    readdirSync(dir).flatMap((name) => {
      const path = join(dir, name)
      return statSync(path).isDirectory() ? files(path) : /\.(ts|tsx|mjs|css|json)$/.test(name) ? [path] : []
    })

  it('leaves no route, component, test or link that names it anywhere in src', () => {
    const all = files(SRC)
    expect(all.length).toBeGreaterThan(100)
    const hits = all.filter((f) => RETIRED.test(readFileSync(f, 'utf8'))).map((f) => relative(SRC, f))
    expect(hits).toEqual([])
    expect(all.map((f) => relative(SRC, f)).filter((f) => RETIRED.test(f))).toEqual([])
  })

  it('keeps Liquidity Intelligence, labelled as a mock prototype', () => {
    const li = advancedWorkspaces.find((w) => w.slug === 'liquidity-intelligence')
    expect(li?.href).toBe('/advanced/liquidity-intelligence')
    expect(li?.description).toMatch(/^Prototype: .*Mock agent, synthetic data\.$/)
    expect(src('../../../app/(platform)/advanced/liquidity-intelligence/page.tsx')).toContain('Prototype · mock agent · synthetic data')
  })

  it('a segment under the explorer that is not a scenario id is a 404 (the former route included)', async () => {
    const layout = src('../../../app/(platform)/advanced/stress-explorer/[scenarioId]/layout.tsx')
    expect(layout).toContain('notFound()')
    const [, body] = /const SCENARIO_ID = \/(.+)\/i/.exec(layout) ?? []
    const id = new RegExp(body, 'i')
    expect(id.test('6f1c0f7e-0000-4000-8000-000000000002')).toBe(true)
    for (const segment of [['agent', 'demo'].join('-'), 'new-ish', '1', '']) expect(id.test(segment), segment).toBe(false)

    // Q19: the layout itself, with its own pattern: a full-match, case-insensitive UUID check.
    const notFound = vi.fn(() => {
      throw new Error('NEXT_NOT_FOUND')
    })
    vi.doMock('next/navigation', () => ({ notFound }))
    vi.resetModules()
    try {
      const { default: ScenarioLayout } = await import('@/app/(platform)/advanced/stress-explorer/[scenarioId]/layout')
      const render = async (scenarioId: string) => renderToStaticMarkup(await ScenarioLayout({ children: createElement('p', null, 'scenario'), params: Promise.resolve({ scenarioId }) }))
      const uuid = '6f1c0f7e-0000-4000-8000-00000000abcd'
      for (const ok of [uuid, uuid.toUpperCase()]) expect(await render(ok), ok).toBe('<p>scenario</p>')
      expect(notFound).not.toHaveBeenCalled()
      for (const bad of [`${uuid}x`, `x${uuid}`, ` ${uuid}`, `${uuid}\n`, uuid.replace(/-/g, ''), `{${uuid}}`, uuid.replace('a', 'g'), ['agent', 'demo'].join('-')])
        await expect(render(bad), JSON.stringify(bad)).rejects.toThrow('NEXT_NOT_FOUND')
      expect(notFound).toHaveBeenCalledTimes(8)
    } finally {
      vi.doUnmock('next/navigation')
      vi.resetModules()
    }
    // An unknown but well-formed id passes the layout; the scenario page keeps its "not found" empty state.
    const view = src('../ScenarioView.tsx')
    expect(view).toMatch(/state\.kind === 'not_found' && \(\s*<EmptyState icon=\{FileWarning\} title="Scenario not found"/)
  })

  it('the server answers that 404 before rendering: the middleware, with the layout pattern (HTTP status)', async () => {
    const { NextRequest } = await import('next/server')
    const { NOT_FOUND_CACHE_CONTROL, SCENARIO_ID, STATIC_SEGMENTS, config, middleware } = await import('@/middleware')
    // The layout's notFound() sits below the client SessionProvider, so it cannot set the status; same pattern here.
    const [, body] = /const SCENARIO_ID = \/(.+)\/i/.exec(src('../../../app/(platform)/advanced/stress-explorer/[scenarioId]/layout.tsx')) ?? []
    expect(SCENARIO_ID.source).toBe(body)
    expect(SCENARIO_ID.flags).toBe('i')
    // Every static route beside [scenarioId] passes; one added later fails here until it is listed.
    const explorer = join(SRC, 'app', '(platform)', 'advanced', 'stress-explorer')
    const statics = readdirSync(explorer).filter((n) => statSync(join(explorer, n)).isDirectory() && !n.startsWith('['))
    expect([...STATIC_SEGMENTS].sort()).toEqual(statics.sort())
    // Only the explorer's sub-paths run it.
    expect(config).toEqual({ matcher: '/advanced/stress-explorer/:segment/:path*' })

    const at = (path: string) => middleware(new NextRequest(new URL(path, 'http://localhost:3000')))
    const uuid = '6f1c0f7e-0000-4000-8000-00000000abcd'
    for (const ok of [uuid, uuid.toUpperCase(), `${uuid}/runs`, `${uuid}/runs/${uuid}`, `${uuid}/versions/new`, `${uuid}/versions/2/edit`, 'new']) {
      const res = at(`/advanced/stress-explorer/${ok}`)
      expect(res.headers.get('x-middleware-next'), ok).toBe('1')
      expect(res.headers.get('x-middleware-rewrite'), ok).toBeNull()
    }
    for (const bad of [['agent', 'demo'].join('-'), 'not-a-scenario', `${['agent', 'demo'].join('-')}/runs`, `${uuid}x`, `x${uuid}`, uuid.replace(/-/g, ''), uuid.replace('a', 'g'), 'new-ish', '%20']) {
      const res = at(`/advanced/stress-explorer/${bad}`)
      expect(res.headers.get('x-middleware-rewrite'), bad).toBe('http://localhost:3000/_not-found')
      expect(res.headers.get('x-middleware-next'), bad).toBeNull()
      // Never cached, like Next's own 404 for an unmatched path.
      expect(res.headers.get('cache-control'), bad).toBe(NOT_FOUND_CACHE_CONTROL)
    }
  })
})
