import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { BASELINE, CHECKSUM_LABEL, DEMO_BANNER, applyShocks, checksum, ladder, money, problems, propose, ratio } from '../agentDemo'
import { AgentDemo } from '../AgentDemo'
import { LiquidityIntel } from '../LiquidityIntel'

const src = (rel: string) => readFileSync(fileURLToPath(new URL(rel, import.meta.url)), 'utf8')
/** Every source file behind the two demo pages (FE-28.4). */
const DEMO_SOURCES = [
  '../AgentDemo.tsx',
  '../agentDemo.ts',
  '../LiquidityIntel.tsx',
  '../liquidityIntel.ts',
  '../../../app/(platform)/advanced/stress-explorer/agent-demo/page.tsx',
  '../../../app/(platform)/advanced/liquidity-intelligence/page.tsx',
]

/** Backend lifecycle state names (import job, calculation run, scenario, proposal) a mock must not show. */
const PLATFORM_STATES =
  /\b(DRAFT|PENDING_VALIDATION|VALIDATING|VALIDATED|AWAITING_CONFIRMATION|PENDING_APPROVAL|SUBMITTED|IN_REVIEW|APPROVED|REJECTED|QUEUED|RUNNING|SUCCEEDED|SUCCEEDED_WITH_WARNINGS|FAILED|CANCELLED)\b/

describe('agent demo engine', () => {
  it('reproduces the design doc §5 worked example: delay 20% of loan repayments by 14 days', () => {
    expect(BASELINE.slice(0, 3).map((r) => [r.inflows, r.outflows, r.net])).toEqual([[10000, 8000, 2000], [6000, 7000, -1000], [9000, 4000, 5000]])
    const { shocks } = propose('Delay 20% of loan repayments by 14 days')
    expect(shocks).toEqual([{ t: 'DELAYED_INFLOWS', fraction: 20, days: 14, defaults: [] }])
    const s = ladder(applyShocks(shocks))
    expect(s.slice(0, 3).map((r) => money(r.net))).toEqual(['10.00', '0.00', '50.00'])
    expect(s.reduce((a, r) => a + r.net, 0)).toBe(BASELINE.reduce((a, r) => a + r.net, 0))
  })
  it('splits a combined request, flags defaults, asks when nothing matches, and validates bounds', () => {
    expect(propose('20% of loans delayed two weeks, and deposits up 15%, and the interbank line unavailable for 7 days').shocks.map((s) => s.t)).toEqual(['DELAYED_INFLOWS', 'DEPOSIT_WITHDRAWAL', 'FUNDING_UNAVAILABLE'])
    expect(propose('What if deposits run off faster?').shocks[0].defaults).toEqual(['fraction'])
    expect(propose('Show me something scary').ask).toBeTruthy()
    expect(problems({ t: 'DELAYED_INFLOWS', fraction: 150, days: 14, defaults: [] }).fraction).toBeTruthy()
  })
})

describe('demo credibility (FE-28.4)', () => {
  it('guards every ratio against a zero denominator', () => {
    expect(ratio(5, 0)).toBeNull()
    expect(ratio(0, 0)).toBeNull()
    expect(ratio(5, Number.NaN)).toBeNull()
    expect(ratio(1, 4)).toBe(0.25)
  })

  it('labels the hash an illustrative checksum, not a SHA-256 result fingerprint', () => {
    expect(CHECKSUM_LABEL).toBe('illustrative checksum (not a SHA-256 result fingerprint)')
    expect(checksum(BASELINE)).toMatch(/^[0-9a-f]{8}$/)
  })

  it('no "verified" or "exact" claim remains in either demo page', () => {
    for (const file of DEMO_SOURCES) {
      const text = src(file)
      expect(text, file).not.toMatch(/verif/i)
      expect(text, file).not.toMatch(/exact/i)
      expect(text, file).not.toMatch(/reproduces it/i)
    }
    // Q20: the walkthrough uses mock-only step labels, never the platform's lifecycle states.
    for (const file of DEMO_SOURCES) expect(src(file), file).not.toMatch(PLATFORM_STATES)
    // Hard-coded fingerprints are gone from the Liquidity Intelligence mock.
    for (const fp of ['9f3c1a07', '4be0d915', 'c71e0b42']) expect(src('../LiquidityIntel.tsx') + src('../liquidityIntel.ts')).not.toContain(fp)
  })

  it('both demo pages render the illustrative-mock banner and no "verified"/"exact" text', () => {
    expect(DEMO_BANNER).toBe('Illustrative mock: browser arithmetic on synthetic data, not the calculation engine')
    for (const html of [renderToStaticMarkup(createElement(AgentDemo)), renderToStaticMarkup(createElement(LiquidityIntel))]) {
      expect(html).toContain(DEMO_BANNER)
      expect(html).not.toMatch(/verif|exact/i)
      expect(html).not.toMatch(PLATFORM_STATES)
    }
  })
})
