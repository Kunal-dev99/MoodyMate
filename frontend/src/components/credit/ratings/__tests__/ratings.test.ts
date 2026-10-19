import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ProposalFields } from '@/components/credit/ratings/ProposeRatingDialog'
import { RatingHistory } from '@/components/credit/ratings/RatingsPanel'
import { RatingStatusPill } from '@/components/credit/ratings/RatingStatusPill'
import {
  EMPTY_PROPOSAL,
  RATIONALE_MAX,
  RATING_STATUSES,
  canProposeRating,
  gradeLabel,
  gradeOptions,
  hasProblems,
  isIsoDate,
  proposalBody,
  proposalProblems,
  rationaleCounter,
  type ObligorRating,
  type ProposalField,
  type ProposalForm,
  type VocabularyEntry,
} from '@/components/credit/ratings/ratings'
import { activeSection } from '@/components/registry/RegistryTabs'

/*
 * FE-20.1: the Ratings panel and the "Propose rating" form. Fixtures use the generated types
 * (ObligorRatingOut, CreditVocabularyEntryOut); the status enum and the rationale limit are read
 * from backend/openapi.json, so a contract change fails here.
 */

const openapi = JSON.parse(readFileSync(fileURLToPath(new URL('../../../../../../backend/openapi.json', import.meta.url)), 'utf8'))
const MASKED = { masked: true as const, classification: 'confidential' }
const ME = '99999999-9999-4999-8999-999999999999'

const vocab = (code: string, rank: number | null, over: Partial<VocabularyEntry> = {}): VocabularyEntry => ({
  code,
  name: `Grade ${code}`,
  rank,
  is_default_grade: false,
  provisional: true,
  vocabulary: 'rating_grade',
  ...over,
})
const VOCAB: VocabularyEntry[] = [
  vocab('G01', 1),
  vocab('G05', 5),
  vocab('D', 9, { is_default_grade: true }),
  { code: 'TERM_LOAN', name: 'Term loan', rank: null, is_default_grade: null, provisional: true, vocabulary: 'facility_type' },
]
const GRADES = ['G01', 'G05', 'D']

const rating = (over: Partial<ObligorRating> = {}): ObligorRating => ({
  rating_id: '55555555-5555-4555-8555-555555555551',
  counterparty_id: '11111111-1111-4111-8111-111111111111',
  counterparty_key: 'CP-0008',
  grade: 'G05',
  grade_rank: 5,
  effective_from: '2026-09-30',
  rationale: 'Two quarters of covenant breaches.',
  status: 'IN_REVIEW',
  proposed_by: 'dev:risk_analyst',
  proposer_user_id: ME,
  proposed_at: '2026-10-19T08:00:00Z',
  decided_by: null,
  decided_at: null,
  decision_comment: MASKED,
  superseded_at: null,
  superseded_by_rating_id: null,
  ...over,
})

const text = (html: string) => html.replace(/<[^>]+>|<!-- -->/g, '')
const valid: ProposalForm = { grade: 'G05', effective_from: '2026-09-30', rationale: 'Covenant breaches.' }

describe('ratings contract (FE-20.1)', () => {
  it('the status styles cover exactly the ObligorRatingStatus enum of openapi.json', () => {
    expect([...RATING_STATUSES].sort()).toEqual([...openapi.components.schemas.ObligorRatingStatus.enum].sort())
  })

  it('the rationale limit is the ObligorRatingCreate maxLength, and grade, date and rationale are all required', () => {
    const create = openapi.components.schemas.ObligorRatingCreate
    expect(RATIONALE_MAX).toBe(create.properties.rationale.maxLength)
    expect([...create.required].sort()).toEqual(['effective_from', 'grade', 'rationale'])
  })

  it('the pill shows the server status as sent', () => {
    for (const status of RATING_STATUSES) {
      const html = renderToStaticMarkup(createElement(RatingStatusPill, { status }))
      expect(html).toContain(`data-status="${status}"`)
    }
    expect(text(renderToStaticMarkup(createElement(RatingStatusPill, { status: 'IN_REVIEW' })))).toBe('In review')
  })
})

describe('the propose form validation states (FE-20.1)', () => {
  it('empty: each of the three fields has its problem, and no body is built', () => {
    expect(proposalProblems(EMPTY_PROPOSAL, GRADES)).toEqual({
      grade: 'Choose a grade.',
      effective_from: 'An effective date is required.',
      rationale: 'A rationale is required.',
    })
    expect(proposalBody(EMPTY_PROPOSAL, GRADES)).toBeNull()
  })

  it('valid: no problems, and the body carries the grade, the date and the trimmed rationale', () => {
    expect(hasProblems(proposalProblems(valid, GRADES))).toBe(false)
    expect(proposalBody({ ...valid, rationale: '  Covenant breaches.\n' }, GRADES)).toEqual({
      grade: 'G05',
      effective_from: '2026-09-30',
      rationale: 'Covenant breaches.',
    })
  })

  it('a grade outside the offered rating_grade codes is a problem (the server answers 422 invalid_grade)', () => {
    expect(proposalProblems({ ...valid, grade: 'TERM_LOAN' }, GRADES).grade).toBe('Not a rating_grade code of your tenant.')
    expect(proposalProblems({ ...valid, grade: 'TERM_LOAN' }).grade).toBeUndefined() // no list loaded: presence only
  })

  it('the effective date must be a real YYYY-MM-DD date', () => {
    for (const bad of ['2026-02-30', '2026-13-01', '30/09/2026', '2026-9-30', '2025-02-29'])
      expect(proposalProblems({ ...valid, effective_from: bad }, GRADES).effective_from, bad).toBe('A valid date, YYYY-MM-DD.')
    expect(isIsoDate('2024-02-29')).toBe(true)
    expect(isIsoDate('2000-02-29')).toBe(true)
    expect(isIsoDate('1900-02-29')).toBe(false)
  })

  it('the rationale: whitespace only is empty; 2,000 characters after trimming is the limit', () => {
    expect(proposalProblems({ ...valid, rationale: '   \n ' }, GRADES).rationale).toBe('A rationale is required.')
    expect(proposalProblems({ ...valid, rationale: ` ${'x'.repeat(RATIONALE_MAX)} ` }, GRADES).rationale).toBeUndefined()
    expect(proposalProblems({ ...valid, rationale: 'x'.repeat(RATIONALE_MAX + 1) }, GRADES).rationale).toBe('At most 2,000 characters (2,001 now).')
    expect(rationaleCounter('')).toEqual({ text: '0/2000', over: false })
    expect(rationaleCounter(' abc ')).toEqual({ text: '3/2000', over: false })
    expect(rationaleCounter('x'.repeat(RATIONALE_MAX + 1))).toEqual({ text: '2001/2000', over: true })
  })

  const fields = (form: ProposalForm, opts: { touched?: ProposalField[]; showAll?: boolean; grades?: Parameters<typeof ProposalFields>[0]['grades'] } = {}) =>
    renderToStaticMarkup(
      createElement(ProposalFields, {
        form,
        grades: opts.grades ?? { kind: 'ready', items: VOCAB },
        touched: new Set(opts.touched ?? []),
        showAll: opts.showAll,
        onChange: () => {},
        onTouch: () => {},
      }),
    )
  const problemOf = (html: string, field: string) => {
    const m = new RegExp(`data-testid="rating-${field}-problem"[^>]*>([^<]*)<`).exec(html)
    return m ? m[1] : ''
  }

  it('rendered: untouched shows no problem; touched shows that field only; after a submit attempt all of them', () => {
    const fresh = fields(EMPTY_PROPOSAL)
    expect(['grade', 'effective-from', 'rationale'].map((f) => problemOf(fresh, f))).toEqual(['', '', ''])
    const one = fields(EMPTY_PROPOSAL, { touched: ['rationale'] })
    expect([problemOf(one, 'grade'), problemOf(one, 'rationale')]).toEqual(['', 'A rationale is required.'])
    const all = fields(EMPTY_PROPOSAL, { showAll: true })
    expect(['grade', 'effective-from', 'rationale'].map((f) => problemOf(all, f))).toEqual([
      'Choose a grade.',
      'An effective date is required.',
      'A rationale is required.',
    ])
    expect(['grade', 'effective-from', 'rationale'].map((f) => problemOf(fields(valid, { showAll: true }), f))).toEqual(['', '', ''])
  })

  it('rendered: the counter counts the trimmed rationale and turns "over" past the limit', () => {
    expect(fields({ ...valid, rationale: 'abcd' })).toMatch(/data-testid="rating-rationale-counter" data-over="false">4\/2000</)
    const over = fields({ ...valid, rationale: 'x'.repeat(RATIONALE_MAX + 5) }, { touched: ['rationale'] })
    expect(over).toMatch(/data-testid="rating-rationale-counter" data-over="true">2005\/2000</)
    expect(problemOf(over, 'rationale')).toBe('At most 2,000 characters (2,005 now).')
  })

  it('rendered: the grade picker offers only rating_grade entries, labelled, with the provisional note', () => {
    expect(gradeOptions(VOCAB).map((v) => v.code)).toEqual(GRADES)
    expect(gradeLabel(VOCAB[2])).toBe('D · Grade D · rank 9 · default grade · provisional')
    const html = fields(EMPTY_PROPOSAL)
    expect(html).toContain('data-testid="rating-grade"')
    expect((html.match(/<option /g) ?? []).length).toBe(1 + GRADES.length)
    expect(html).not.toContain('TERM_LOAN')
    expect(html).toContain('data-testid="rating-grades-provisional"')
  })

  it('rendered: the grade list loading, failed and empty states replace the picker', () => {
    expect(fields(EMPTY_PROPOSAL, { grades: { kind: 'loading' } })).toContain('data-testid="rating-grades-loading"')
    const failed = fields(EMPTY_PROPOSAL, { grades: { kind: 'failed', status: 503, cid: 'cid-1' } })
    expect(failed).toContain('data-testid="rating-grades-failed"')
    expect(failed).toContain('cid-1')
    expect(failed).not.toContain('data-testid="rating-grade"')
    expect(fields(EMPTY_PROPOSAL, { grades: { kind: 'ready', items: VOCAB.slice(3) } })).toContain('data-testid="rating-grades-empty"')
  })
})

describe('the rating history (FE-20.1)', () => {
  it('shows grade, effective date, status, proposer and decider per version, as the server sent them', () => {
    const rows = [
      rating({ rating_id: 'r2', status: 'ACTIVE', decided_by: 'dev:approver', decided_at: '2026-10-19T09:00:00Z' }),
      rating({ rating_id: 'r1', status: 'IN_REVIEW', proposer_user_id: 'someone-else', rationale: MASKED, grade: MASKED, grade_rank: null }),
    ]
    const html = renderToStaticMarkup(createElement(RatingHistory, { ratings: rows, myUserId: ME }))
    expect(html).toContain('data-count="2"')
    expect(html.match(/data-testid="rating-row"/g)).toHaveLength(2)
    expect(html).toMatch(/data-id="r2" data-status="ACTIVE"/)
    expect(html).toMatch(/data-id="r1" data-status="IN_REVIEW"/)
    expect(text(html)).toContain('dev:approver')
    expect(text(html)).toContain('2026-09-30')
    expect(text(html)).toContain('rank 5')
    expect(text(html)).toContain('You') // r2 is mine (P-1: "You", not the raw actor)
    expect((text(html).match(/You(?![a-z])/g) ?? []).length).toBe(1)
    expect(text(html)).toContain('masked') // r1's grade and rationale
    expect(text(html)).toContain('Waiting for an approver who is not the proposer.')
  })

  it('no versions: says so, and that the origination grade is never a fallback', () => {
    const html = renderToStaticMarkup(createElement(RatingHistory, { ratings: [], myUserId: ME }))
    expect(html).toContain('data-testid="no-ratings"')
    expect(text(html)).toContain('NO_APPROVED_RATING')
  })

  it('"Propose rating" is offered with rating:propose only (cosmetic; the server checks it)', () => {
    expect(canProposeRating((p) => p === 'rating:propose')).toBe(true)
    expect(canProposeRating((p) => p === 'rating:approve' || p === 'domain:read')).toBe(false)
  })
})

describe('ratings reversibility (FE-20.1, D-02)', () => {
  afterEach(() => {
    vi.unstubAllEnvs()
    vi.resetModules()
  })

  it('on: a Ratings tab (domain:read) after Counterparties, active on /domain/ratings, and the domain entry names it', async () => {
    vi.resetModules()
    const { domainSections, mainWorkspaces } = await import('@/config/navigation')
    expect(domainSections.map((s) => s.slug)).toEqual(['instruments', 'counterparties', 'ratings', 'rules', 'modules', 'assumption-sets'])
    expect(domainSections.find((s) => s.slug === 'ratings')).toEqual({ slug: 'ratings', href: '/domain/ratings', label: 'Ratings', permission: 'domain:read' })
    expect(activeSection('/domain/ratings', domainSections)).toBe('ratings')
    expect(activeSection('/domain/counterparties/abc', domainSections)).toBe('counterparties')
    expect(mainWorkspaces.find((w) => w.slug === 'domain')?.description).toMatch(/Obligor ratings with maker-checker approval\.$/)
    expect((await import('@/config/features')).RATINGS_UI_ENABLED).toBe(true)
  })

  it('NEXT_PUBLIC_RATINGS_UI=off restores the Phase 19 tabs and domain entry exactly', async () => {
    vi.stubEnv('NEXT_PUBLIC_RATINGS_UI', 'off')
    vi.resetModules()
    const { domainSections, mainWorkspaces } = await import('@/config/navigation')
    expect(domainSections.map((s) => s.slug)).toEqual(['instruments', 'counterparties', 'rules', 'modules', 'assumption-sets'])
    expect(mainWorkspaces.find((w) => w.slug === 'domain')?.description).toMatch(/Counterparties and credit exposures with their versions and lineage\.$/)
    expect((await import('@/config/features')).RATINGS_UI_ENABLED).toBe(false)
  })

  it('with the credit domain off, no Ratings tab either (ratings belong to counterparties)', async () => {
    vi.stubEnv('NEXT_PUBLIC_CREDIT_DOMAIN_UI', 'off')
    vi.resetModules()
    const { domainSections } = await import('@/config/navigation')
    expect(domainSections.map((s) => s.slug)).toEqual(['instruments', 'rules', 'modules', 'assumption-sets'])
  })

  it('the counterparty detail mounts the panel only behind RATINGS_UI_ENABLED', () => {
    const src = readFileSync(fileURLToPath(new URL('../../CounterpartyView.tsx', import.meta.url)), 'utf8')
    expect(src).toMatch(/\{RATINGS_UI_ENABLED && <RatingsPanel /)
    expect(src.match(/<RatingsPanel /g)).toHaveLength(1)
  })
})
