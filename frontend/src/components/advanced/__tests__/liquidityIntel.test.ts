import { describe, expect, it } from 'vitest'
import {
  LATEST, NOT_COMPUTABLE, PREVIOUS, RUNOFF_COLUMN, bridge, calculate, lcrDelta, movementText, pct, pp, shockDeposits,
} from '../liquidityIntel'

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
