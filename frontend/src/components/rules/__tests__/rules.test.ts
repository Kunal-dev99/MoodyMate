import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import type { Schemas } from '@/api/client'
import {
  RULE_STATUS_STYLE,
  compareDecimalText,
  decisionGate,
  editBuckets,
  editorFor,
  editorProblems,
  mergePatch,
  proposalBody,
  proposalProblems,
  type Editor,
  type RuleDetail,
} from '@/components/rules/rules'

// The OpenAPI document the types are generated from: enums are tested against it (D-23).
const openapi = JSON.parse(readFileSync(fileURLToPath(new URL('../../../../../backend/openapi.json', import.meta.url)), 'utf8'))
const enumOf = (name: string): string[] => [...openapi.components.schemas[name].enum].sort()

// The seeded maturity_buckets parameters (D-35 §1), as GET /rules/{id} sends them.
const SEEDED = {
  buckets: [
    [0, 1],
    [1, 7],
    [7, 30],
    [30, 90],
    [90, 180],
    [180, 365],
    [365, 730],
    [730, null],
  ].map(([lower, upper], i) => ({
    code: `B0${i + 1}`,
    label: upper === null ? `(${lower},inf)` : `(${lower},${upper}]`,
    lower_exclusive: lower,
    upper_inclusive: upper,
  })),
}
type Kinds = Pick<RuleDetail, 'parameter_schema' | 'vocabulary'>
const buckets: Kinds = { parameter_schema: { kind: 'DAY_BUCKETS', unit: 'calendar days after reporting date' }, vocabulary: [] }
const decimal: Kinds = { parameter_schema: { kind: 'DECIMAL', min: '0', max: '1', scale: 2 }, vocabulary: [] }

describe('rule version status', () => {
  it('styles exactly the OpenAPI status set', () => {
    expect(Object.keys(RULE_STATUS_STYLE).sort()).toEqual(enumOf('RuleVersionStatus'))
  })
})

describe('decimal text is compared as text, never parsed', () => {
  it.each([
    ['0.10', '0.1', 0],
    ['1', '0.99', 1],
    ['-0.5', '0', -1],
    ['-0', '0.00', 0],
    ['10.0', '9.999999999999999999', 1],
    ['-2', '-10', 1],
  ])('%s vs %s', (a, b, expected) => expect(compareDecimalText(a, b)).toBe(expected))

  it('a DECIMAL value goes to the patch as the exact text typed, checked by text against min, max and scale', () => {
    const e = editorFor(decimal, { value: '0.10' })
    expect(editorProblems({ ...e, value: '0.125' } as Editor)).toEqual(['Value: at most 2 decimal places.'])
    expect(editorProblems({ ...e, value: '1.01' } as Editor)).toEqual(['Value: at most 1.'])
    expect(editorProblems({ ...e, value: '1e-2' } as Editor)).toEqual(['Value: must be decimal text, e.g. 0.10.'])
    const body = proposalBody({ ...e, value: '0.50' } as Editor, { value: '0.10' }, 'Raise the run-off')
    expect(body?.parameters_patch).toEqual({ value: '0.50' })
    expect(typeof body?.parameters_patch.value).toBe('string')
  })
})

describe('DAY_BUCKETS editor', () => {
  const e = editorFor(buckets, SEEDED)
  if (e.kind !== 'DAY_BUCKETS') throw new Error('expected the bucket editor')

  it('is filled from the ACTIVE version, the last bucket open', () => {
    expect(e.buckets).toHaveLength(8)
    expect(e.buckets[7]).toEqual({ code: 'B08', lower: '730', upper: '' })
    expect(editorProblems(e)).toEqual([])
  })

  it('keeps buckets contiguous: a new upper bound moves the next lower bound', () => {
    const moved = editBuckets(e.buckets, { type: 'upper', index: 1, value: '14' })
    expect(moved[1]).toEqual({ code: 'B02', lower: '1', upper: '14' })
    expect(moved[2].lower).toBe('14')
    const body = proposalBody({ ...e, buckets: moved }, SEEDED, 'Two-week second bucket')
    expect(Object.keys(body!.parameters_patch)).toEqual(['buckets'])
    const sent = body!.parameters_patch.buckets as (typeof SEEDED)['buckets']
    expect(sent[1]).toEqual({ code: 'B02', label: '(1,14]', lower_exclusive: 1, upper_inclusive: 14 })
    expect(sent[2]).toEqual({ code: 'B03', label: '(14,30]', lower_exclusive: 14, upper_inclusive: 30 })
  })

  it('flags what BE-16.1 refuses: bounds out of order, duplicate codes, a missing bound', () => {
    expect(editorProblems({ ...e, buckets: editBuckets(e.buckets, { type: 'upper', index: 1, value: '40' }) })).toContain(
      'Bucket 3: the upper bound must be greater than the lower bound.',
    )
    expect(editorProblems({ ...e, buckets: editBuckets(e.buckets, { type: 'code', index: 1, value: 'B01' }) })).toContain('Bucket 2: the code B01 is used twice.')
    const added = editBuckets(e.buckets, { type: 'add' })
    expect(added).toHaveLength(9)
    expect(added[8]).toEqual({ code: 'B08', lower: '', upper: '' })
    expect(editorProblems({ ...e, buckets: added })).toContain('Bucket 8 upper bound: a whole number is required.')
  })

  it('refuses no change and a missing summary before the server does', () => {
    expect(proposalBody(e, SEEDED, 'x')).toBeNull()
    expect(proposalProblems(e, SEEDED, '')).toEqual([
      'Nothing changed from the ACTIVE version (the server refuses it: no_change).',
      'A change summary is required.',
    ])
  })
})

describe('merge patch (RFC 7386)', () => {
  it('sends only changed keys, null for a removed map entry', () => {
    expect(mergePatch({ map: { BOND: 'INFLOW', LOAN: 'INFLOW' } }, { map: { BOND: 'OUTFLOW' } })).toEqual({ map: { BOND: 'OUTFLOW', LOAN: null } })
  })
})

describe('maker-checker gate (cosmetic; the server answers 409 self_approval)', () => {
  const rule = { change_enabled: true }
  const inReview = { status: 'IN_REVIEW', author_user_id: 'u-analyst' } as const
  const holds = (...p: Schemas['Permission'][]) => (x: Schemas['Permission']) => p.includes(x)

  it('never offers the author a decision, even with rule:approve', () => {
    expect(decisionGate(rule, inReview, { user_id: 'u-analyst' }, holds('rule:approve', 'rule:propose'))).toBe('self_approval')
  })
  it('offers another approver the decision; not without rule:approve, while switched off, or before submit', () => {
    expect(decisionGate(rule, inReview, { user_id: 'u-approver' }, holds('rule:approve'))).toBe('allowed')
    expect(decisionGate(rule, inReview, { user_id: 'u-other' }, holds('rule:read'))).toBe('no_permission')
    expect(decisionGate({ change_enabled: false }, inReview, { user_id: 'u-approver' }, holds('rule:approve'))).toBe('change_disabled')
    expect(decisionGate(rule, { ...inReview, status: 'DRAFT' }, { user_id: 'u-approver' }, holds('rule:approve'))).toBe('not_in_review')
  })
})
