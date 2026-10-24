import { api, call, callWithResponse, type Schemas } from '@/api/client'
import { hasResults, type RunDetail, type RunPage, type RunSummary } from '@/components/calculations/calculations'
import { newIdempotencyKey } from '@/lib/idempotency'
import { refusalOf, type Refusal } from '@/lib/refusal'
import type { ScenarioVersion, ScenarioVersionStatus } from './scenarios'

/*
 * FE-25.1: the "Run against baseline" panel's pure helpers (D-45). Every payload type is the
 * generated one (src/api/schema.d.ts). Each gate here is cosmetic: the server checks `calc:run`
 * (403), the version's status (409 `scenario_not_runnable`), the baseline (409
 * `invalid_baseline` / `baseline_not_reproducible`), the matches (409 `no_matching_cashflows`)
 * and its switches (409 `scenarios_disabled` / `calculations_disabled`) itself, audited (D-04).
 * The UI never decides that a run succeeded: it shows each member run's status as the server
 * returned it.
 */

export type ScenarioRunOut = Schemas['ScenarioRunOut']
export type ScenarioRunCreate = Schemas['ScenarioRunCreate']
export type ScenarioRunMember = Schemas['ScenarioRunMember']
export type ScenarioValidation = Schemas['ScenarioValidationOut']
export type ShockMatchCount = Schemas['ShockMatchCount']
export type RunScenarioRef = Schemas['RunScenarioRef']
export type ApprovalState = Schemas['ApprovalState']
export type ApprovalLabel = Schemas['ApprovalLabel']
type Has = (permission: Schemas['Permission']) => boolean

/** The only module a scenario runs against (D-45 §1). */
export const BASELINE_MODULE = 'maturity_ladder'
/** GET /calculation-runs `limit` maximum. */
export const BASELINE_PAGE = 200

// --- which versions offer the panel -------------------------------------------------------------

/** A version runs only while CONFIRMED_FOR_EXPLORATION (an exploration) or APPROVED_FOR_TEST. */
export const RUNNABLE_STATUSES: readonly ScenarioVersionStatus[] = ['CONFIRMED_FOR_EXPLORATION', 'APPROVED_FOR_TEST']

export const isRunnableStatus = (status: ScenarioVersionStatus): boolean => RUNNABLE_STATUSES.includes(status)

/** Whether the panel is offered: a runnable version and `calc:run` (cosmetic; the server checks both). */
export const canRunVersion = (v: Pick<ScenarioVersion, 'status'>, has: Has): boolean => isRunnableStatus(v.status) && has('calc:run')

// --- the baseline picker ------------------------------------------------------------------------

/**
 * A baseline candidate: a `maturity_ladder` run that ended SUCCEEDED (or SUCCEEDED_WITH_WARNINGS,
 * which the server also accepts) and is not itself stressed (`scenario` null or absent). A
 * stressed run is never a baseline (409 `invalid_baseline`).
 */
export function isBaselineCandidate(run: Pick<RunSummary, 'module' | 'status' | 'scenario'>): boolean {
  return run.module.code === BASELINE_MODULE && hasResults(run.status) && !run.scenario
}

/** The picker's options: candidates only, each once, newest first. */
export function baselineCandidates(runs: readonly RunSummary[]): RunSummary[] {
  const seen = new Set<string>()
  return runs
    .filter((r) => {
      if (!isBaselineCandidate(r) || seen.has(r.run_id)) return false
      seen.add(r.run_id)
      return true
    })
    .sort((a, b) => (a.started_at < b.started_at ? 1 : a.started_at > b.started_at ? -1 : 0))
}

/** One option's text: run ID, reporting date, module version and status. */
export const baselineOptionText = (r: Pick<RunSummary, 'run_id' | 'reporting_date' | 'module' | 'status'>): string =>
  `${r.run_id.slice(0, 8)}… · reporting date ${r.reporting_date} · ${r.module.code} v${r.module.version} · ${r.status}`

/**
 * The tenant's succeeded, non-stressed maturity_ladder runs (calc:read), one page per succeeded
 * status, so a crowd of failed runs never hides a baseline. `stressed: false` has the server drop
 * stressed runs (Q23), so they never use up a page; `baselineCandidates` still filters as a guard.
 */
export const fetchBaselines = async (signal: AbortSignal): Promise<RunSummary[]> => {
  const pages: RunPage[] = await Promise.all(
    (['SUCCEEDED', 'SUCCEEDED_WITH_WARNINGS'] as const).map((status) =>
      call(() => api.GET('/api/v1/calculation-runs', { params: { query: { module_code: BASELINE_MODULE, status, stressed: false, limit: BASELINE_PAGE, offset: 0 } }, signal })),
    ),
  )
  return baselineCandidates(pages.flatMap((p) => p.items))
}

/** The baseline's detail (calc:read): its pins, which every stressed run copies exactly. */
export const fetchBaseline = (runId: string) => (signal: AbortSignal): Promise<RunDetail> =>
  call(() => api.GET('/api/v1/calculation-runs/{run_id}', { params: { path: { run_id: runId } }, signal }))

// --- the validation dry run ---------------------------------------------------------------------

/** GET …/versions/{version}/validation?baseline_run_id= : nothing is written (D-45 §10). */
export const fetchValidation = (scenarioId: string, version: number, baselineRunId: string) => (signal: AbortSignal): Promise<ScenarioValidation> =>
  call(() =>
    api.GET('/api/v1/scenarios/{scenario_id}/versions/{version}/validation', {
      params: { path: { scenario_id: scenarioId, version }, query: { baseline_run_id: baselineRunId } },
      signal,
    }),
  )

/** The shocks that match no cash flow on the baseline's inputs (`matched_flows` 0). */
export const zeroMatchShocks = (v: Pick<ScenarioValidation, 'shocks'>): ShockMatchCount[] => v.shocks.filter((s) => s.matched_flows === 0)

const BLOCKING_TEXT: Record<string, string> = {
  scenario_not_runnable: 'The version is not runnable: only a version confirmed for exploration or approved for test runs.',
  invalid_baseline: 'The baseline is not a succeeded, non-stressed maturity_ladder run of your tenant.',
  baseline_not_reproducible: 'The baseline cannot be reproduced from its pins.',
  no_matching_cashflows: 'At least one shock matches no cash flow of the baseline.',
  scenarios_disabled: 'Scenario runs are switched off (SCENARIOS_ENABLED).',
  calculations_disabled: 'Calculation runs are switched off (CALCULATIONS_ENABLED).',
}

/** A server blocking reason in words, with its code. */
export const blockingText = (code: string): string => `${BLOCKING_TEXT[code] ?? 'The server would refuse the run.'} (${code})`

/**
 * Whether Run is enabled, and why not. Fail closed: the server's `runnable` must be true, it must
 * name no blocking reason, there must be shocks and every shock must match at least one flow;
 * a zero count disables Run even if the server said runnable.
 */
export function runGate(v: Pick<ScenarioValidation, 'runnable' | 'blocking_reasons' | 'shocks'>): { runnable: boolean; reasons: string[] } {
  const reasons = v.blocking_reasons.map(blockingText)
  for (const s of zeroMatchShocks(v)) reasons.push(`Shock ${s.shock_index} (${s.template}) matches no cash flow of this baseline.`)
  if (v.shocks.length === 0) reasons.push('The version has no shocks.')
  if (!v.runnable && reasons.length === 0) reasons.push('The server says this version cannot run on this baseline.')
  return { runnable: v.runnable && reasons.length === 0, reasons }
}

/** "Creates 3 runs: each shock alone, then all combined." */
export function runCountText(v: Pick<ScenarioValidation, 'run_count' | 'shocks'>): string {
  if (v.run_count === 1) return 'Creates 1 stressed run.'
  return `Creates ${v.run_count} stressed runs: each of the ${v.shocks.length} shocks alone, then all combined.`
}

// --- submission ---------------------------------------------------------------------------------

/** A fresh `Idempotency-Key` for one run request (1–255 visible ASCII). */
export const newScenarioRunKey = (): string => `scenario-run-${newIdempotencyKey()}`

export const runBodyFor = (version: number, baselineRunId: string): ScenarioRunCreate => ({ version, baseline_run_id: baselineRunId })

/** One request attempt: its body and key. A retry of the same body reuses the key; a new body gets a new one. */
export interface RunAttempt {
  version: number
  baselineRunId: string
  key: string
}

export function attemptFor(previous: RunAttempt | null, version: number, baselineRunId: string, newKey: () => string = newScenarioRunKey): RunAttempt {
  if (previous && previous.version === version && previous.baselineRunId === baselineRunId) return previous
  return { version, baselineRunId, key: newKey() }
}

/**
 * POST scenarios/{scenario_id}/run with its Idempotency-Key: 201 a new scenario_run, 200 the
 * original one of this key (an idempotent replay). Synchronous: the member runs come back ended.
 */
export async function runScenario(scenarioId: string, attempt: RunAttempt): Promise<{ run: ScenarioRunOut; replayed: boolean }> {
  const { data, response } = await callWithResponse(() =>
    api.POST('/api/v1/scenarios/{scenario_id}/run', {
      params: { path: { scenario_id: scenarioId }, header: { 'Idempotency-Key': attempt.key } },
      body: runBodyFor(attempt.version, attempt.baselineRunId),
    }),
  )
  return { run: data, replayed: response.status === 200 }
}

const RUN_REFUSAL_TITLES: Record<string, string> = {
  forbidden: 'Not allowed to run scenarios',
  not_found: 'Scenario, version or baseline not found',
  scenario_not_runnable: 'The version is not runnable',
  invalid_baseline: 'That run cannot be a baseline',
  baseline_not_reproducible: 'The baseline cannot be reproduced',
  no_matching_cashflows: 'A shock matches no cash flow',
  scenarios_disabled: 'Scenario runs are switched off',
  calculations_disabled: 'Calculation runs are switched off',
  idempotency_conflict: 'This request key was used for a different run',
  validation_error: 'The request was refused',
}

/** A refused run or dry run, as the server sent it (status, code, correlation ID). */
export const scenarioRunRefusal = (err: unknown, fallback = 'Could not run the scenario'): Refusal => refusalOf(err, fallback, RUN_REFUSAL_TITLES)

// --- labels -------------------------------------------------------------------------------------

/** The badge of a stressed run: an exploration is never shown as approved (D-47 §3). */
export const approvalVariant = (state: ApprovalState): 'warning' | 'success' => (state === 'APPROVED' ? 'success' : 'warning')

/** A member run in words: "Shock 1 alone", or "Combined (shocks 1 + 2)". */
export function memberLabel(m: Pick<ScenarioRunMember, 'combined' | 'shock_subset'>): string {
  const subset = [...m.shock_subset].sort((a, b) => a - b)
  if (m.combined) return subset.length === 1 ? `Shock ${subset[0]}` : `Combined (shocks ${subset.join(' + ')})`
  return subset.length === 1 ? `Shock ${subset[0]} alone` : `Shocks ${subset.join(' + ')}`
}

/** How many member runs the server reports in each status, e.g. "3 SUCCEEDED". Never inferred. */
export function memberStatusText(members: readonly Pick<ScenarioRunMember, 'status'>[]): string {
  const counts = new Map<string, number>()
  for (const m of members) counts.set(m.status, (counts.get(m.status) ?? 0) + 1)
  return [...counts.entries()].map(([status, n]) => `${n} ${status}`).join(' · ')
}
