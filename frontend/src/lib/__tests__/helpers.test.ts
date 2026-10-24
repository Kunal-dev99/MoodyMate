import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { ApiError } from '@/api/client'
import { COMMENT_MAX, commentProblem, dateProblem, isIsoDate, textLength } from '@/lib/forms'
import { newIdempotencyKey } from '@/lib/idempotency'
import { isAuthorOf, makerCheckerGate, mayDecide } from '@/lib/maker-checker'
import { refusalOf } from '@/lib/refusal'

/*
 * FE-29.1 / FE-29.2: the shared helpers, and the grep the Done-when names, kept as a test so a
 * second copy cannot creep back in.
 */

const SRC = fileURLToPath(new URL('../..', import.meta.url))

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) return name === '__tests__' || name === 'api' ? [] : sources(path)
    return /\.tsx?$/.test(name) ? [path] : []
  })
}

const FILES = sources(SRC).map((path) => ({ path: relative(SRC, path), text: readFileSync(path, 'utf8') }))

/** The files that define `name` as a function, an arrow function, a class, an interface or a type. */
function definitions(name: string): string[] {
  const rx = new RegExp(`(?:function\\s+${name}\\b|(?:const|let)\\s+${name}\\s*[:=]|interface\\s+${name}\\b|type\\s+${name}\\s*[=<])`)
  return FILES.filter((f) => rx.test(f.text)).map((f) => f.path)
}

const envelope = (code: string) => ({ code, message: `${code} message`, details: null, correlation_id: 'cid-29', retryable: false })

describe('one definition each (FE-29.1, FE-29.2)', () => {
  it.each([
    ['newIdempotencyKey', ['lib/idempotency.ts']],
    ['Refusal', ['lib/refusal.ts']],
    ['refusalOf', ['lib/refusal.ts']],
    ['dateProblem', ['lib/forms.ts']],
    ['isIsoDate', ['lib/forms.ts']],
    ['COMMENT_MAX', ['lib/forms.ts']],
    ['makerCheckerGate', ['lib/maker-checker.ts']],
    ['isMasked', ['components/common/Masked.tsx']],
  ])('%s is defined once', (name, where) => {
    expect(definitions(name)).toEqual(where)
  })

  it('<Masked> and <StatusPill> are each one component (local `type Masked` aliases name the generated type only)', () => {
    expect(FILES.filter((f) => /function\s+Masked\b/.test(f.text)).map((f) => f.path)).toEqual(['components/common/Masked.tsx'])
    expect(FILES.filter((f) => /type\s+Masked\s*=/.test(f.text)).every((f) => /type\s+Masked\s*=\s*Schemas\['MaskedValue'\]/.test(f.text))).toBe(true)
    expect(FILES.filter((f) => /function\s+StatusPill\b/.test(f.text)).map((f) => f.path).sort()).toEqual(['components/common/StatusPill.tsx'])
  })

  it('commentProblem has one implementation; drift.ts only binds its short wording', () => {
    expect(FILES.filter((f) => /function\s+commentProblem\b/.test(f.text)).map((f) => f.path)).toEqual(['lib/forms.ts'])
    expect(definitions('commentProblem').sort()).toEqual(['components/ingestion/drift.ts', 'lib/forms.ts'])
    const drift = FILES.find((f) => f.path === 'components/ingestion/drift.ts')!.text
    expect(drift).toContain("export const commentProblem = (comment: string): string | null => sharedCommentProblem(comment, { style: 'short' })")
  })

  it('no masked literal is rendered outside <Masked>', () => {
    const literal = /data-masked="true"|data-testid="masked-value"|>\s*masked\s*<|: masked\b|^\s*masked\s*$/m
    expect(FILES.filter((f) => f.path.endsWith('.tsx') && literal.test(f.text)).map((f) => f.path)).toEqual(['components/common/Masked.tsx'])
  })

  it('every pill renders through the one StatusPill', () => {
    const pillClass = /rounded-sm border px-2 py-0\.5 text-xs font-medium/
    expect(FILES.filter((f) => pillClass.test(f.text)).map((f) => f.path).sort()).toEqual(['components/common/StatusPill.tsx', 'components/ui/badge.tsx'])
  })
})

describe('newIdempotencyKey (FE-29.1)', () => {
  it('gives a fresh key of 1 to 255 visible ASCII characters each time', () => {
    const keys = new Set(Array.from({ length: 20 }, newIdempotencyKey))
    expect(keys.size).toBe(20)
    for (const k of keys) expect(k).toMatch(/^[\x21-\x7e]{1,255}$/)
  })
})

describe('refusalOf (FE-29.1)', () => {
  const TITLES = { forbidden: 'You may not do this', self_approval: 'You cannot decide on your own version' }

  it('keeps the server envelope and titles a known code', () => {
    expect(refusalOf(new ApiError(409, envelope('self_approval')), 'Could not decide', TITLES)).toEqual({
      title: 'You cannot decide on your own version',
      status: 409,
      code: 'self_approval',
      message: 'self_approval message',
      cid: 'cid-29',
    })
  })

  it('gives an unknown code, or a code that is only an Object property, the fallback title', () => {
    expect(refusalOf(new ApiError(500, envelope('internal_error')), 'Could not decide', TITLES).title).toBe('Could not decide')
    expect(refusalOf(new ApiError(400, envelope('constructor')), 'Could not decide', TITLES).title).toBe('Could not decide')
    expect(refusalOf(new ApiError(403, envelope('forbidden')), 'Could not decide').title).toBe('Could not decide')
  })

  it('never shows a non-API error as a server answer', () => {
    expect(refusalOf(new Error('boom'), 'Could not decide', TITLES)).toEqual({ title: 'Could not decide', status: null, code: 'unexpected_error', message: 'Something went wrong.', cid: null })
  })
})

describe('commentProblem (FE-29.1)', () => {
  it('requires a comment unless told otherwise (boolean or option)', () => {
    expect(commentProblem('  \n ')).toBe('A comment is required.')
    expect(commentProblem('', false)).toBeNull()
    expect(commentProblem('', { required: false })).toBeNull()
    expect(commentProblem('ok', { required: true })).toBeNull()
  })

  it('counts code points after trimming, as the server does, so an astral character is one', () => {
    const astral = '😀'.repeat(COMMENT_MAX)
    expect(astral.length).toBe(2 * COMMENT_MAX)
    expect(textLength(`  ${astral}  `)).toBe(COMMENT_MAX)
    expect(commentProblem(astral)).toBeNull()
    expect(commentProblem(`${astral}x`)).toBe('At most 1,000 characters (1,001 now).')
  })

  it('states the limit in the counted or the short wording, and honours a schema max', () => {
    expect(commentProblem('x'.repeat(COMMENT_MAX + 1))).toBe('At most 1,000 characters (1,001 now).')
    expect(commentProblem('x'.repeat(COMMENT_MAX + 1), { style: 'short' })).toBe('At most 1000 characters.')
    expect(commentProblem('x'.repeat(11), { max: 10 })).toBe('At most 10 characters (11 now).')
    expect(commentProblem('x'.repeat(10), { max: 10 })).toBeNull()
  })
})

describe('dateProblem and isIsoDate (FE-29.1)', () => {
  it.each(['2024-02-29', '2000-02-29', '2026-12-31', '0001-01-01', '9999-12-31'])('accepts the real date %s', (d) => {
    expect(isIsoDate(d)).toBe(true)
    expect(dateProblem(d)).toBeNull()
  })

  it.each(['1900-02-29', '2026-02-30', '2026-04-31', '2026-13-01', '2026-00-10', '2026-01-00', '0000-01-01', '2026-1-1', '2026-01-01T00:00:00Z', ' 2026-01-01', '20260101'])(
    'refuses %j',
    (d) => {
      expect(isIsoDate(d)).toBe(false)
      expect(dateProblem(d)).toBe('A valid date, YYYY-MM-DD.')
    },
  )

  it('names a missing date, unless it is optional', () => {
    expect(dateProblem('')).toBe('A date is required.')
    expect(dateProblem('', { missing: 'An effective date is required.' })).toBe('An effective date is required.')
    expect(dateProblem('', { required: false })).toBeNull()
  })
})

describe('the maker-checker gate (FE-29.2)', () => {
  const me = { user_id: 'u-me' }
  const base = { inReview: true, authorUserId: 'u-other', me, mayApprove: true }

  it('allows the checker who holds the permission and is not the author', () => {
    expect(makerCheckerGate(base)).toBe('allowed')
    expect(mayDecide(base)).toBe(true)
  })

  it('checks the state, then the author, then the switch, then the permission', () => {
    const everythingWrong = { inReview: false, authorUserId: 'u-me', me, mayApprove: false, enabled: false }
    expect(makerCheckerGate(everythingWrong)).toBe('not_in_review')
    expect(makerCheckerGate({ ...everythingWrong, inReview: true })).toBe('self_approval')
    expect(makerCheckerGate({ ...everythingWrong, inReview: true, authorUserId: 'u-other' })).toBe('change_disabled')
    expect(makerCheckerGate({ ...everythingWrong, inReview: true, authorUserId: 'u-other', enabled: true })).toBe('no_permission')
  })

  it('never offers the author a decision, even with the permission', () => {
    expect(makerCheckerGate({ ...base, authorUserId: 'u-me' })).toBe('self_approval')
    expect(mayDecide({ ...base, authorUserId: 'u-me' })).toBe(false)
  })

  it('treats a seeded item (no author user) as authored by nobody', () => {
    expect(isAuthorOf(null, me)).toBe(false)
    expect(isAuthorOf(undefined, me)).toBe(false)
    expect(makerCheckerGate({ ...base, authorUserId: null })).toBe('allowed')
  })
})
