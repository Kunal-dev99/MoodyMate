import { describe, expect, it } from 'vitest'
import { BASELINE, applyShocks, ladder, money, problems, propose } from '../agentDemo'

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
