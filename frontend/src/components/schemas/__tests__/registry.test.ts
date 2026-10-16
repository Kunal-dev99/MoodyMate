import { describe, expect, it } from 'vitest'
import type { Field } from '@/components/schemas/registry'
import { COMPATIBILITY_STYLE, STATUS_STYLE, alignFields, typeLabel, valueLabel } from '@/components/schemas/registry'

const field = (name: string, extra: Partial<Field> = {}): Field => ({
  position: 1,
  name,
  data_type: 'STRING',
  required: false,
  unit: null,
  precision: null,
  scale: null,
  classification: 'internal',
  extension_eligible: false,
  description: null,
  ...extra,
})

describe('schema registry display (FE-4.1, FE-4.2)', () => {
  it('styles every platform status, not the bundle draft/published set', () => {
    expect(Object.keys(STATUS_STYLE).sort()).toEqual(
      ['ACTIVE', 'APPROVED', 'DEPRECATED', 'DRAFT', 'IN_REVIEW', 'REJECTED', 'RETIRED'],
    )
    expect(STATUS_STYLE.ACTIVE.className).toBe('status-published')
    expect(STATUS_STYLE.REJECTED.className).toBe('status-failed')
  })
  it('gives each compatibility class its own badge', () => {
    expect(COMPATIBILITY_STYLE.ADDITIVE.variant).toBe('success')
    expect(COMPATIBILITY_STYLE.COMPATIBLE.variant).toBe('info')
    expect(COMPATIBILITY_STYLE.BREAKING.variant).toBe('destructive')
  })
  it('aligns fields by name, keeping removed fields', () => {
    const rows = alignFields([field('a'), field('gone')], [field('a'), field('new')])
    expect(rows.map((r) => [r.name, !!r.from, !!r.to])).toEqual([
      ['a', true, true],
      ['new', false, true],
      ['gone', true, false],
    ])
  })
  it('labels types and values', () => {
    expect(typeLabel(field('r', { data_type: 'DECIMAL', precision: 11, scale: 6, unit: 'percent' }))).toBe(
      'DECIMAL(11,6) percent',
    )
    expect(valueLabel({ precision: 9, scale: 4 })).toBe('precision=9, scale=4')
    expect(valueLabel(null)).toBe('—')
    expect(valueLabel(true)).toBe('true')
  })
})
