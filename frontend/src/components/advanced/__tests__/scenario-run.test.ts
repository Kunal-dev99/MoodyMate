import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, api } from '@/api/client'
import {
  BASELINE_MODULE,
  RUNNABLE_STATUSES,
  attemptFor,
  baselineCandidates,
  canRunVersion,
  fetchBaselines,
  isBaselineCandidate,
  memberLabel,
  memberStatusText,
  runGate,
  runScenario,
  scenarioRunRefusal,
  zeroMatchShocks,
  type RunScenarioRef,
  type ScenarioRunOut,
  type ScenarioValidation,
  type ShockMatchCount,
} from '@/components/advanced/scenario-runs'
import { BaselinePicker, RunResult, ValidationBlock } from '@/components/advanced/ScenarioRunPanel'
import { StressedRunBadge } from '@/components/calculations/StressedRunBadge'
import type { RunSummary } from '@/components/calculations/calculations'

/*
 * FE-25.1 (Done-when): the baseline picker's filter (succeeded, non-stressed maturity_ladder runs
 * only) and the zero-match state of the validation dry run (Run disabled while any count is 0),
 * plus the Idempotency-Key, the approval badges and the NEXT_PUBLIC_STRESS_COMPARE_UI switch.
 * Fixtures use the generated types (src/api/schema.d.ts) only.
 */

const toasts = vi.hoisted(() => [] as { title?: string }[])
vi.mock('@/components/ui/use-toast', () => ({ toast: (t: { title?: string }) => toasts.push(t) }))

const openapi = JSON.parse(readFileSync(fileURLToPath(new URL('../../../../../backend/openapi.json', import.meta.url)), 'utf8'))
const enumOf = (name: string): string[] => [...openapi.components.schemas[name].enum].sort()

const SCENARIO = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
const VERSION_ID = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
const SCENARIO_RUN = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'
const BASE = '10000000-0000-4000-8000-000000000001'

const summary = (run_id: string, over: Partial<RunSummary> = {}): RunSummary => ({
  run_id,
  status: 'SUCCEEDED',
  reporting_date: '2026-12-31',
  module: { module_id: 'module-1', module_version_id: 'mv-1', code: 'maturity_ladder', name: 'Maturity ladder', version: 1 },
  engine_version: '1',
  rule_set_id: 'rule-set-1',
  assumption_set_id: 'as-1',
  dataset_snapshot_id: 'snapshot-1',
  manifest_sha256: 'sha-manifest',
  result_sha256: 'sha-result',
  illustrative: true,
  created_by: 'user:analyst',
  started_at: '2026-10-17T10:00:00Z',
  ended_at: '2026-10-17T10:00:01Z',
  rerun_of_run_id: null,
  warning_count: 0,
  scenario: null,
  ...over,
})

const ref = (over: Partial<RunScenarioRef> = {}): RunScenarioRef => ({
  approval_label: 'Exploration, not approved',
  approval_state: 'EXPLORATION',
  baseline_run_id: BASE,
  combined: true,
  scenario_id: SCENARIO,
  scenario_run_id: SCENARIO_RUN,
  scenario_version_id: VERSION_ID,
  shock_subset: [1, 2],
  version: 1,
  ...over,
})

const shock = (shock_index: number, matched_flows: number, over: Partial<ShockMatchCount> = {}): ShockMatchCount => ({
  shock_index,
  matched_flows,
  parts: matched_flows * 2,
  direction_mismatches: 0,
  skipped_incomplete: 0,
  skipped_past_due: 0,
  template: shock_index === 1 ? 'DELAYED_INFLOWS' : 'DEPOSIT_WITHDRAWAL',
  ...over,
})

const validation = (over: Partial<ScenarioValidation> = {}): ScenarioValidation => ({
  approval_label: 'Exploration, not approved',
  approval_state: 'EXPLORATION',
  baseline_run_id: BASE,
  blocking_reasons: [],
  labels: { statement: 'Illustrative stress exploration, not a regulatory stress test.' },
  run_count: 3,
  runnable: true,
  scenario_id: SCENARIO,
  scenario_version_id: VERSION_ID,
  shocks: [shock(1, 2), shock(2, 3)],
  status: 'CONFIRMED_FOR_EXPLORATION',
  version: 1,
  ...over,
})

const scenarioRun = (over: Partial<ScenarioRunOut> = {}): ScenarioRunOut => ({
  approval_label: 'Exploration, not approved',
  approval_state: 'EXPLORATION',
  baseline_run_id: BASE,
  correlation_id: 'cid-25',
  created_at: '2026-10-19T10:00:00Z',
  created_by: 'user:analyst',
  labels: { statement: 'Illustrative stress exploration, not a regulatory stress test.' },
  members: [
    { calculation_run_id: 'run-1', combined: false, shock_subset: [1], status: 'SUCCEEDED', manifest_sha256: 'm1', result_sha256: 'r1' },
    { calculation_run_id: 'run-2', combined: false, shock_subset: [2], status: 'SUCCEEDED', manifest_sha256: 'm2', result_sha256: 'r2' },
    { calculation_run_id: 'run-3', combined: true, shock_subset: [1, 2], status: 'FAILED', manifest_sha256: 'm3', result_sha256: null },
  ],
  reporting_date: '2026-12-31',
  scenario_code: 'SCN-1',
  scenario_id: SCENARIO,
  scenario_name: 'Two-week loan repayment delay',
  scenario_run_id: SCENARIO_RUN,
  scenario_version_id: VERSION_ID,
  shock_counts: [shock(1, 2), shock(2, 3)],
  shocks_sha256: 'shocks-sha',
  version: 1,
  ...over,
})

const respond = (status: number, body: unknown) => ({
  data: status < 300 ? body : undefined,
  error: status < 300 ? undefined : body,
  response: new Response(null, { status }),
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllEnvs()
  vi.doUnmock('@/components/auth/SessionProvider')
  vi.resetModules()
  toasts.length = 0
})

describe('the baseline picker filter (FE-25.1 Done-when)', () => {
  it('keeps succeeded, non-stressed maturity_ladder runs only', () => {
    expect(BASELINE_MODULE).toBe('maturity_ladder')
    expect(isBaselineCandidate(summary('a'))).toBe(true)
    expect(isBaselineCandidate(summary('a', { status: 'SUCCEEDED_WITH_WARNINGS' }))).toBe(true)
    // A stressed run is never a baseline (409 invalid_baseline), whatever its status.
    expect(isBaselineCandidate(summary('a', { scenario: ref() }))).toBe(false)
    // `scenario` is optional in the contract: absent means a Feature 03 run.
    const { scenario: _omitted, ...withoutScenario } = summary('a')
    expect(isBaselineCandidate(withoutScenario)).toBe(true)
    for (const status of ['QUEUED', 'RUNNING', 'BLOCKED', 'FAILED'] as const) expect(isBaselineCandidate(summary('a', { status }))).toBe(false)
    expect(isBaselineCandidate(summary('a', { module: { ...summary('a').module, code: 'lcr_hqla', name: 'LCR' } }))).toBe(false)
  })

  it('every run status the server knows is classified (no status is silently a baseline)', () => {
    const accepted = enumOf('CalculationRunStatus').filter((status) => isBaselineCandidate(summary('a', { status: status as RunSummary['status'] })))
    expect(accepted).toEqual(['SUCCEEDED', 'SUCCEEDED_WITH_WARNINGS'])
  })

  it('lists each candidate once, newest first', () => {
    const runs = [
      summary('old', { started_at: '2026-10-01T00:00:00Z' }),
      summary('stressed', { started_at: '2026-10-05T00:00:00Z', scenario: ref() }),
      summary('new', { started_at: '2026-10-03T00:00:00Z', status: 'SUCCEEDED_WITH_WARNINGS' }),
      summary('failed', { started_at: '2026-10-04T00:00:00Z', status: 'FAILED' }),
      summary('old', { started_at: '2026-10-01T00:00:00Z' }),
    ]
    expect(baselineCandidates(runs).map((r) => r.run_id)).toEqual(['new', 'old'])
  })

  it('asks the server for maturity_ladder runs per succeeded status, then filters out stressed runs', async () => {
    const get = vi.spyOn(api, 'GET').mockImplementation(((_path: string, init: { params: { query: { status: string } } }) =>
      Promise.resolve(
        respond(200, {
          items: init.params.query.status === 'SUCCEEDED' ? [summary('plain'), summary('stressed', { scenario: ref() })] : [summary('warned', { status: 'SUCCEEDED_WITH_WARNINGS', started_at: '2026-10-18T00:00:00Z' })],
          total: 1,
          limit: 200,
          offset: 0,
        }),
      )) as never)
    const runs = await fetchBaselines(new AbortController().signal)
    expect(get).toHaveBeenCalledTimes(2)
    const queries = get.mock.calls.map((c) => (c[1] as { params: { query: Record<string, unknown> } }).params.query)
    expect(queries).toEqual([
      { module_code: 'maturity_ladder', status: 'SUCCEEDED', limit: 200, offset: 0 },
      { module_code: 'maturity_ladder', status: 'SUCCEEDED_WITH_WARNINGS', limit: 200, offset: 0 },
    ])
    expect(runs.map((r) => r.run_id)).toEqual(['warned', 'plain'])
  })

  it('renders the candidates as options, or a "no baseline" state', () => {
    const html = renderToStaticMarkup(createElement(BaselinePicker, { id: 'p', state: { kind: 'ready', value: [summary(BASE)] }, value: '', disabled: false, onChange: () => undefined }))
    expect(html).toContain('data-testid="baseline-picker"')
    expect(html).toContain(`value="${BASE}"`)
    expect(html).toContain('reporting date 2026-12-31 · maturity_ladder v1 · SUCCEEDED')
    const none = renderToStaticMarkup(createElement(BaselinePicker, { id: 'p', state: { kind: 'ready', value: [] }, value: '', disabled: false, onChange: () => undefined }))
    expect(none).toContain('data-testid="baseline-none"')
    expect(none).not.toContain('baseline-picker')
  })
})

describe('the zero-match state (FE-25.1 Done-when)', () => {
  it('Run is enabled only when the server says runnable and every shock matches', () => {
    expect(runGate(validation())).toEqual({ runnable: true, reasons: [] })
  })

  it('a shock with 0 matched flows disables Run, even if the server said runnable (fail closed)', () => {
    const v = validation({ shocks: [shock(1, 2), shock(2, 0)] })
    expect(zeroMatchShocks(v).map((s) => s.shock_index)).toEqual([2])
    const gate = runGate(v)
    expect(gate.runnable).toBe(false)
    expect(gate.reasons).toEqual(['Shock 2 (DEPOSIT_WITHDRAWAL) matches no cash flow of this baseline.'])
  })

  it("names the server's blocking reasons with their codes, and never runs without shocks", () => {
    const gate = runGate(validation({ runnable: false, blocking_reasons: ['no_matching_cashflows', 'something_new'], shocks: [shock(1, 0)] }))
    expect(gate.runnable).toBe(false)
    expect(gate.reasons).toEqual([
      'At least one shock matches no cash flow of the baseline. (no_matching_cashflows)',
      'The server would refuse the run. (something_new)',
      'Shock 1 (DELAYED_INFLOWS) matches no cash flow of this baseline.',
    ])
    expect(runGate(validation({ shocks: [] })).runnable).toBe(false)
    expect(runGate(validation({ runnable: false })).reasons).toEqual(['The server says this version cannot run on this baseline.'])
  })

  it('renders the zero-match row, the "No match" badge and the disabled notice', () => {
    const html = renderToStaticMarkup(createElement(ValidationBlock, { validation: validation({ shocks: [shock(1, 4), shock(2, 0)] }) }))
    expect(html).toContain('data-runnable="false"')
    expect(html).toContain('data-shock-index="2" data-matched="0"')
    expect(html.match(/data-testid="validation-zero-match"/g)).toHaveLength(1)
    expect(html).toContain('data-testid="run-blocked"')
    expect(html).toContain('Exploration, not approved')
    expect(html).toContain('Creates 3 stressed runs: each of the 2 shocks alone, then all combined.')
    const ok = renderToStaticMarkup(createElement(ValidationBlock, { validation: validation() }))
    expect(ok).toContain('data-runnable="true"')
    expect(ok).not.toContain('validation-zero-match')
    expect(ok).not.toContain('run-blocked')
  })
})

describe('submission (FE-25.1)', () => {
  it('reuses the key for a retry of the same body, and makes a new one for a new body', () => {
    let n = 0
    const key = () => `k${++n}`
    const first = attemptFor(null, 1, BASE, key)
    expect(first).toEqual({ version: 1, baselineRunId: BASE, key: 'k1' })
    expect(attemptFor(first, 1, BASE, key)).toBe(first)
    expect(attemptFor(first, 1, 'other', key).key).toBe('k2')
    expect(attemptFor(first, 2, BASE, key).key).toBe('k3')
  })

  it('POSTs the version and baseline with the Idempotency-Key; 200 is a replay', async () => {
    const post = vi.spyOn(api, 'POST').mockResolvedValue(respond(201, scenarioRun()) as never)
    const done = await runScenario(SCENARIO, { version: 1, baselineRunId: BASE, key: 'scenario-run-k' })
    expect(done.replayed).toBe(false)
    expect(done.run.scenario_run_id).toBe(SCENARIO_RUN)
    expect(post).toHaveBeenCalledWith('/api/v1/scenarios/{scenario_id}/run', {
      params: { path: { scenario_id: SCENARIO }, header: { 'Idempotency-Key': 'scenario-run-k' } },
      body: { version: 1, baseline_run_id: BASE },
    })
    post.mockResolvedValue(respond(200, scenarioRun()) as never)
    expect((await runScenario(SCENARIO, { version: 1, baselineRunId: BASE, key: 'scenario-run-k' })).replayed).toBe(true)
  })

  it('keeps a refusal as the server sent it', async () => {
    vi.spyOn(api, 'POST').mockResolvedValue(
      respond(409, { code: 'no_matching_cashflows', message: 'refused', details: null, correlation_id: 'cid-409', retryable: false }) as never,
    )
    const err = await runScenario(SCENARIO, { version: 1, baselineRunId: BASE, key: 'k' }).catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(scenarioRunRefusal(err)).toEqual({ title: 'A shock matches no cash flow', status: 409, code: 'no_matching_cashflows', message: 'refused', cid: 'cid-409' })
  })

  it("shows each member run with the server's own status, never a success it did not state", () => {
    const run = scenarioRun()
    expect(memberStatusText(run.members)).toBe('2 SUCCEEDED · 1 FAILED')
    expect(run.members.map(memberLabel)).toEqual(['Shock 1 alone', 'Shock 2 alone', 'Combined (shocks 1 + 2)'])
    expect(memberLabel({ combined: true, shock_subset: [1] })).toBe('Shock 1')
    const html = renderToStaticMarkup(createElement(RunResult, { result: { run, replayed: false } }))
    expect(html).toContain('data-run-id="run-3" data-status="FAILED"')
    expect(html.match(/data-testid="scenario-run-member"/g)).toHaveLength(3)
    expect(html).toContain('href="/calculations/runs/run-1"')
    expect(html).toContain('data-approval-state="EXPLORATION"')
    // Q27: the result links to its comparison while NEXT_PUBLIC_STRESS_COMPARE_UI is on.
    expect(html).toContain('data-testid="scenario-run-result-compare"')
    expect(html).toContain(`href="/advanced/stress-explorer/${SCENARIO}/runs/${run.scenario_run_id}"`)
  })
})

describe('the stressed-run badge on the run list and detail (FE-25.1)', () => {
  it('renders nothing for a Feature 03 run', () => {
    expect(renderToStaticMarkup(createElement(StressedRunBadge, { scenario: null }))).toBe('')
    expect(renderToStaticMarkup(createElement(StressedRunBadge, { scenario: undefined }))).toBe('')
  })

  it('labels an exploration and an approved run with the server label, and links baseline and scenario', () => {
    const exploration = renderToStaticMarkup(createElement(StressedRunBadge, { scenario: ref({ combined: false, shock_subset: [2] }) }))
    expect(exploration).toContain('data-approval-state="EXPLORATION"')
    expect(exploration).toContain('Exploration, not approved')
    expect(exploration).toContain('Shock 2 alone')
    expect(exploration).toContain(`href="/calculations/runs/${BASE}"`)
    expect(exploration).toContain(`href="/advanced/stress-explorer/${SCENARIO}"`)
    const approved = renderToStaticMarkup(createElement(StressedRunBadge, { scenario: ref({ approval_state: 'APPROVED', approval_label: 'Approved scenario (illustrative)' }) }))
    expect(approved).toContain('Approved scenario (illustrative)')
    expect(approved).not.toContain('Exploration, not approved')
  })
})

describe('the panel gate and NEXT_PUBLIC_STRESS_COMPARE_UI (FE-25.1, D-02)', () => {
  const version = (status: (typeof RUNNABLE_STATUSES)[number] | 'DRAFT' | 'IN_REVIEW' | 'REJECTED' | 'CLOSED') => ({ status, version: 1, scenario_version_id: VERSION_ID })

  async function render(status: Parameters<typeof version>[0], granted: string[]) {
    vi.doMock('@/components/auth/SessionProvider', () => ({ useCurrentSession: () => ({ me: { user_id: 'u' }, has: (p: string) => granted.includes(p) }) }))
    vi.resetModules()
    const { ScenarioRunPanel } = await import('@/components/advanced/ScenarioRunPanel')
    return renderToStaticMarkup(createElement(ScenarioRunPanel, { scenarioId: SCENARIO, version: version(status), enabled: true }))
  }

  it('offers the panel on exploration and approved versions only, to calc:run', async () => {
    expect(RUNNABLE_STATUSES).toEqual(['CONFIRMED_FOR_EXPLORATION', 'APPROVED_FOR_TEST'])
    const has = (p: string) => p === 'calc:run'
    for (const status of enumOf('ScenarioVersionStatus')) expect(canRunVersion({ status: status as ScenarioValidation['status'] }, has)).toBe(RUNNABLE_STATUSES.includes(status as ScenarioValidation['status']))
    expect(canRunVersion({ status: 'APPROVED_FOR_TEST' }, () => false)).toBe(false)
    expect(await render('CONFIRMED_FOR_EXPLORATION', ['calc:run'])).toContain('data-testid="run-against-baseline"')
    expect(await render('APPROVED_FOR_TEST', ['calc:run'])).toContain('Run against baseline')
    expect(await render('DRAFT', ['calc:run'])).toBe('')
    expect(await render('CONFIRMED_FOR_EXPLORATION', ['calc:read'])).toBe('')
  })

  it('off (or the explorer off) hides the panel and restores the Phase 24 texts', async () => {
    vi.stubEnv('NEXT_PUBLIC_STRESS_COMPARE_UI', 'off')
    expect(await render('CONFIRMED_FOR_EXPLORATION', ['calc:run'])).toBe('')
    const offPanel = await import('@/components/advanced/ScenarioRunPanel')
    expect(renderToStaticMarkup(createElement(offPanel.RunResult, { result: { run: scenarioRun(), replayed: false } }))).not.toContain('scenario-run-result-compare')
    const { advancedWorkspaces } = await import('@/config/navigation')
    expect(advancedWorkspaces[0].description).toBe(
      'Author scenarios from the approved shock templates, confirm them for exploration and promote them through maker-checker approval. Illustrative, not a regulatory stress test.',
    )
    vi.unstubAllEnvs()
    vi.stubEnv('NEXT_PUBLIC_STRESS_EXPLORER_UI', 'off')
    vi.resetModules()
    expect((await import('@/config/features')).STRESS_COMPARE_UI_ENABLED).toBe(false)
    vi.unstubAllEnvs()
    vi.resetModules()
    expect((await import('@/config/features')).STRESS_COMPARE_UI_ENABLED).toBe(true)
    expect((await import('@/config/navigation')).advancedWorkspaces[0].description).toMatch(/run them against a baseline maturity ladder/)
  })
})

describe('run panel code hygiene (FE-25.1)', () => {
  const root = fileURLToPath(new URL('../../../', import.meta.url))
  const files = ['components/advanced/scenario-runs.ts', 'components/advanced/ScenarioRunPanel.tsx', 'components/calculations/StressedRunBadge.tsx']
  const read = (file: string) => readFileSync(`${root}${file}`, 'utf8')

  it.each(files)('%s never parses a figure as a float', (file) => {
    for (const p of [/parseFloat/, /\bNumber\s*\(/, /\bNumber\./, /\.toFixed\s*\(/, /NumberFormat/]) expect(read(file), `${file} ${p}`).not.toMatch(p)
  })

  it('writes only through POST scenarios/{scenario_id}/run, and the other files not at all', () => {
    expect(read('components/advanced/scenario-runs.ts').match(/api\.(POST|PUT|PATCH|DELETE)\('[^']+'/g)).toEqual(["api.POST('/api/v1/scenarios/{scenario_id}/run'"])
    for (const file of files.slice(1)) expect(read(file), file).not.toMatch(/api\.[A-Z]+\(/)
  })
})
