import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { KeysetPager } from '@/components/audit/AuditTimeline'
import { cursorsFor, keysetNext, pageView } from '@/components/audit/paging'

describe('audit timeline paging (FE-3.1)', () => {
  it('offers Older only when a further row exists', () => {
    expect(pageView([1, 2, 3], 3)).toEqual({ shown: [1, 2, 3], hasOlder: false })
    expect(pageView([1, 2, 3, 4], 3)).toEqual({ shown: [1, 2, 3], hasOlder: true })
    // FE-33.1 (Q17): Older is driven by the page's has_more and sends `before` (the last shown id).
    const shown = [{ id: 'evt-3' }, { id: 'evt-2' }]
    expect(keysetNext(shown, true)).toEqual({ has_more: true, before: 'evt-2' })
    expect(keysetNext(shown, false)).toEqual({ has_more: false, before: null })
    expect(keysetNext([], true)).toEqual({ has_more: false, before: null })
    const event = (id: string) => ({ id }) as Parameters<typeof KeysetPager>[0]['page']['items'][number]
    const pager = (hasOlder: boolean, depth: number) =>
      renderToStaticMarkup(
        createElement(KeysetPager, { page: { items: [event('evt-3'), event('evt-2')], total: 5, index: depth, hasOlder }, depth, loading: false, onNewer: () => {}, onOlder: () => {} }),
      )
    const first = pager(true, 0)
    expect(first).toContain('data-has-more="true"')
    expect(first).toContain('Showing 2 of 5 (rows 1–2)')
    expect(first).toMatch(/<button[^>]*data-testid="audit-pager-older"[^>]*data-before="evt-2"/)
    expect(first).not.toMatch(/<button[^>]*disabled=""[^>]*data-testid="audit-pager-older"/)
    // The last page: has_more false, Older is not offered.
    const last = pager(false, 1)
    expect(last).toContain('data-has-more="false"')
    expect(last).toMatch(/<button[^>]*disabled=""[^>]*data-testid="audit-pager-older"/)
    expect(last).not.toContain('data-before=')
  })
  it('drops cursors that belong to another resource or filter set', () => {
    const paging = { scope: 'schema|A|', cursors: ['evt-1'] }
    expect(cursorsFor(paging, 'schema|A|')).toEqual(['evt-1'])
    expect(cursorsFor(paging, 'schema|B|')).toEqual([])
  })
})
