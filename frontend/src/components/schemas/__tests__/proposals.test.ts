import { describe, expect, it } from 'vitest'
import {
  PROPOSAL_STATUS_STYLE,
  canDecide,
  canSubmit,
  draftProblems,
  emptyField,
  type Proposal,
} from '@/components/schemas/proposals'

const proposal = (extra: Partial<Proposal>): Proposal =>
  ({ status: 'DRAFT', impact: null, proposer_user_id: 'maker', ...extra }) as Proposal

describe('schema change proposals (FE-5.1)', () => {
  it('styles every proposal status', () => {
    expect(Object.keys(PROPOSAL_STATUS_STYLE).sort()).toEqual(
      ['APPLIED', 'APPROVED', 'DRAFT', 'IN_REVIEW', 'REJECTED', 'SUBMITTED', 'SUPERSEDED'],
    )
  })
  it('keeps Submit disabled until an impact analysis exists', () => {
    expect(canSubmit(proposal({}), 'maker')).toBe(false)
    const analysed = proposal({ impact: {} as Proposal['impact'] })
    expect(canSubmit(analysed, 'maker')).toBe(true)
    expect(canSubmit(analysed, 'someone-else')).toBe(false)
  })
  it('offers a decision only to a different user with approval rights', () => {
    const p = proposal({ status: 'SUBMITTED' })
    expect(canDecide(p, 'checker', true)).toBe(true)
    expect(canDecide(p, 'maker', true)).toBe(false)
    expect(canDecide(p, 'checker', false)).toBe(false)
    expect(canDecide(proposal({ status: 'DRAFT' }), 'checker', true)).toBe(false)
  })
  it('reports draft problems before the request', () => {
    const ok = { ...emptyField(), name: 'amount' }
    expect(draftProblems([ok])).toEqual([])
    expect(draftProblems([ok, ok])).toEqual(['amount: duplicate field name.'])
    expect(draftProblems([{ ...ok, name: 'Bad Name' }])[0]).toContain('lower_snake_case')
    expect(draftProblems([{ ...ok, precision: 4, scale: 6 }])).toEqual(['amount: scale cannot exceed precision.'])
    expect(draftProblems([])).toEqual(['Add at least one field.'])
  })
  it('requires decimal bounds and whole numbers in range', () => {
    const dec = { ...emptyField(), name: 'amount', data_type: 'DECIMAL' as const }
    expect(draftProblems([{ ...dec, precision: 18, scale: 6 }])).toEqual([])
    expect(draftProblems([{ ...dec, precision: 18 }])).toEqual(['amount: a DECIMAL needs both precision and scale.'])
    expect(draftProblems([{ ...dec, precision: Number.NaN, scale: 2 }])[0]).toContain('whole numbers')
    expect(draftProblems([{ ...dec, precision: 40, scale: 2 }])).toEqual(['amount: precision must be 1–38.'])
  })
})
