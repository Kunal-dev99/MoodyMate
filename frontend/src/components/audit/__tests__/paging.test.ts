import { describe, expect, it } from 'vitest'
import { cursorsFor, pageView } from '@/components/audit/paging'

describe('audit timeline paging (FE-3.1)', () => {
  it('offers Older only when a further row exists', () => {
    expect(pageView([1, 2, 3], 3)).toEqual({ shown: [1, 2, 3], hasOlder: false })
    expect(pageView([1, 2, 3, 4], 3)).toEqual({ shown: [1, 2, 3], hasOlder: true })
  })
  it('drops cursors that belong to another resource or filter set', () => {
    const paging = { scope: 'schema|A|', cursors: ['evt-1'] }
    expect(cursorsFor(paging, 'schema|A|')).toEqual(['evt-1'])
    expect(cursorsFor(paging, 'schema|B|')).toEqual([])
  })
})
