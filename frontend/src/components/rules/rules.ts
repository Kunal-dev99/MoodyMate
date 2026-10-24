import { api, call, type Schemas } from '@/api/client'
import { COMMENT_MAX, commentProblem } from '@/lib/forms'
import type { RuleLimits } from '@/lib/limits'
import { isAuthorOf, makerCheckerGate, type MakerCheckerGate } from '@/lib/maker-checker'
import { refusalOf, type Refusal } from '@/lib/refusal'

/*
 * FE-16.2: governed rule changes (D-32 §1, §3). Propose a DRAFT from the ACTIVE version, submit
 * it, and approve or reject it (maker-checker). Every payload type is the generated one.
 *
 * Parameters are typed data from the closed kind list (D-32 §1). The contract types
 * `parameter_schema` and `parameters` as open objects, so the editor narrows them here into its
 * own form state (text per input). DECIMAL values stay strings end to end: they are compared as
 * text, never parsed to numbers. Every check here is cosmetic and mirrors BE-16.1; the server
 * decides (422 `invalid_parameters`, D-04).
 */

export type RuleDetail = Schemas['RuleDetail']
export type RuleVersion = Schemas['RuleVersionOut']
export type RuleVersionStatus = Schemas['RuleVersionStatus']
export type RuleVersionCreate = Schemas['RuleVersionCreate']
export type RuleVersionDecision = Schemas['RuleVersionDecision']
export type Decision = Schemas['ProposalDecision']
type Parameters = RuleVersion['parameters']
type Me = Schemas['Me']
type Permission = Schemas['Permission']

/**
 * `rule_version.status` (Data Model §9 plus REJECTED, D-06, D-32 §2) on the design system's
 * status styles. The Record type makes a status the backend adds a compile error here.
 */
export const RULE_STATUS_STYLE: Record<RuleVersionStatus, { label: string; className: string; description: string }> = {
  DRAFT: { label: 'Draft', className: 'status-draft', description: 'Proposed; editable only as a new proposal. Not used by any run.' },
  IN_REVIEW: { label: 'In review', className: 'status-testing', description: 'Submitted; waiting for an approver who is not the author.' },
  APPROVED: { label: 'Approved', className: 'status-building', description: 'Reserved: an approval activates the version in the same step.' },
  ACTIVE: { label: 'Active', className: 'status-published', description: 'The version a run submitted now pins.' },
  SUPERSEDED: { label: 'Superseded', className: 'status-draft', description: 'Replaced by a later ACTIVE version. Runs that pinned it keep it.' },
  RETIRED: { label: 'Retired', className: 'status-draft', description: 'No longer used by new runs.' },
  REJECTED: { label: 'Rejected', className: 'status-failed', description: 'The approver rejected it. Never used by a run.' },
}

/**
 * The limits BE-16.1 enforces, as GET config/limits serves them (FE-35.3): the editor reads the
 * server's values (`fetchLimits().rule`) and mirrors none of them.
 */
export type { RuleLimits }
export { COMMENT_MAX }

// --- decimal text, compared without parsing --------------------------------------------------

const DECIMAL_TEXT = /^-?\d+(\.\d+)?$/
const INTEGER_TEXT = /^-?\d+$/
const BUCKET_CODE = /^[A-Za-z0-9_]+$/

export const isDecimalText = (t: string): boolean => DECIMAL_TEXT.test(t)
export const isIntegerText = (t: string): boolean => INTEGER_TEXT.test(t)

/** The number of digits after the decimal point of decimal text. */
export const scaleOf = (t: string): number => (t.includes('.') ? t.length - t.indexOf('.') - 1 : 0)

function parts(t: string): { negative: boolean; whole: string; fraction: string } {
  const negative = t.startsWith('-')
  const [w, f = ''] = (negative ? t.slice(1) : t).split('.')
  const whole = w.replace(/^0+(?=\d)/, '')
  const fraction = f.replace(/0+$/, '')
  return { negative: negative && !(whole === '0' && fraction === ''), whole, fraction }
}

function compareMagnitude(a: { whole: string; fraction: string }, b: { whole: string; fraction: string }): number {
  if (a.whole.length !== b.whole.length) return a.whole.length < b.whole.length ? -1 : 1
  if (a.whole !== b.whole) return a.whole < b.whole ? -1 : 1
  const width = a.fraction.length > b.fraction.length ? a.fraction.length : b.fraction.length
  const fa = a.fraction.padEnd(width, '0')
  const fb = b.fraction.padEnd(width, '0')
  return fa === fb ? 0 : fa < fb ? -1 : 1
}

/** Compares two decimal texts exactly (-1, 0, 1), as text: no float is ever made. */
export function compareDecimalText(a: string, b: string): number {
  const x = parts(a)
  const y = parts(b)
  if (x.negative !== y.negative) return x.negative ? -1 : 1
  const m = compareMagnitude(x, y)
  return x.negative ? -m : m
}

/** A bound from a parameter schema (a JSON number or decimal text) as text; null when absent. */
const boundText = (v: unknown): string | null =>
  typeof v === 'string' && isDecimalText(v) ? v : typeof v === 'number' && Number.isFinite(v) ? `${v}` : null

const textList = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : [])
const asObject = (v: unknown): Record<string, unknown> =>
  typeof v === 'object' && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : {}

/** A JSON integer for the request body, from text the checks accepted (INTEGER, DAY_BUCKETS). */
const toInteger = (t: string): number => JSON.parse(t.replace(/^(-?)0+(?=\d)/, '$1')) as number

// --- the editor: one form state per closed kind (D-32 §1) ------------------------------------

export interface DecimalSpec {
  min: string | null
  max: string | null
  scale: number | null
}
export interface IntegerSpec {
  min: string | null
  max: string | null
}
/** One bucket as the form holds it: text per input. `lower` is the previous bucket's `upper`. */
export interface BucketDraft {
  code: string
  lower: string
  upper: string
}

export type Editor =
  | { kind: 'DECIMAL'; spec: DecimalSpec; value: string }
  | { kind: 'INTEGER'; spec: IntegerSpec; value: string }
  | { kind: 'ENUM'; values: string[]; value: string }
  | { kind: 'DAY_BUCKETS'; unit: string | null; buckets: BucketDraft[] }
  | { kind: 'ENUM_MAP'; vocabulary: string[]; values: string[]; entries: Record<string, string> }
  | { kind: 'DECIMAL_MAP'; vocabulary: string[]; spec: DecimalSpec; entries: Record<string, string> }
  | { kind: 'UNSUPPORTED'; reason: string }

export type EditorKind = Editor['kind']

const decimalSpec = (s: Record<string, unknown>): DecimalSpec => ({
  min: boundText(s.min),
  max: boundText(s.max),
  scale: typeof s.scale === 'number' && Number.isInteger(s.scale) ? s.scale : null,
})

const textOf = (v: unknown): string => (typeof v === 'string' ? v : typeof v === 'number' ? `${v}` : '')

/** Map entries as text, the vocabulary's keys first (unset ones empty), then any other key the version holds. */
function entriesOf(map: unknown, vocabulary: string[]): Record<string, string> {
  const held = asObject(map)
  const out: Record<string, string> = {}
  for (const key of vocabulary) out[key] = textOf(held[key])
  for (const [key, value] of Object.entries(held)) if (!(key in out)) out[key] = textOf(value)
  return out
}

/** The form for a rule's parameter kind, filled from a version's parameters (the ACTIVE one, to propose a change). */
export function editorFor(rule: Pick<RuleDetail, 'parameter_schema' | 'vocabulary'>, parameters: Parameters): Editor {
  const schema = asObject(rule.parameter_schema)
  const p = asObject(parameters)
  switch (schema.kind) {
    case 'DECIMAL':
      return { kind: 'DECIMAL', spec: decimalSpec(schema), value: textOf(p.value) }
    case 'INTEGER':
      return { kind: 'INTEGER', spec: { min: boundText(schema.min), max: boundText(schema.max) }, value: textOf(p.value) }
    case 'ENUM':
      return { kind: 'ENUM', values: textList(schema.values), value: textOf(p.value) }
    case 'DAY_BUCKETS': {
      const buckets = (Array.isArray(p.buckets) ? p.buckets : []).map(asObject).map((b) => ({
        code: textOf(b.code),
        lower: textOf(b.lower_exclusive),
        upper: b.upper_inclusive === null ? '' : textOf(b.upper_inclusive),
      }))
      return { kind: 'DAY_BUCKETS', unit: typeof schema.unit === 'string' ? schema.unit : null, buckets }
    }
    case 'ENUM_MAP':
      return { kind: 'ENUM_MAP', vocabulary: rule.vocabulary, values: textList(schema.values), entries: entriesOf(p.map, rule.vocabulary) }
    case 'DECIMAL_MAP':
      return { kind: 'DECIMAL_MAP', vocabulary: rule.vocabulary, spec: decimalSpec(schema), entries: entriesOf(p.map, rule.vocabulary) }
    default:
      return { kind: 'UNSUPPORTED', reason: 'This rule declares a parameter kind outside the closed list. It cannot be edited here.' }
  }
}

// --- DAY_BUCKETS edits: contiguous by construction -------------------------------------------

/** The label BE-16.1 expects for a bucket: `(lower,upper]`, or `(lower,inf)` for the open last one. */
export const bucketLabel = (b: Pick<BucketDraft, 'lower' | 'upper'>): string => (b.upper === '' ? `(${b.lower},inf)` : `(${b.lower},${b.upper}]`)

/** Recomputes every lower bound but the first from the previous bucket's upper bound; the last stays open. */
function relink(buckets: BucketDraft[]): BucketDraft[] {
  return buckets.map((b, i) => ({
    ...b,
    lower: i === 0 ? b.lower : buckets[i - 1].upper,
    upper: i === buckets.length - 1 ? '' : b.upper,
  }))
}

export type BucketEdit =
  | { type: 'code'; index: number; value: string }
  | { type: 'first_lower'; value: string }
  | { type: 'upper'; index: number; value: string }
  | { type: 'add' }
  | { type: 'remove'; index: number }

/** A free bucket code: B01, B02, … not yet used. */
function freeCode(buckets: BucketDraft[]): string {
  const used = new Set(buckets.map((b) => b.code))
  for (let n = 1; ; n += 1) {
    const code = `B${`${n}`.padStart(2, '0')}`
    if (!used.has(code)) return code
  }
}

export function editBuckets(buckets: BucketDraft[], e: BucketEdit): BucketDraft[] {
  const next = buckets.map((b) => ({ ...b }))
  switch (e.type) {
    case 'code':
      if (next[e.index]) next[e.index].code = e.value
      return next
    case 'first_lower':
      if (next[0]) next[0].lower = e.value
      return relink(next)
    case 'upper':
      if (next[e.index] && e.index < next.length - 1) next[e.index].upper = e.value
      return relink(next)
    case 'add': {
      // A new closed bucket just before the open last one; its upper bound is to be filled in.
      const at = next.length > 0 ? next.length - 1 : 0
      next.splice(at, 0, { code: freeCode(next), lower: '', upper: '' })
      return relink(next)
    }
    case 'remove':
      if (next.length <= 1) return next
      next.splice(e.index, 1)
      return relink(next)
  }
}

// --- cosmetic checks mirroring BE-16.1 -------------------------------------------------------

function decimalProblems(label: string, value: string, spec: DecimalSpec, limits: RuleLimits): string[] {
  const t = value.trim()
  if (t === '') return [`${label}: a value is required.`]
  if (!isDecimalText(t)) return [`${label}: must be decimal text, e.g. 0.10.`]
  const out: string[] = []
  const maxScale = spec.scale ?? limits.max_decimal_scale
  if (scaleOf(t) > maxScale) out.push(`${label}: at most ${maxScale} decimal places.`)
  if (spec.min !== null && compareDecimalText(t, spec.min) < 0) out.push(`${label}: at least ${spec.min}.`)
  if (spec.max !== null && compareDecimalText(t, spec.max) > 0) out.push(`${label}: at most ${spec.max}.`)
  return out
}

function integerProblems(label: string, value: string, min: string | null, max: string | null): string[] {
  const t = value.trim()
  if (t === '') return [`${label}: a whole number is required.`]
  if (!isIntegerText(t)) return [`${label}: must be a whole number.`]
  const out: string[] = []
  if (min !== null && compareDecimalText(t, min) < 0) out.push(`${label}: at least ${min}.`)
  if (max !== null && compareDecimalText(t, max) > 0) out.push(`${label}: at most ${max}.`)
  return out
}

function bucketProblems(buckets: BucketDraft[], limits: RuleLimits): string[] {
  const out: string[] = []
  if (buckets.length === 0) return ['At least one bucket is required.']
  if (buckets.length > limits.max_buckets) out.push(`At most ${limits.max_buckets} buckets.`)
  const seen = new Set<string>()
  buckets.forEach((b, i) => {
    const name = `Bucket ${i + 1}`
    const code = b.code.trim()
    if (!code) out.push(`${name}: a code is required.`)
    else if (code.length > limits.max_bucket_code_length || !BUCKET_CODE.test(code))
      out.push(`${name}: the code is letters, digits and _ only, at most ${limits.max_bucket_code_length}.`)
    else if (seen.has(code)) out.push(`${name}: the code ${code} is used twice.`)
    seen.add(code)
    const last = i === buckets.length - 1
    if (i === 0) out.push(...integerProblems(`${name} lower bound`, b.lower, '0', `${limits.max_days}`))
    if (last) {
      if (b.upper.trim() !== '') out.push(`${name}: the last bucket is open (no upper bound).`)
      return
    }
    const upper = integerProblems(`${name} upper bound`, b.upper, '0', `${limits.max_days}`)
    out.push(...upper)
    if (upper.length === 0 && isIntegerText(b.lower.trim()) && compareDecimalText(b.upper.trim(), b.lower.trim()) <= 0)
      out.push(`${name}: the upper bound must be greater than the lower bound.`)
    if (i > 0 && b.lower !== buckets[i - 1].upper) out.push(`${name}: must start where bucket ${i} ends.`)
  })
  return out
}

function mapProblems(
  entries: Record<string, string>,
  vocabulary: string[],
  check: (key: string, value: string) => string[],
  limits: RuleLimits,
): string[] {
  const set = Object.entries(entries).filter(([, v]) => v.trim() !== '')
  const out: string[] = []
  if (set.length === 0) out.push('At least one entry is required.')
  if (set.length > limits.max_map_entries) out.push(`At most ${limits.max_map_entries} entries.`)
  for (const [key, value] of set) {
    if (vocabulary.length > 0 && !vocabulary.includes(key)) out.push(`${key}: not in the key vocabulary.`)
    out.push(...check(key, value.trim()))
  }
  return out
}

/**
 * Why the form does not fit its kind (empty: it may be proposed), against the served `limits`.
 * Cosmetic: BE-16.1 decides.
 */
export function editorProblems(e: Editor, limits: RuleLimits): string[] {
  switch (e.kind) {
    case 'DECIMAL':
      return decimalProblems('Value', e.value, e.spec, limits)
    case 'INTEGER':
      return integerProblems('Value', e.value, e.spec.min, e.spec.max)
    case 'ENUM':
      return e.values.includes(e.value) ? [] : ['Pick one of the allowed values.']
    case 'DAY_BUCKETS':
      return bucketProblems(e.buckets, limits)
    case 'ENUM_MAP':
      return mapProblems(e.entries, e.vocabulary, (key, value) => (e.values.includes(value) ? [] : [`${key}: not an allowed value.`]), limits)
    case 'DECIMAL_MAP':
      return mapProblems(e.entries, e.vocabulary, (key, value) => decimalProblems(key, value, e.spec, limits), limits)
    case 'UNSUPPORTED':
      return [e.reason]
  }
}

// --- the request ------------------------------------------------------------------------------

const setEntries = (entries: Record<string, string>): [string, string][] =>
  Object.entries(entries)
    .filter(([, v]) => v.trim() !== '')
    .map(([k, v]) => [k, v.trim()])

/** The full parameters the form describes, over the base version's (keys the form does not edit are kept). */
export function parametersOf(e: Editor, base: Parameters, limits: RuleLimits): Parameters | null {
  if (editorProblems(e, limits).length > 0) return null
  const p: Parameters = { ...asObject(base) }
  switch (e.kind) {
    case 'DECIMAL':
      return { ...p, value: e.value.trim() } // decimal text, never a JSON number
    case 'INTEGER':
      return { ...p, value: toInteger(e.value.trim()) }
    case 'ENUM':
      return { ...p, value: e.value }
    case 'DAY_BUCKETS':
      return {
        ...p,
        buckets: e.buckets.map((b) => ({
          code: b.code.trim(),
          label: bucketLabel(b),
          lower_exclusive: toInteger(b.lower.trim()),
          upper_inclusive: b.upper === '' ? null : toInteger(b.upper.trim()),
        })),
      }
    case 'ENUM_MAP':
    case 'DECIMAL_MAP':
      return { ...p, map: Object.fromEntries(setEntries(e.entries)) } // DECIMAL_MAP values stay text
    case 'UNSUPPORTED':
      return null
  }
}

const isPlainObject = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)

/** Canonical JSON (sorted keys): equal values give equal text. */
export function canonical(v: unknown): string {
  if (Array.isArray(v)) return `[${v.map(canonical).join(',')}]`
  if (isPlainObject(v))
    return `{${Object.keys(v)
      .sort()
      .map((k) => `${JSON.stringify(k)}:${canonical(v[k])}`)
      .join(',')}}`
  return JSON.stringify(v)
}

/**
 * The JSON merge patch (RFC 7386) from `base` to `next`, as `parameters_patch` takes it: objects
 * merge key by key, a removed key is `null`, anything else (e.g. the `buckets` list) replaces.
 * Empty when nothing changed (the server would answer 422 `no_change`).
 */
export function mergePatch(base: Record<string, unknown>, next: Record<string, unknown>): Record<string, unknown> {
  const patch: Record<string, unknown> = {}
  for (const key of Object.keys(base)) if (!(key in next)) patch[key] = null
  for (const [key, value] of Object.entries(next)) {
    const old = base[key]
    if (canonical(old) === canonical(value)) continue
    patch[key] = isPlainObject(old) && isPlainObject(value) ? mergePatch(old, value) : value
  }
  return patch
}

/** The POST /rules/{id}/versions body; null while the form or the summary is not ready, or nothing changed. */
export function proposalBody(e: Editor, base: Parameters, changeSummary: string, limits: RuleLimits): RuleVersionCreate | null {
  const next = parametersOf(e, base, limits)
  const summary = changeSummary.trim()
  if (!next || !summary || summary.length > COMMENT_MAX) return null
  const parameters_patch = mergePatch(asObject(base), asObject(next))
  return Object.keys(parameters_patch).length === 0 ? null : { parameters_patch, change_summary: summary }
}

/** Why "Propose" is disabled (empty: it may be clicked). Cosmetic: the server decides. */
export function proposalProblems(e: Editor, base: Parameters, changeSummary: string, limits: RuleLimits): string[] {
  const out = editorProblems(e, limits)
  if (out.length === 0) {
    const next = parametersOf(e, base, limits)
    if (next && Object.keys(mergePatch(asObject(base), asObject(next))).length === 0)
      out.push('Nothing changed from the ACTIVE version (the server refuses it: no_change).')
  }
  const summary = changeSummary.trim()
  if (!summary) out.push('A change summary is required.')
  else if (summary.length > COMMENT_MAX) out.push(`The change summary is at most ${COMMENT_MAX} characters.`)
  return out
}

// --- who may do what (cosmetic, D-04: the server decides) ------------------------------------

export const activeVersionOf = (rule: Pick<RuleDetail, 'versions'>): RuleVersion | undefined => rule.versions.find((v) => v.status === 'ACTIVE')

/** Versions still in the maker-checker flow: DRAFT and IN_REVIEW, newest first. */
export const openVersions = (rule: Pick<RuleDetail, 'versions'>): RuleVersion[] =>
  rule.versions.filter((v) => v.status === 'DRAFT' || v.status === 'IN_REVIEW').sort((a, b) => b.version - a.version)

/** The signed-in user authored this version (a seeded version has no author user). */
export const isAuthor = (v: Pick<RuleVersion, 'author_user_id'>, me: Pick<Me, 'user_id'>): boolean => isAuthorOf(v.author_user_id, me)

type Has = (permission: Permission) => boolean

export function canPropose(rule: Pick<RuleDetail, 'change_enabled' | 'versions'>, has: Has): boolean {
  return has('rule:propose') && rule.change_enabled && activeVersionOf(rule) !== undefined
}

export function canSubmit(rule: Pick<RuleDetail, 'change_enabled'>, v: Pick<RuleVersion, 'status'>, has: Has): boolean {
  return has('rule:propose') && rule.change_enabled && v.status === 'DRAFT'
}

/**
 * Whether the Approve and Reject buttons show for an IN_REVIEW version: never to its author
 * (self-approval is not allowed, 409 `self_approval`), and only with `rule:approve` while rule
 * changes are switched on.
 */
export type DecisionGate = MakerCheckerGate

export const decisionGate = (rule: Pick<RuleDetail, 'change_enabled'>, v: Pick<RuleVersion, 'status' | 'author_user_id'>, me: Pick<Me, 'user_id'>, has: Has): DecisionGate =>
  makerCheckerGate({ inReview: v.status === 'IN_REVIEW', authorUserId: v.author_user_id, me, mayApprove: has('rule:approve'), enabled: rule.change_enabled })

/** Why a decision comment cannot be sent yet, or null: the shared check (FE-29.1). */
export { commentProblem }

export const proposeHref = (ruleId: string): string => `/domain/rules/${encodeURIComponent(ruleId)}/propose`
export const ruleHref = (ruleId: string): string => `/domain/rules/${encodeURIComponent(ruleId)}`

// --- requests and refusals --------------------------------------------------------------------

/** The rule refusals BE-16.2 names, with a title each. */
export const RULE_REFUSAL_TITLES: Record<string, string> = {
  rule_change_disabled: 'Rule changes are switched off',
  invalid_parameters: 'The parameters do not fit the rule',
  invalid_parameter_schema: "The rule's parameter schema is not valid",
  no_change: 'Nothing changed from the ACTIVE version',
  no_active_version: 'The rule has no ACTIVE version to copy',
  invalid_status: 'The version is not in a state that allows this',
  self_approval: 'You cannot decide on your own version',
  stale_version: 'Another version was activated since this one was proposed',
  forbidden: 'You may not do this',
  not_found: 'Rule or version not found',
  validation_error: 'The request was rejected',
}

export const ruleRefusal = (err: unknown, fallback: string): Refusal => refusalOf(err, fallback, RULE_REFUSAL_TITLES)

export const fetchRule = (ruleId: string) => (signal: AbortSignal) =>
  call(() => api.GET('/api/v1/rules/{rule_id}', { params: { path: { rule_id: ruleId } }, signal }))

export const proposeVersion = (ruleId: string, body: RuleVersionCreate) =>
  call(() => api.POST('/api/v1/rules/{rule_id}/versions', { params: { path: { rule_id: ruleId } }, body }))

export const submitVersion = (ruleId: string, version: number) =>
  call(() => api.POST('/api/v1/rules/{rule_id}/versions/{version}/submit', { params: { path: { rule_id: ruleId, version } }, body: {} }))

export const decideVersion = (ruleId: string, version: number, body: RuleVersionDecision) =>
  call(() => api.POST('/api/v1/rules/{rule_id}/versions/{version}/approval', { params: { path: { rule_id: ruleId, version } }, body }))
