import { describe, expect, it } from 'vitest'
import { formatMoney, formatTimestamp, groupDecimal } from '@/lib/format'

describe('groupDecimal (P-7)', () => {
  it.each([
    ['0', '0'],
    ['12', '12'],
    ['999', '999'],
    ['1000', '1,000'],
    ['4443500.86', '4,443,500.86'],
    ['1234567890123456789.123456789', '1,234,567,890,123,456,789.123456789'],
    ['-1234567.5', '-1,234,567.5'],
    ['+1000', '+1,000'],
    ['1000.00', '1,000.00'],
    ['0.000001', '0.000001'],
  ])('groups %s as %s', (input, expected) => {
    expect(groupDecimal(input)).toBe(expected)
  })

  it('keeps precision a float would lose', () => {
    // 2^53 + 1 is not representable as a float.
    expect(groupDecimal('9007199254740993.10')).toBe('9,007,199,254,740,993.10')
  })

  it.each(['', 'abc', '1e6', '1,000', '1.2.3'])('leaves non-decimal %j unchanged', (input) => {
    expect(groupDecimal(input)).toBe(input)
  })
})

describe('formatMoney (P-7)', () => {
  it('appends the currency', () => {
    expect(formatMoney('4443500.86', 'EUR')).toBe('4,443,500.86 EUR')
  })
  it('omits a missing currency', () => {
    expect(formatMoney('1000', null)).toBe('1,000')
  })
})

describe('formatTimestamp (P-5)', () => {
  it('renders UTC in a fixed style', () => {
    expect(formatTimestamp('2026-10-19T09:15:42Z')).toBe('19 Oct 2026, 09:15 UTC')
    expect(formatTimestamp('2026-01-02T23:05:00+02:00')).toBe('2 Jan 2026, 21:05 UTC')
  })
  it('renders missing values as an em dash', () => {
    expect(formatTimestamp(null)).toBe('—')
    expect(formatTimestamp(undefined)).toBe('—')
  })
  it('returns an unparseable string as-is', () => {
    expect(formatTimestamp('not a date')).toBe('not a date')
  })
})
