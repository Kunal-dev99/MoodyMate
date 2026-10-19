import { describe, expect, it } from 'vitest'
import { LATEST, PREVIOUS, bridge, calculate, pct, pp, shockDeposits } from '../liquidityIntel'

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
})
