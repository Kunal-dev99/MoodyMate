import { describe, expect, it } from 'vitest'
import { environmentLabel } from '@/components/layout/EnvironmentBanner'

describe('environment banner (FE-6.1)', () => {
  it('reads LOCAL · SYNTHETIC DATA in local dev', () => {
    expect(environmentLabel({ env: 'local', data_class: 'synthetic' })).toBe('LOCAL · SYNTHETIC DATA')
  })
  it('names client data in staging', () => {
    expect(environmentLabel({ env: 'staging', data_class: 'client' })).toBe('STAGING · CLIENT DATA')
  })
  it('says so when the environment cannot be read', () => {
    expect(environmentLabel(null)).toBe('ENVIRONMENT UNKNOWN · TREAT DATA AS REAL')
  })
  it('is not shown in production', () => {
    expect(environmentLabel({ env: 'production', data_class: 'client' })).toBeNull()
    expect(environmentLabel({ env: 'production', data_class: 'synthetic' })).toBeNull()
  })
})
