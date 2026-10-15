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
    ['malformed encoding', '/%E0%A4%A'],
  ])('rejects %s', (_label, raw) => {
    expect(safeRedirectPath(raw as string | null, ORIGIN)).toBe(DEFAULT_LANDING)
  })
})
