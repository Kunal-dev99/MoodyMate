import { ApiError, api, call, type Schemas } from '@/api/client'
import type { operations } from '@/api/schema'
import type { Refusal } from '@/components/ingestion/mappings'

/*
 * FE-24.2: the Agentic Stress-Test Explorer's pure helpers (D-44, D-47). Every payload type is
 * the generated one (src/api/schema.d.ts). Each gate here is cosmetic: the server checks
 * `scenario:propose` / `scenario:approve` on every write (403) and refuses a non-author
 * (409 `not_author`), self-approval (409 `self_approval`) and a wrong status (409
 * `invalid_status`) itself (D-04). The UI never moves a status; it shows what the server sent.
 */

export type ScenarioPage = Schemas['ScenarioPage']
export type ScenarioSummary = Schemas['ScenarioSummary']
export type ScenarioDetail = Schemas['ScenarioDetail']
export type ScenarioVersion = Schemas['ScenarioVersionOut']
export type ScenarioVersionStatus = Schemas['ScenarioVersionStatus']
export type ScenarioCreate = Schemas['ScenarioCreate']
export type ScenarioVersionCreate = Schemas['ScenarioVersionCreate']
export type ScenarioVersionUpdate = Schemas['ScenarioVersionUpdate']
export type ScenarioVersionConfirm = Schemas['ScenarioVersionConfirm']
export type ScenarioVersionSubmit = Schemas['ScenarioVersionSubmit']
export type ScenarioVersionDecision = Schemas['ScenarioVersionDecision']
export type Shock = ScenarioCreate['shocks'][number]
export type TemplatePage = Schemas['TemplatePage']
export type TemplateOut = Schemas['TemplateOut']
export type TemplateField = Schemas['TemplateField']
export type ScenarioTemplate = Schemas['ScenarioTemplate']
export type Decision = Schemas['ProposalDecision']
export type ScenarioQuery = NonNullable<operations['listScenarios']['parameters']['query']>
export type InstrumentPage = Schemas['InstrumentPage']
type Me = Schemas['Me']
type Has = (permission: Schemas['Permission']) => boolean

export const explorerHref = '/advanced/stress-explorer'
export const newScenarioHref = `${explorerHref}/new`
export const scenarioHref = (scenarioId: string) => `${explorerHref}/${scenarioId}`
export const editVersionHref = (scenarioId: string, version: number) => `${scenarioHref(scenarioId)}/versions/${version}/edit`
export const newVersionHref = (scenarioId: string, parent: number) => `${scenarioHref(scenarioId)}/versions/new?parent=${parent}`

export const PAGE_SIZE = 50
/** ScenarioVersionDecision / ScenarioVersionSubmit `comment` maxLength (a test reads openapi.json). */
export const COMMENT_MAX = 1000
/** ScenarioCreate `name` / `description` and `change_summary` maxLength. */
export const NAME_MAX = 200
export const DESCRIPTION_MAX = 2000
export const SUMMARY_MAX = 1000

// --- status pill ---------------------------------------------------------------------------------

/**
 * `scenario_version.status` (D-44 §5: Data Model §9 plus CONFIRMED_FOR_EXPLORATION), in the
 * backend's own set. A confirmed version is an exploration, never an approved scenario.
 */
export const SCENARIO_STATUS_STYLE: Record<ScenarioVersionStatus, { label: string; className: string; description: string }> = {
  DRAFT: { label: 'Draft', className: 'status-draft', description: 'Editable by its author only. Not runnable.' },
  CONFIRMED_FOR_EXPLORATION: {
    label: 'Confirmed for exploration',
    className: 'status-building',
    description: 'The author confirmed it for exploration runs only: "Exploration, not approved". Never authoritative, never in the shared library.',
  },
  IN_REVIEW: { label: 'In review', className: 'status-testing', description: 'Submitted for promotion; waiting for an approver (scenario:approve) who is not the author.' },
  APPROVED_FOR_TEST: {
    label: 'Approved for test',
    className: 'status-published',
    description: 'Promoted by an approver who is not the author: a shared, approved scenario (illustrative).',
  },
  RUNNING: { label: 'Running (reserved)', className: 'status-building', description: 'Reserved: runs are synchronous, so no version is ever left RUNNING.' },
  TESTED: { label: 'Tested (reserved)', className: 'status-draft', description: 'Reserved: not used while runs are synchronous.' },
  REJECTED: { label: 'Rejected', className: 'status-failed', description: 'The approver rejected it. Not runnable; its author may copy it into a new version. Kept, never deleted.' },
  CLOSED: { label: 'Closed', className: 'status-draft', description: 'Hidden from pickers; still readable by id. Kept, never deleted.' },
}
export const SCENARIO_STATUSES = Object.keys(SCENARIO_STATUS_STYLE) as ScenarioVersionStatus[]

// --- library tabs ---------------------------------------------------------------------------------

/** "Shared library" (APPROVED_FOR_TEST), "My explorations" and, for scenario:approve, "Review". */
export type ExplorerTab = 'shared' | 'mine' | 'review'
export const TAB_LABELS: Record<ExplorerTab, string> = { shared: 'Shared library', mine: 'My explorations', review: 'Review' }

export function tabsFor(has: Has): ExplorerTab[] {
  return has('scenario:approve') ? ['shared', 'mine', 'review'] : ['shared', 'mine']
}

/** A `?tab=` value as a tab this user is offered; anything else is the shared library. */
export function tabOf(raw: string | null, has: Has): ExplorerTab {
  return (tabsFor(has) as string[]).includes(raw ?? '') ? (raw as ExplorerTab) : 'shared'
}

/** "My explorations": every status an author's versions live in; CLOSED only on request. */
export const MINE_STATUSES: ScenarioVersionStatus[] = ['DRAFT', 'CONFIRMED_FOR_EXPLORATION', 'IN_REVIEW', 'APPROVED_FOR_TEST', 'REJECTED']

/**
 * The GET /scenarios query of a tab. The shared library sends no status, so the server's default
 * applies (APPROVED_FOR_TEST only: never an exploration version, never a CLOSED one).
 */
export function libraryQuery(tab: ExplorerTab, offset: number, showClosed = false): ScenarioQuery {
  const query: ScenarioQuery = { limit: PAGE_SIZE, offset }
  if (tab === 'mine') {
    query.mine = true
    query.status = showClosed ? [...MINE_STATUSES, 'CLOSED'] : [...MINE_STATUSES]
  }
  if (tab === 'review') query.status = ['IN_REVIEW']
  return query
}

// --- version gates --------------------------------------------------------------------------------

export const isAuthor = (v: Pick<ScenarioVersion, 'author_user_id'>, me: Pick<Me, 'user_id'>) => v.author_user_id === me.user_id

type Gated = Pick<ScenarioVersion, 'status' | 'author_user_id'>

/** Edit and "Confirm for exploration": the author's own DRAFT (scenario:propose). */
export const canEdit = (v: Gated, me: Pick<Me, 'user_id'>, has: Has) => v.status === 'DRAFT' && isAuthor(v, me) && has('scenario:propose')
export const canConfirm = canEdit

/** "Submit for approval": the author's DRAFT or CONFIRMED_FOR_EXPLORATION version. */
export const canSubmit = (v: Gated, me: Pick<Me, 'user_id'>, has: Has) =>
  (v.status === 'DRAFT' || v.status === 'CONFIRMED_FOR_EXPLORATION') && isAuthor(v, me) && has('scenario:propose')

/** A new DRAFT adapting any version (design §6.4, e.g. "what if 30 days?"). */
export const canAdapt = (has: Has) => has('scenario:propose')

/**
 * Whether Approve and Reject are offered. The author never is (409 `self_approval`); without
 * `scenario:approve` they are hidden (403). The server checks both again.
 */
export type DecisionGate = 'allowed' | 'not_in_review' | 'self_approval' | 'no_permission'

export function decisionGate(v: Gated, me: Pick<Me, 'user_id'>, has: Has): DecisionGate {
  if (v.status !== 'IN_REVIEW') return 'not_in_review'
  if (isAuthor(v, me)) return 'self_approval'
  if (!has('scenario:approve')) return 'no_permission'
  return 'allowed'
}

/** The PROPOSED_DEFAULT parameter paths an AI_PROPOSED version needs acknowledged (D-44 §6). */
export function proposedDefaults(v: Pick<ScenarioVersion, 'parameter_sources'>): string[] {
  return Object.entries(v.parameter_sources ?? {})
    .filter(([, source]) => source === 'PROPOSED_DEFAULT')
    .map(([path]) => path)
    .sort()
}

/** Whether confirming or submitting needs the AI-origin acknowledgement (422 `ai_review_required`). */
export const needsAiReview = (v: Pick<ScenarioVersion, 'origin'>) => v.origin === 'AI_PROPOSED'

/** The confirm / submit acknowledgement fields: sent only for an AI_PROPOSED version. */
export function acknowledgement(v: Pick<ScenarioVersion, 'origin' | 'parameter_sources'>, aiOrigin: boolean, defaults: string[]): ScenarioVersionConfirm {
  if (!needsAiReview(v)) return { acknowledge_ai_origin: false }
  return { acknowledge_ai_origin: aiOrigin, acknowledged_defaults: [...defaults].sort() }
}

/** Whether every acknowledgement an AI_PROPOSED version needs is ticked. */
export function acknowledged(v: Pick<ScenarioVersion, 'origin' | 'parameter_sources'>, aiOrigin: boolean, defaults: string[]): boolean {
  if (!needsAiReview(v)) return true
  return aiOrigin && proposedDefaults(v).every((p) => defaults.includes(p))
}

// --- comments -------------------------------------------------------------------------------------

/** Why a decision comment cannot be sent yet, or null (1 to 1,000 characters, D-44 §6). */
export function commentProblem(text: string, required = true): string | null {
  const t = text.trim()
  if (!t) return required ? 'A comment is required.' : null
  if (t.length > COMMENT_MAX) return `At most ${COMMENT_MAX.toLocaleString('en')} characters (${t.length.toLocaleString('en')} now).`
  return null
}

/** The decision body, or null while the comment has a problem. */
export function decisionBody(decision: Decision, text: string): ScenarioVersionDecision | null {
  return commentProblem(text) ? null : { decision, comment: text.trim() }
}

// --- refusals -------------------------------------------------------------------------------------

const REFUSAL_TITLES: Record<string, string> = {
  forbidden: 'Not allowed',
  not_found: 'Scenario not found',
  not_author: 'Only the author can do this',
  self_approval: 'Self-approval is not allowed',
  invalid_status: 'The version is not in a state that allows this',
  scenarios_disabled: 'Scenario changes are switched off',
  invalid_shock: 'A shock was refused',
  duplicate_code: 'That scenario code is taken',
  ai_review_required: 'Acknowledge the AI origin first',
  unacknowledged_defaults: 'Acknowledge every proposed default first',
  validation_error: 'The request was refused',
}

/** A refused scenario request, as the server sent it (status, code, correlation ID). */
export function scenarioRefusal(err: unknown, fallback: string): Refusal {
  if (err instanceof ApiError)
    return { title: REFUSAL_TITLES[err.envelope.code] ?? fallback, status: err.status, code: err.envelope.code, message: err.envelope.message, cid: err.correlationId }
  return { title: fallback, status: null, code: 'unexpected_error', message: 'Something went wrong.', cid: null }
}

const PROBLEM_TEXT: Record<string, string> = {
  not_a_list: 'not a list',
  too_few_shocks: 'at least one shock is needed',
  too_many_shocks: 'too many shocks',
  not_an_object: 'not an object',
  unknown_field: 'an unknown field',
  required: 'required',
  unknown_template: 'an unknown template',
  inexact_number: 'not an exact number (a JSON fraction or an exponent)',
  not_decimal: 'not plain decimal text',
  too_many_places: 'more than 4 decimal places (never rounded)',
  not_integer: 'not a whole number',
  not_text: 'not text',
  out_of_range: 'out of range',
  unsupported_value: 'not an allowed value',
  unknown_instrument_type: 'not an instrument type of the ACTIVE cashflow_direction rule',
  direction_mismatch: "the instrument type's direction does not fit the template",
  invalid_window: 'the due window ends before it starts',
  too_many_items: 'too many items',
  duplicate_item: 'a duplicate item',
  unknown_instrument: 'an instrument that is unknown or not visible to you',
}

/** One line of a 422 `invalid_shock` refusal: which shock (null: the list) and what is wrong. */
export interface ProblemLine {
  shock: number | null
  text: string
}

/**
 * The `problems` of a 422 `invalid_shock` details object (`{"problems": [{shock_index, field,
 * code}]}`, D-44 §4); an empty list for any other shape, so an unexpected body shows no lines.
 */
function shockProblemItems(details: unknown): Record<string, unknown>[] {
  if (typeof details !== 'object' || details === null || Array.isArray(details)) return []
  const problems = (details as { problems?: unknown }).problems
  if (!Array.isArray(problems)) return []
  return problems.filter((d): d is Record<string, unknown> => typeof d === 'object' && d !== null && !Array.isArray(d))
}

/** The value-free details of a 422 `invalid_shock`, as lines (D-44 §4); empty for anything else. */
export function shockProblemLines(err: unknown): ProblemLine[] {
  if (!(err instanceof ApiError) || err.envelope.code !== 'invalid_shock') return []
  return shockProblemItems(err.envelope.details).map((d) => {
    const index = d['shock_index']
    const field = typeof d['field'] === 'string' ? d['field'] : ''
    const code = typeof d['code'] === 'string' ? d['code'] : 'refused'
    const shock = typeof index === 'number' ? index : null
    const where = shock === null ? 'The shock list' : `Shock ${shock + 1}${field ? ` · ${field}` : ''}`
    return { shock, text: `${where}: ${PROBLEM_TEXT[code] ?? code} (${code})` }
  })
}

// --- shock text -----------------------------------------------------------------------------------

/**
 * Exact fraction text ("0.2000") as percent text ("20"), by moving the decimal point: never via a
 * binary float. Text that is not plain decimal is returned as it came.
 */
export function fractionToPercent(fraction: string): string {
  const m = /^(\d+)(?:\.(\d{1,4}))?$/.exec(fraction)
  if (!m) return fraction
  const units = (m[1] + (m[2] ?? '').padEnd(4, '0')).replace(/^0+(?=\d)/, '')
  const padded = units.padStart(3, '0')
  const whole = padded.slice(0, -2).replace(/^0+(?=\d)/, '')
  const part = padded.slice(-2).replace(/0+$/, '')
  return part ? `${whole}.${part}` : whole
}

const pct = (fraction: unknown) => (typeof fraction === 'string' ? `${fractionToPercent(fraction)}%` : '?')

/** One shock in words, from its own fields (the server's exact text, shown as percent). */
export function describeShock(shock: Shock): string {
  if (shock.template === 'DELAYED_INFLOWS') {
    const p = shock.parameters
    return `Delayed inflows: ${pct(p.affected_fraction)} of ${p.cashflow_type} inflows move ${p.delay_days} calendar days later`
  }
  if (shock.template === 'DEPOSIT_WITHDRAWAL') {
    const p = shock.parameters
    return `Deposit withdrawal: ${p.cashflow_type} outflows +${pct(p.increase_fraction)}`
  }
  const p = shock.parameters
  const keys = shock.selector.instrument_keys?.length ? ` (${shock.selector.instrument_keys.length} picked instrument${shock.selector.instrument_keys.length === 1 ? '' : 's'})` : ''
  return `Funding unavailable: ${pct(p.affected_fraction)} of ${shock.selector.instrument_type}${keys} inflows due within ${p.unavailable_days} days move to day ${p.unavailable_days + 1}`
}

/** The optional selector of a shock in words, or null when it selects every matching flow. */
export function describeSelector(shock: Shock): string | null {
  const s = shock.selector
  if (!s) return null
  const parts: string[] = []
  if (s.currency) parts.push(`currency ${s.currency}`)
  if (s.origin) parts.push(`${s.origin.toLowerCase()} flows`)
  if (s.due_from_day !== undefined && s.due_from_day !== null) parts.push(`due from day ${s.due_from_day}`)
  if (s.due_to_day !== undefined && s.due_to_day !== null) parts.push(`due to day ${s.due_to_day}`)
  if (shock.template === 'FUNDING_UNAVAILABLE' && shock.selector.source_system_code) parts.push(`source ${shock.selector.source_system_code}`)
  return parts.length ? parts.join(' · ') : null
}

// --- requests -------------------------------------------------------------------------------------

export const fetchTemplates = () => (signal: AbortSignal) => call(() => api.GET('/api/v1/scenario-templates', { signal }))

export const fetchScenarios = (query: ScenarioQuery) => (signal: AbortSignal) => call(() => api.GET('/api/v1/scenarios', { params: { query }, signal }))

export const fetchScenario = (scenarioId: string) => (signal: AbortSignal) =>
  call(() => api.GET('/api/v1/scenarios/{scenario_id}', { params: { path: { scenario_id: scenarioId } }, signal }))

/** The instrument picker of FUNDING_UNAVAILABLE: instruments of one type (`domain:read`). */
export const fetchInstruments = (instrumentType: string, offset: number) => (signal: AbortSignal) =>
  call(() => api.GET('/api/v1/instruments', { params: { query: { instrument_type: instrumentType, limit: PAGE_SIZE, offset } }, signal }))

export const createScenario = (body: ScenarioCreate) => call(() => api.POST('/api/v1/scenarios', { body }))

export const createVersion = (scenarioId: string, body: ScenarioVersionCreate) =>
  call(() => api.POST('/api/v1/scenarios/{scenario_id}/versions', { params: { path: { scenario_id: scenarioId } }, body }))

export const updateVersion = (scenarioId: string, version: number, body: ScenarioVersionUpdate) =>
  call(() => api.PUT('/api/v1/scenarios/{scenario_id}/versions/{version}', { params: { path: { scenario_id: scenarioId, version } }, body }))

export const confirmVersion = (scenarioId: string, version: number, body: ScenarioVersionConfirm) =>
  call(() => api.POST('/api/v1/scenarios/{scenario_id}/versions/{version}/confirm', { params: { path: { scenario_id: scenarioId, version } }, body }))

export const submitVersion = (scenarioId: string, version: number, body: ScenarioVersionSubmit) =>
  call(() => api.POST('/api/v1/scenarios/{scenario_id}/versions/{version}/submit', { params: { path: { scenario_id: scenarioId, version } }, body }))

export const decideVersion = (scenarioId: string, version: number, body: ScenarioVersionDecision) =>
  call(() => api.POST('/api/v1/scenarios/{scenario_id}/versions/{version}/approval', { params: { path: { scenario_id: scenarioId, version } }, body }))
