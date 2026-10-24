import { api, call, type Schemas } from '@/api/client'
import { dateProblem } from '@/lib/forms'
import { isAuthorOf } from '@/lib/maker-checker'
import { refusalOf, type Refusal } from '@/lib/refusal'

/*
 * FE-20.1: the Ratings panel's pure helpers (D-39). Every payload type comes from the generated
 * API schema. Grades are human judgements: nothing here suggests or derives one (D-39 §7). Dates
 * are ISO text from the server, shown as that text.
 */

export type ObligorRating = Schemas['ObligorRatingOut']
export type ObligorRatingStatus = Schemas['ObligorRatingStatus']
export type ObligorRatingCreate = Schemas['ObligorRatingCreate']
export type VocabularyEntry = Schemas['CreditVocabularyEntryOut']

/** The limit BE-20.2 enforces (ObligorRatingCreate.rationale maxLength; a test reads openapi.json). */
export const RATIONALE_MAX = 2000

/**
 * `obligor_rating.status` (D-39 §2) as the server sent it, with a pill style each. Record: kept
 * equal to the OpenAPI enum (a test compares the keys). The UI never moves a status itself.
 */
export const RATING_STATUS_STYLE: Record<ObligorRatingStatus, { label: string; className: string; description: string }> = {
  IN_REVIEW: { label: 'In review', className: 'status-testing', description: 'Proposed; waiting for an approver (rating:approve) who is not the proposer.' },
  ACTIVE: { label: 'Active', className: 'status-published', description: 'Approved: governs from its effective date until a later ACTIVE effective date.' },
  REJECTED: { label: 'Rejected', className: 'status-failed', description: 'The approver rejected it. It never governs a date. Kept, never deleted.' },
  SUPERSEDED: {
    label: 'Superseded',
    className: 'status-draft',
    description: 'Replaced by a later approval for the same effective date. Runs that pinned it keep it.',
  },
  RETIRED: { label: 'Retired', className: 'status-draft', description: 'Reserved: withdrawal is deferred.' },
}
export const RATING_STATUSES = Object.keys(RATING_STATUS_STYLE) as ObligorRatingStatus[]

/** The `rating_grade` entries of the credit vocabularies, in the server's order (rank, then code). */
export const gradeOptions = (items: readonly VocabularyEntry[]): VocabularyEntry[] => items.filter((i) => i.vocabulary === 'rating_grade')

/** A grade option's text: code, name, rank, and the default and provisional markers. */
export function gradeLabel(e: VocabularyEntry): string {
  const parts = [`${e.code} · ${e.name}`]
  if (e.rank !== null) parts.push(`rank ${e.rank}`)
  if (e.is_default_grade) parts.push('default grade')
  if (e.provisional) parts.push('provisional')
  return parts.join(' · ')
}

// --- the propose form ---------------------------------------------------------------------------

/** The form's text per field of the generated ObligorRatingCreate (empty while unset); never a hand-written payload. */
export type ProposalForm = Record<keyof ObligorRatingCreate, string>
export const EMPTY_PROPOSAL: ProposalForm = { grade: '', effective_from: '', rationale: '' }

export type ProposalField = keyof ProposalForm
export type ProposalProblems = Partial<Record<ProposalField, string>>

/** A real calendar date written YYYY-MM-DD: the shared check (FE-29.1). */
export { isIsoDate } from '@/lib/forms'

/** The rationale's length as the server counts it (it strips surrounding whitespace). */
export const rationaleLength = (rationale: string): number => rationale.trim().length

/**
 * Why each field cannot be sent yet (empty: the form may be submitted). Cosmetic: the server
 * validates again (422 `invalid_grade`, `validation_error`) and decides (D-04).
 * `grades` are the codes offered; with none loaded, the grade is only checked for presence.
 */
export function proposalProblems(f: ProposalForm, grades: readonly string[] = []): ProposalProblems {
  const out: ProposalProblems = {}
  if (!f.grade) out.grade = 'Choose a grade.'
  else if (grades.length > 0 && !grades.includes(f.grade)) out.grade = 'Not a rating_grade code of your tenant.'
  const date = dateProblem(f.effective_from, { missing: 'An effective date is required.' })
  if (date) out.effective_from = date
  const n = rationaleLength(f.rationale)
  if (n === 0) out.rationale = 'A rationale is required.'
  else if (n > RATIONALE_MAX) out.rationale = `At most ${RATIONALE_MAX.toLocaleString('en')} characters (${n.toLocaleString('en')} now).`
  return out
}

export const hasProblems = (p: ProposalProblems): boolean => Object.keys(p).length > 0

/** The request body, or null while any field has a problem. */
export function proposalBody(f: ProposalForm, grades: readonly string[] = []): ObligorRatingCreate | null {
  if (hasProblems(proposalProblems(f, grades))) return null
  return { grade: f.grade, effective_from: f.effective_from, rationale: f.rationale.trim() }
}

/** The character counter's text and tone: `n/2000`, "over" past the limit. */
export function rationaleCounter(rationale: string): { text: string; over: boolean } {
  const n = rationaleLength(rationale)
  return { text: `${n}/${RATIONALE_MAX}`, over: n > RATIONALE_MAX }
}

// --- who may do what (cosmetic, D-04: the server decides) ---------------------------------------

type Has = (permission: Schemas['Permission']) => boolean

/** The "Propose rating" button shows with rating:propose only; the server checks it on POST. */
export const canProposeRating = (has: Has): boolean => has('rating:propose')

/** The signed-in user proposed this version. */
export const isProposer = (r: Pick<ObligorRating, 'proposer_user_id'>, me: { user_id: string }): boolean => isAuthorOf(r.proposer_user_id, me)

// --- requests and refusals ----------------------------------------------------------------------

/** The rating refusals BE-20.2 names (D-39 §3), with a title each. */
export const RATING_REFUSAL_TITLES: Record<string, string> = {
  rating_change_disabled: 'Rating changes are switched off',
  invalid_grade: 'The grade is not in your rating vocabulary',
  invalid_status: 'The rating is not in a state that allows this',
  self_approval: 'You cannot decide on your own proposal',
  stale_version: 'Another rating was approved for this date since this proposal',
  forbidden: 'You may not do this',
  not_found: 'Counterparty not found',
  validation_error: 'The request was rejected',
}

export const ratingRefusal = (err: unknown, fallback: string): Refusal => refusalOf(err, fallback, RATING_REFUSAL_TITLES)

export const fetchCreditVocabularies = (signal: AbortSignal) => call(() => api.GET('/api/v1/credit-vocabularies', { signal }))

export const proposeRating = (counterpartyId: string, body: ObligorRatingCreate) =>
  call(() => api.POST('/api/v1/counterparties/{counterparty_id}/ratings', { params: { path: { counterparty_id: counterpartyId } }, body }))
