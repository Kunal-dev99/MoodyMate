import { describe, expect, it } from 'vitest'
import { isMasked } from '@/components/common/MaskedValue'

describe('isMasked (FE-3.2)', () => {
  it('recognises the server masking marker', () => {
    expect(isMasked({ masked: true, classification: 'restricted' })).toBe(true)
  })
  it('still treats a marker with an empty classification as masked', () => {
    expect(isMasked({ masked: true, classification: '' })).toBe(true)
  })
  it.each([
    ['plain string', 'pat@example.test'],
    ['null', null],
    ['empty string', ''],
    ['object without marker', { classification: 'restricted' }],
    ['masked false', { masked: false, classification: 'internal' }],
    ['missing classification', { masked: true }],
  ])('does not treat %s as masked', (_label, value) => {
    expect(isMasked(value)).toBe(false)
  })
})
