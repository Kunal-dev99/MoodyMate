import { describe, expect, it } from 'vitest'
import { DEFAULT_LANDING, safeRedirectPath } from '@/lib/redirect'

const ORIGIN = 'http://localhost:3000'

describe('safeRedirectPath (FE-2.3)', () => {
  it.each([
    '/access',
    '/access/users?page=2',
    '/schemas#top',
  ])('keeps same-origin path %s', (path) => {
    expect(safeRedirectPath(path, ORIGIN)).toBe(path)
  })

  it.each([
    ['protocol-relative', '//evil.example'],
    ['backslash', '/\\evil.example'],
    ['encoded backslash', '/%5Cevil.example'],
    ['encoded slash', '/%2Fevil.example'],
    ['tab-separated', '/\t/evil.example'],
    ['absolute https', 'https://evil.example/x'],
    ['javascript', 'javascript:alert(1)'],
    ['relative without slash', 'evil.example'],
    ['empty', ''],
    ['null', null],
    ['dot segment', '/.//evil.example'],
    ['parent segment', '/..//evil.example'],
    ['encoded dot segment', '/%2e//evil.example'],
    ['encoded parent segment', '/%2e%2e//evil.example'],
    ['nested parent segment', '/a/..//evil.example'],
    ['dot segment with backslash', '/./\\evil.example'],
  ])('rejects %s', (_label, raw) => {
    expect(safeRedirectPath(raw as string | null, ORIGIN)).toBe(DEFAULT_LANDING)
  })

  it('never returns a value the browser resolves to another origin', () => {
    const payloads = ['/.//e.x', '/..//e.x', '/%2e//e.x', '/a/b/../..//e.x', '/\\e.x', '//e.x', '/\t/e.x']
    for (const raw of payloads) {
      const out = safeRedirectPath(raw, ORIGIN)
      expect(new URL(out, ORIGIN).origin).toBe(ORIGIN)
    }
  })

  it('keeps a legitimate path containing a bare percent sign', () => {
    expect(safeRedirectPath('/reports?q=100%', ORIGIN)).toBe('/reports?q=100%')
  })
})
