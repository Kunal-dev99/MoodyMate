import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, api } from '@/api/client'
import type { ImportSummary } from '@/components/ingestion/imports'
import { IllustrativeBanner } from '@/components/calculations/IllustrativeBanner'
import { LadderFigureValue } from '@/components/calculations/LadderFigure'
import { LiquidityView } from '@/components/calculations/LiquidityView'
import { MaturityLadderView } from '@/components/calculations/MaturityLadderView'
import { followsRun } from '@/components/calculations/polling'
import { RunIdentity } from '@/components/calculations/RunIdentity'
import { ImplementationPill, RunStatusPill } from '@/components/calculations/RunStatusPill'
import { SnapshotMembers } from '@/components/calculations/SnapshotMembers'
import {
  CANDIDATES_PER_SCHEMA,
  ILLUSTRATIVE_STATEMENT,
  IMPLEMENTATION_STYLE,
  INITIAL_PANEL,
  INPUT_REF_TEXT,
  NO_RUN_FILTERS,
  POST_CHECK_TEXT,
  RESULT_STATUSES,
  RUN_REFUSAL_TITLES,
  RUN_STATUSES,
  RUN_STATUS_STYLE,
  SEVERITY_VARIANT,
  SNAPSHOT_REFUSAL_TITLES,
  WARNING_TEXT,
  bucketRange,
  buildSnapshot,
  drillFrom,
  drillHref,
  fetchCandidates,
  hasResults,
  isReportingDate,
  isSelectable,
  ladderMaskHint,
  missingSchemas,
  panelReducer,
  pinsPreview,
  runBody,
  runHref,
  runQuery,
  runRefusal,
  selectedImports,
  snapshotRefusal,
  snapshotProblems,
  submitProblems,
  submitRun,
  type LadderFigure,
  type LadderRow,
  type ModuleVersion,
  type PanelEvent,
  type PanelState,
  type RunDetail,
  type Snapshot,
} from '@/components/calculations/calculations'

// Refusals are toasted (FE-1.4): the toasts are captured to check the correlation ID is shown.
const toasts = vi.hoisted(() => [] as { title?: string; description?: string; variant?: string }[])
vi.mock('@/components/ui/use-toast', () => ({ toast: (t: { title?: string; description?: string }) => toasts.push(t) }))
// The result panels read the drill-down from the URL; rendered here outside the app router.
vi.mock('next/navigation', () => ({ useSearchParams: () => new URLSearchParams(), useRouter: () => ({ push: vi.fn(), replace: vi.fn() }) }))

// The OpenAPI document the types are generated from: enums are tested against it (D-23).
const openapi = JSON.parse(readFileSync(fileURLToPath(new URL('../../../../../backend/openapi.json', import.meta.url)), 'utf8'))
const enumOf = (name: string): string[] => [...openapi.components.schemas[name].enum].sort()

const CID = 'cid-15'
const envelope = (code: string) => ({ code, message: `${code} message`, details: null, correlation_id: CID, retryable: false })

const IMPORT_I = '11111111-1111-4111-8111-111111111111'
const IMPORT_C = '22222222-2222-4222-8222-222222222222'
const SNAPSHOT_ID = '33333333-3333-4333-8333-333333333333'
const MODULE_VERSION_ID = '44444444-4444-4444-8444-444444444444'
const PLANNED_VERSION_ID = '55555555-5555-4555-8555-555555555555'
const RUN_ID = '66666666-6666-4666-8666-666666666666'

const ruleVersion = (code: string, n: number): ModuleVersion['rules'][number] => ({
  rule_id: `rule-${code}`,
  code,
  name: code,
  category: 'BUCKETING',
  description: null,
  owner: 'seed',
  parameter_schema: { kind: 'DAY_BUCKETS' },
  active_version: {
    rule_version_id: `rv-${code}`,
    version: n,
    status: 'ACTIVE',
    parameters: { buckets: [] },
    parameters_sha256: `sha-${code}`,
    authored_by: 'seed',
    approved_by: '0018_calculations',
    approved_at: null,
    effective_from: null,
    illustrative: true,
    provisional: true,
  },
})

const moduleVersion = (over: Partial<ModuleVersion> = {}): ModuleVersion => ({
  module_id: 'module-1',
  module_version_id: MODULE_VERSION_ID,
  code: 'maturity_ladder',
  name: 'Maturity ladder',
  version: 1,
  description: 'Illustrative maturity ladder.',
  engine_version: '1',
  implementation_status: 'AVAILABLE',
  runnable: true,
  illustrative: true,
  provisional: true,
  statement: ILLUSTRATIVE_STATEMENT,
  validation_status: 'ILLUSTRATIVE',
  created_at: '2026-10-17T00:00:00Z',
  input_contract: {
    schemas: [
      { schema_code: 'cashflow_event', required: true, fields: ['amount'] },
      { schema_code: 'instrument', required: true, fields: ['instrument_type'] },
    ],
    rules: ['maturity_buckets', 'cashflow_direction', 'past_due_treatment'],
    assumption_keys: [],
    parameters: ['reporting_date'],
  },
  output_contract: { results: ['liquidity_gap_result'], intermediates: ['CASHFLOW_CONTRIBUTION'], warning_codes: ['PAST_DUE_EXCLUDED'] },
  rules: [ruleVersion('maturity_buckets', 1), ruleVersion('cashflow_direction', 1), ruleVersion('past_due_treatment', 1)],
  assumption_set: {
    assumption_set_id: 'as-1',
    code: 'baseline',
    name: 'Baseline (illustrative)',
    version: 1,
    status: 'ACTIVE',
    assumptions: {},
    content_sha256: 'sha-assumptions',
    owner: 'seed',
    provenance: 'seed',
    effective_from: null,
    effective_to: null,
    illustrative: true,
    provisional: true,
  },
  ...over,
})

const planned = moduleVersion({
  module_version_id: PLANNED_VERSION_ID,
  code: 'lcr',
  name: 'LCR',
  implementation_status: 'PLANNED',
  runnable: false,
  rules: [],
})

const member = (schema_code: 'instrument' | 'cashflow_event', import_id: string): Snapshot['members'][number] => ({
  schema_code,
  import_id,
  import_manifest_id: `manifest-${schema_code}`,
  manifest_sha256: `sha-manifest-${schema_code}`,
  mapping_version_id: `mapping-${schema_code}`,
  projection_id: `projection-${schema_code}`,
  projector_version: 1,
  schema_version: 1,
  schema_version_id: `schema-${schema_code}`,
  accepted_count: 25,
  quarantined_count: 0,
  rejected_count: 0,
  reconciliation_outcome: 'MATCHED',
  source_code: `src_${schema_code}`,
  source_id: `source-${schema_code}`,
})

const snapshot = (members = [member('cashflow_event', IMPORT_C), member('instrument', IMPORT_I)]): Snapshot => ({
  snapshot_id: SNAPSHOT_ID,
  content_sha256: 'sha-snapshot',
  created_at: '2026-10-17T10:00:00Z',
  created_by: 'user:analyst',
  member_count: members.length,
  members,
})

const run = (over: Partial<RunDetail> = {}): RunDetail => ({
  run_id: RUN_ID,
  status: 'SUCCEEDED_WITH_WARNINGS',
  reporting_date: '2026-12-31',
  module: { module_id: 'module-1', module_version_id: MODULE_VERSION_ID, code: 'maturity_ladder', name: 'Maturity ladder', version: 1 },
  engine_version: '1',
  rule_set_id: 'rule-set-1',
  assumption_set_id: 'as-1',
  dataset_snapshot_id: SNAPSHOT_ID,
  manifest_sha256: 'sha-manifest',
  result_sha256: 'sha-result',
  illustrative: true,
  statement: ILLUSTRATIVE_STATEMENT,
  created_by: 'user:analyst',
  correlation_id: CID,
  started_at: '2026-10-17T10:00:00Z',
  ended_at: '2026-10-17T10:00:01Z',
  rerun_of_run_id: null,
  warning_count: 0,
  warnings: [],
  input_refs: [],
  outcome: null,
  validation_status: 'ILLUSTRATIVE',
  context: {
    as_of_date: '2026-12-31',
    currency: { basis: 'PER_CURRENCY_NO_FX', codes: [] },
    methodology: { module_code: 'maturity_ladder', module_version: 1, engine_version: '1', rule_versions: [] },
    units: { amount: 'exact decimal text', days: 'calendar days after the reporting date', ratio: null },
    validation_status: 'ILLUSTRATIVE',
    statement: ILLUSTRATIVE_STATEMENT,
  },
  manifest: {
    manifest_version: 1,
    reporting_date: '2026-12-31',
    engine_version: '1',
    labels: { illustrative: true, provisional: true, statement: ILLUSTRATIVE_STATEMENT },
    module: { module_id: 'module-1', module_version_id: MODULE_VERSION_ID, code: 'maturity_ladder', version: 1, engine_version: '1' },
    rule_set: { rule_set_id: 'rule-set-1', content_sha256: 'sha-rule-set', members: [] },
    assumption_set: { assumption_set_id: 'as-1', code: 'baseline', version: 1, content_sha256: 'sha-assumptions' },
    dataset_snapshot: { dataset_snapshot_id: SNAPSHOT_ID, content_sha256: 'sha-snapshot', members: [] },
  },
  ...over,
})

const job = (id: string, schema_code: string | null, status: ImportSummary['status'] = 'COMPLETED'): ImportSummary => ({
  id,
  schema_code,
  status,
  source_id: 'source-1',
  source_code: 'src',
  source_contract_id: 'contract-1',
  contract_version: 1,
  schema_version: 1,
  schema_version_id: 'schema-version-1',
  mapping_version_id: null,
  format: 'CSV',
  row_count: 25,
  expected_row_count: null,
  control_totals: null,
  data_class: 'synthetic',
  synthetic_attested: true,
  synthetic_marker: true,
  retry_of_import_id: null,
  correlation_id: null,
  created_at: '2026-10-17T09:00:00Z',
  created_by: 'user:steward',
  updated_at: '2026-10-17T09:00:00Z',
  artifact: { classification: 'confidential', detected_mime: 'text/csv', original_filename: 'x.csv', retention_state: 'RETAINED', sha256: 'sha', size_bytes: 1 },
})

/** Drives the reducer like useReducer does, for the async actions. */
function store(initial: PanelState = INITIAL_PANEL) {
  let state = initial
  const events: PanelEvent[] = []
  return {
    get state() {
      return state
    },
    events,
    dispatch: (e: PanelEvent) => {
      events.push(e)
      state = panelReducer(state, e)
    },
  }
}

const reduce = (events: PanelEvent[], from: PanelState = INITIAL_PANEL) => events.reduce(panelReducer, from)

/** The state right before "Submit run": both imports picked, the snapshot built, a module and a date. */
const ready = (): PanelState =>
  reduce([
    { type: 'select', schema: 'instrument', importId: IMPORT_I },
    { type: 'select', schema: 'cashflow_event', importId: IMPORT_C },
    { type: 'snapshot_started' },
    { type: 'snapshot_built', snapshot: snapshot(), status: 201 },
    { type: 'module', moduleVersionId: MODULE_VERSION_ID },
    { type: 'reporting_date', value: '2026-12-31' },
  ])

type Answer = { status: number; body: unknown }
const respond = ({ status, body }: Answer) => ({
  data: status < 300 ? body : undefined,
  error: status < 300 ? undefined : body,
  response: new Response(null, { status }),
})

afterEach(() => {
  vi.restoreAllMocks()
  toasts.length = 0
})

describe('run status pill (FE-15.2): every calculation_run status, as the API defines them', () => {
  it('styles exactly the OpenAPI CalculationRunStatus enum', () => {
    expect([...RUN_STATUSES].sort()).toEqual(enumOf('CalculationRunStatus'))
    expect(Object.keys(RUN_STATUS_STYLE).sort()).toEqual(enumOf('CalculationRunStatus'))
  })

  it('renders each status as the server sent it, with a label of its own', () => {
    const labels = new Set<string>()
    for (const status of enumOf('CalculationRunStatus') as (typeof RUN_STATUSES)[number][]) {
      const html = renderToStaticMarkup(createElement(RunStatusPill, { status }))
      expect(html).toContain(`data-status="${status}"`)
      expect(html).toContain(`>${RUN_STATUS_STYLE[status].label}<`)
      labels.add(RUN_STATUS_STYLE[status].label)
    }
    expect(labels.size).toBe(RUN_STATUSES.length)
  })

  it('shows results only for SUCCEEDED and SUCCEEDED_WITH_WARNINGS; RUNNING is never success', () => {
    expect([...RESULT_STATUSES].sort()).toEqual(['SUCCEEDED', 'SUCCEEDED_WITH_WARNINGS'])
    for (const status of RUN_STATUSES) expect(hasResults(status), status).toBe(status === 'SUCCEEDED' || status === 'SUCCEEDED_WITH_WARNINGS')
    expect(RUN_STATUS_STYLE.RUNNING.className).not.toBe(RUN_STATUS_STYLE.SUCCEEDED.className)
    expect(RUN_STATUS_STYLE.FAILED.className).toBe('status-failed')
    expect(RUN_STATUS_STYLE.BLOCKED.className).toBe('status-failed')
  })

  it('covers every other run enum the API defines', () => {
    expect(Object.keys(IMPLEMENTATION_STYLE).sort()).toEqual(enumOf('ImplementationStatus'))
    expect(Object.keys(SEVERITY_VARIANT).sort()).toEqual(enumOf('WarningSeverity'))
    expect(Object.keys(WARNING_TEXT).sort()).toEqual(enumOf('CalculationWarningCode'))
    expect(Object.keys(POST_CHECK_TEXT).sort()).toEqual(enumOf('PostCheck'))
    expect(Object.keys(INPUT_REF_TEXT).sort()).toEqual(enumOf('InputRefType'))
  })

  it('shows a PLANNED module as planned, never as ready', () => {
    const html = renderToStaticMarkup(createElement(ImplementationPill, { status: 'PLANNED' }))
    expect(html).toContain('Planned')
    expect(html).not.toMatch(/available|ready/i)
    expect(isSelectable(planned)).toBe(false)
    expect(isSelectable(moduleVersion())).toBe(true)
  })
})

describe('New-run panel state machine (FE-15.1)', () => {
  it('starts with nothing picked and Build disabled', () => {
    expect(snapshotProblems(INITIAL_PANEL)).toEqual(['Pick at least one projected import.'])
    expect(submitProblems(INITIAL_PANEL, [moduleVersion()])).toEqual([
      'Build or reuse a dataset snapshot first.',
      'Pick a module.',
      'Pick a reporting date.',
    ])
    expect(runBody(INITIAL_PANEL)).toBeNull()
  })

  it('picks at most one import per schema, in schema-code order, and "none" removes it', () => {
    let s = reduce([
      { type: 'select', schema: 'cashflow_event', importId: IMPORT_C },
      { type: 'select', schema: 'instrument', importId: 'other' },
      { type: 'select', schema: 'instrument', importId: IMPORT_I },
    ])
    expect(selectedImports(s)).toEqual([IMPORT_I, IMPORT_C])
    s = panelReducer(s, { type: 'select', schema: 'cashflow_event', importId: '' })
    expect(selectedImports(s)).toEqual([IMPORT_I])
    expect(s.selection).toEqual({ instrument: IMPORT_I })
  })

  it('builds: building → built (201 created, 200 reused), and ignores picks while building', () => {
    const building = reduce([{ type: 'select', schema: 'instrument', importId: IMPORT_I }, { type: 'snapshot_started' }])
    expect(building.snapshot.kind).toBe('building')
    expect(snapshotProblems(building)).toContain('A request is in flight.')
    expect(panelReducer(building, { type: 'select', schema: 'instrument', importId: 'other' })).toBe(building)
    expect(panelReducer(building, { type: 'snapshot_started' })).toBe(building)
    const created = panelReducer(building, { type: 'snapshot_built', snapshot: snapshot(), status: 201 })
    expect(created.snapshot).toMatchObject({ kind: 'built', origin: 'created' })
    const reused = panelReducer(building, { type: 'snapshot_built', snapshot: snapshot(), status: 200 })
    expect(reused.snapshot).toMatchObject({ kind: 'built', origin: 'reused' })
  })

  it('ignores answers that do not fit the state (a late answer never lands)', () => {
    const idle = reduce([{ type: 'select', schema: 'instrument', importId: IMPORT_I }])
    expect(panelReducer(idle, { type: 'snapshot_built', snapshot: snapshot(), status: 201 })).toBe(idle)
    expect(panelReducer(idle, { type: 'snapshot_refused', refusal: { title: 't', status: 409, code: 'x', message: 'm', cid: null } })).toBe(idle)
    expect(panelReducer(idle, { type: 'submitted', run: run(), status: 201 })).toBe(idle)
    expect(panelReducer(idle, { type: 'snapshot_started' }).snapshot.kind).toBe('building')
    expect(panelReducer(INITIAL_PANEL, { type: 'snapshot_started' })).toBe(INITIAL_PANEL)
  })

  it('a new pick forgets the snapshot, the run outcome and the attempt key', () => {
    const submitting = panelReducer(ready(), { type: 'submit_started', key: 'k-1' })
    const refused = panelReducer(submitting, { type: 'submit_refused', refusal: { title: 't', status: 409, code: 'x', message: 'm', cid: CID } })
    expect(refused.attemptKey).toBe('k-1')
    const repicked = panelReducer(refused, { type: 'select', schema: 'instrument', importId: 'other' })
    expect(repicked.snapshot.kind).toBe('none')
    expect(repicked.attemptKey).toBeNull()
    expect(repicked.submit.kind).toBe('idle')
  })

  it('submits only with a built snapshot, an AVAILABLE module and a real date', () => {
    const s = ready()
    expect(submitProblems(s, [moduleVersion()])).toEqual([])
    expect(runBody(s)).toEqual({ dataset_snapshot_id: SNAPSHOT_ID, module_version_id: MODULE_VERSION_ID, reporting_date: '2026-12-31' })
    expect(submitProblems(panelReducer(s, { type: 'module', moduleVersionId: PLANNED_VERSION_ID }), [moduleVersion(), planned])).toEqual([
      'Only an AVAILABLE module can run.',
    ])
    expect(submitProblems(panelReducer(s, { type: 'reporting_date', value: '2026-02-30' }), [moduleVersion()])).toEqual(['Pick a reporting date.'])
    expect(panelReducer(INITIAL_PANEL, { type: 'submit_started', key: 'k' })).toBe(INITIAL_PANEL)
  })

  it('submitting → submitted (201 created, 200 replayed); nothing changes while in flight', () => {
    const submitting = panelReducer(ready(), { type: 'submit_started', key: 'k-1' })
    expect(submitting.submit.kind).toBe('submitting')
    expect(submitProblems(submitting, [moduleVersion()])).toContain('A request is in flight.')
    expect(panelReducer(submitting, { type: 'reporting_date', value: '2027-01-01' })).toBe(submitting)
    expect(panelReducer(submitting, { type: 'module', moduleVersionId: PLANNED_VERSION_ID })).toBe(submitting)
    expect(panelReducer(submitting, { type: 'submitted', run: run(), status: 201 }).submit).toMatchObject({ kind: 'submitted', origin: 'created' })
    expect(panelReducer(submitting, { type: 'submitted', run: run(), status: 200 }).submit).toMatchObject({ kind: 'submitted', origin: 'replayed' })
  })

  it('changing the module or the date drops the attempt key (a different body never reuses it)', () => {
    const refused = reduce(
      [
        { type: 'submit_started', key: 'k-1' },
        { type: 'submit_refused', refusal: { title: 't', status: 0, code: 'network_error', message: 'm', cid: null } },
      ],
      ready(),
    )
    expect(refused.attemptKey).toBe('k-1')
    expect(panelReducer(refused, { type: 'reporting_date', value: '2027-01-01' }).attemptKey).toBeNull()
    expect(panelReducer(refused, { type: 'module', moduleVersionId: PLANNED_VERSION_ID }).attemptKey).toBeNull()
    expect(panelReducer(refused, { type: 'reporting_date', value: '2026-12-31' })).toBe(refused)
  })
})

describe('New-run panel requests and refusals (FE-15.1)', () => {
  beforeEach(() => {
    toasts.length = 0
  })

  it('POSTs the picked imports; 201 is a new snapshot, 200 the existing one', async () => {
    const post = vi.spyOn(api, 'POST').mockResolvedValue(respond({ status: 201, body: snapshot() }) as never)
    const s = store(reduce([{ type: 'select', schema: 'cashflow_event', importId: IMPORT_C }, { type: 'select', schema: 'instrument', importId: IMPORT_I }]))
    await buildSnapshot(s.state, s.dispatch)
    expect(post).toHaveBeenCalledWith('/api/v1/dataset-snapshots', { body: { import_ids: [IMPORT_I, IMPORT_C] } })
    expect(s.events.map((e) => e.type)).toEqual(['snapshot_started', 'snapshot_built'])
    expect(s.state.snapshot).toMatchObject({ kind: 'built', origin: 'created', snapshot: { snapshot_id: SNAPSHOT_ID } })
    post.mockResolvedValue(respond({ status: 200, body: snapshot() }) as never)
    await buildSnapshot(s.state, s.dispatch)
    expect(s.state.snapshot).toMatchObject({ kind: 'built', origin: 'reused' })
    expect(toasts).toHaveLength(0)
  })

  it.each(['not_projected', 'reconciliation_broken', 'duplicate_schema', 'ambiguous_business_key', 'calculations_disabled'])(
    'a 409 %s snapshot refusal is toasted with its correlation ID and kept in the panel',
    async (code) => {
      vi.spyOn(api, 'POST').mockResolvedValue(respond({ status: 409, body: envelope(code) }) as never)
      const s = store(reduce([{ type: 'select', schema: 'instrument', importId: IMPORT_I }]))
      await buildSnapshot(s.state, s.dispatch)
      expect(s.state.snapshot).toEqual({
        kind: 'refused',
        refusal: { title: SNAPSHOT_REFUSAL_TITLES[code], status: 409, code, message: `${code} message`, cid: CID },
      })
      expect(toasts).toEqual([{ variant: 'destructive', title: SNAPSHOT_REFUSAL_TITLES[code], description: `${code} message Correlation ID: ${CID}` }])
    },
  )

  it('does not POST a snapshot with nothing picked', async () => {
    const post = vi.spyOn(api, 'POST')
    const s = store()
    await buildSnapshot(s.state, s.dispatch)
    expect(post).not.toHaveBeenCalled()
    expect(s.events).toEqual([])
  })

  it('POSTs the run with an Idempotency-Key; the status shown is the server’s (background runs off: the Phase 32 request exactly)', async () => {
    const post = vi.spyOn(api, 'POST').mockResolvedValue(respond({ status: 201, body: run({ status: 'SUCCEEDED' }) }) as never)
    const s = store(ready())
    const out = await submitRun(s.state, [moduleVersion()], s.dispatch, () => 'key-1', false)
    expect(out?.run_id).toBe(RUN_ID)
    expect(post).toHaveBeenCalledWith('/api/v1/calculation-runs', {
      params: { header: { 'Idempotency-Key': 'key-1' } },
      body: { dataset_snapshot_id: SNAPSHOT_ID, module_version_id: MODULE_VERSION_ID, reporting_date: '2026-12-31' },
    })
    expect(s.state.submit).toMatchObject({ kind: 'submitted', origin: 'created', run: { status: 'SUCCEEDED' } })
  })

  it('FE-33.1: background runs send Prefer: respond-async; a 202 RUNNING run is "created", followed, and never a result', async () => {
    const post = vi.spyOn(api, 'POST').mockResolvedValue(respond({ status: 202, body: run({ status: 'RUNNING', outcome: null, ended_at: null }) }) as never)
    const s = store(ready())
    const out = await submitRun(s.state, [moduleVersion()], s.dispatch, () => 'key-1', true)
    expect(post).toHaveBeenCalledWith('/api/v1/calculation-runs', {
      params: { header: { 'Idempotency-Key': 'key-1', Prefer: 'respond-async' } },
      body: { dataset_snapshot_id: SNAPSHOT_ID, module_version_id: MODULE_VERSION_ID, reporting_date: '2026-12-31' },
    })
    expect(out?.status).toBe('RUNNING')
    expect(s.state.submit).toMatchObject({ kind: 'submitted', origin: 'created', run: { status: 'RUNNING' } })
    expect(hasResults('RUNNING')).toBe(false)
    // A later read of the same run replaces it; a read of another run never does.
    const later = panelReducer(s.state, { type: 'run_polled', run: run({ status: 'SUCCEEDED' }) })
    expect(later.submit).toMatchObject({ kind: 'submitted', origin: 'created', run: { status: 'SUCCEEDED' } })
    expect(panelReducer(s.state, { type: 'run_polled', run: run({ run_id: 'other-run', status: 'FAILED' }) })).toBe(s.state)
    expect(panelReducer(INITIAL_PANEL, { type: 'run_polled', run: run() })).toBe(INITIAL_PANEL)
  })

  it.each([
    { status: 503, code: 'workers_busy', title: 'Every calculation worker is busy', retryAfter: '7' },
    { status: 429, code: 'rate_limited', title: 'Too many requests: wait before retrying', retryAfter: '30' },
  ])('FE-33.1: a $status $code refusal shows its retry time from Retry-After; no run is shown', async ({ status, code, title, retryAfter }) => {
    const body = { ...envelope(code), retryable: true, details: { retry_after_seconds: 999 } }
    vi.spyOn(api, 'POST').mockResolvedValue({ data: undefined, error: body, response: new Response(null, { status, headers: { 'Retry-After': retryAfter } }) } as never)
    const s = store(ready())
    expect(await submitRun(s.state, [moduleVersion()], s.dispatch, () => 'k', true)).toBeNull()
    // The header wins over the envelope's details.
    expect(s.state.submit).toEqual({
      kind: 'refused',
      refusal: { title, status, code, message: `${code} message`, cid: CID, retryAfterSeconds: Number(retryAfter) },
    })
    expect(toasts).toEqual([{ variant: 'destructive', title, description: `${code} message Retry in ${retryAfter} s. Correlation ID: ${CID}` }])
    // A retry of the same body reuses the attempt key (the refusal wrote no run).
    expect(s.state.attemptKey).toBe('k')
  })

  it('FE-33.1: a RUNNING run\'s ladder and liquidity panels read nothing and say results appear when it ends; RunView keys them on the status', () => {
    const running = run({ status: 'RUNNING', outcome: null, ended_at: null })
    expect(followsRun(running)).toBe(true)
    expect(followsRun(run({ status: 'SUCCEEDED' }))).toBe(false)
    expect(followsRun(null)).toBe(false)
    const panels = [
      [MaturityLadderView, 'ladder-pending'],
      [LiquidityView, 'liquidity-pending'],
    ] as const
    for (const [View, testId] of panels) {
      const html = renderToStaticMarkup(createElement(View as typeof LiquidityView, { run: running }))
      expect(html, testId).toContain(`data-testid="${testId}" data-status="RUNNING"`)
      expect(html).toContain('Results appear when the run ends')
      // The panel keeps the run's identity; it never shows a figure or a success.
      expect(html).toContain(RUN_ID)
      expect(html).not.toContain('ladder-none')
      // A run the server ended reads its results instead (still loading in a static render).
      expect(renderToStaticMarkup(createElement(View as typeof LiquidityView, { run: run({ status: 'SUCCEEDED' }) }))).not.toContain(testId)
    }
    // Polling moves the run to its terminal status; the new key remounts the panel, which then reads.
    const source = readFileSync(join(__dirname, '..', 'RunView.tsx'), 'utf8')
    expect(source).toContain('<LiquidityView key={`${run.run_id}:${run.status}`} run={run} />')
    expect(source).toContain('<MaturityLadderView key={`${run.run_id}:${run.status}`} run={run} />')
  })

  it('FE-33.1 NEXT_PUBLIC_BACKGROUND_RUNS_UI=off: no Prefer header, no polling, and a RUNNING run\'s panels as in Phase 32', async () => {
    vi.stubEnv('NEXT_PUBLIC_BACKGROUND_RUNS_UI', 'off')
    vi.resetModules()
    try {
      const features = await import('@/config/features')
      const client = await import('@/api/client')
      const calc = await import('@/components/calculations/calculations')
      const polling = await import('@/components/calculations/polling')
      const ladder = await import('@/components/calculations/MaturityLadderView')
      expect(features.BACKGROUND_RUNS_UI_ENABLED).toBe(false)
      const post = vi.spyOn(client.api, 'POST').mockResolvedValue(respond({ status: 201, body: run({ status: 'SUCCEEDED' }) }) as never)
      const s = store(ready())
      // The switch's default: no respondAsync argument.
      expect((await calc.submitRun(s.state, [moduleVersion()], s.dispatch, () => 'key-off'))?.status).toBe('SUCCEEDED')
      expect(post).toHaveBeenCalledWith('/api/v1/calculation-runs', {
        params: { header: { 'Idempotency-Key': 'key-off' } },
        body: { dataset_snapshot_id: SNAPSHOT_ID, module_version_id: MODULE_VERSION_ID, reporting_date: '2026-12-31' },
      })
      // No polling: the poller's gate (useRunPolling, and the run page's panels) follows no run, RUNNING included.
      expect(polling.followsRun(run({ status: 'RUNNING' }))).toBe(false)
      expect(polling.followsRun(run({ status: 'QUEUED' }))).toBe(false)
      expect(renderToStaticMarkup(createElement(ladder.MaturityLadderView, { run: run({ status: 'RUNNING' }) }))).not.toContain('ladder-pending')
    } finally {
      vi.unstubAllEnvs()
      vi.resetModules()
    }
  })

  it('a BLOCKED or FAILED run is shown as the server ended it, never as success', async () => {
    for (const status of ['BLOCKED', 'FAILED'] as const) {
      vi.spyOn(api, 'POST').mockResolvedValue(respond({ status: 201, body: run({ status }) }) as never)
      const s = store(ready())
      await submitRun(s.state, [moduleVersion()], s.dispatch, () => 'key')
      expect(s.state.submit).toMatchObject({ kind: 'submitted', run: { status } })
      expect(hasResults(status)).toBe(false)
      vi.restoreAllMocks()
    }
  })

  it.each(['module_not_available', 'calculations_disabled', 'idempotency_conflict'])(
    'a 409 %s run refusal is toasted with its correlation ID, and a retry reuses the attempt key',
    async (code) => {
      const post = vi.spyOn(api, 'POST').mockResolvedValue(respond({ status: 409, body: envelope(code) }) as never)
      const s = store(ready())
      const keys = ['key-1', 'key-2']
      const out = await submitRun(s.state, [moduleVersion()], s.dispatch, () => keys.shift() as string)
      expect(out).toBeNull()
      expect(s.state.submit).toEqual({
        kind: 'refused',
        refusal: { title: RUN_REFUSAL_TITLES[code], status: 409, code, message: `${code} message`, cid: CID },
      })
      expect(toasts).toEqual([{ variant: 'destructive', title: RUN_REFUSAL_TITLES[code], description: `${code} message Correlation ID: ${CID}` }])
      await submitRun(s.state, [moduleVersion()], s.dispatch, () => keys.shift() as string)
      const sent = post.mock.calls.map((c) => (c[1] as { params: { header: Record<string, string> } }).params.header['Idempotency-Key'])
      expect(sent).toEqual(['key-1', 'key-1'])
    },
  )

  it('a 403 is refused by the server even when the button showed (D-04)', async () => {
    vi.spyOn(api, 'POST').mockResolvedValue(respond({ status: 403, body: envelope('forbidden') }) as never)
    const s = store(ready())
    await submitRun(s.state, [moduleVersion()], s.dispatch, () => 'k')
    expect(s.state.submit).toMatchObject({ kind: 'refused', refusal: { status: 403, code: 'forbidden', title: 'You may not submit runs' } })
    expect(toasts[0].description).toContain(`Correlation ID: ${CID}`)
  })

  it('a network failure is a refusal without a correlation ID, and nothing is shown as submitted', async () => {
    vi.spyOn(api, 'POST').mockRejectedValue(new TypeError('Failed to fetch'))
    const s = store(ready())
    await submitRun(s.state, [moduleVersion()], s.dispatch, () => 'k')
    expect(s.state.submit).toMatchObject({ kind: 'refused', refusal: { status: 0, code: 'network_error', cid: null } })
    expect(s.state.attemptKey).toBe('k')
  })

  it('does not POST a run while something is missing', async () => {
    const post = vi.spyOn(api, 'POST')
    const s = store(panelReducer(ready(), { type: 'module', moduleVersionId: PLANNED_VERSION_ID }))
    expect(await submitRun(s.state, [moduleVersion(), planned], s.dispatch)).toBeNull()
    expect(post).not.toHaveBeenCalled()
  })

  it('names an undocumented failure generically, keeping its code and correlation ID', () => {
    expect(runRefusal(new ApiError(500, envelope('internal_error')))).toEqual({
      title: 'Could not submit the run',
      status: 500,
      code: 'internal_error',
      message: 'internal_error message',
      cid: CID,
    })
    expect(snapshotRefusal(new ApiError(500, envelope('internal_error'))).title).toBe('Could not build the snapshot')
    expect(snapshotRefusal(new Error('x'))).toMatchObject({ status: null, code: 'unexpected_error', cid: null })
  })
})

describe('the versions a run would pin, shown before submitting (FE-15.1)', () => {
  it('lists the module, engine, each rule version, the assumption set, the snapshot and the date', () => {
    const rows = pinsPreview(moduleVersion(), snapshot(), '2026-12-31')
    expect(rows.map((r) => r.kind)).toEqual([
      'MODULE_VERSION',
      'ENGINE',
      'RULE_VERSION',
      'RULE_VERSION',
      'RULE_VERSION',
      'ASSUMPTION_SET',
      'DATASET_SNAPSHOT',
      'REPORTING_DATE',
    ])
    expect(rows.every((r) => !r.missing)).toBe(true)
    expect(rows[0]).toMatchObject({ id: MODULE_VERSION_ID, version: 'v1' })
    expect(rows[1]).toMatchObject({ version: '1' })
    expect(rows[2]).toMatchObject({ label: 'maturity_buckets', id: 'rv-maturity_buckets', version: 'v1', hash: 'sha-maturity_buckets' })
    expect(rows[2].note).toContain('illustrative')
    expect(rows[5]).toMatchObject({ id: 'as-1', version: 'v1', hash: 'sha-assumptions' })
    expect(rows[6]).toMatchObject({ id: SNAPSHOT_ID, hash: 'sha-snapshot' })
    expect(rows[7]).toMatchObject({ version: '2026-12-31' })
  })

  it('flags a rule without an ACTIVE version, a missing assumption set, snapshot or date', () => {
    const m = moduleVersion({ rules: [{ ...ruleVersion('maturity_buckets', 1), active_version: null }], assumption_set: null })
    const rows = pinsPreview(m, null, '')
    expect(rows.filter((r) => r.missing).map((r) => r.kind)).toEqual(['RULE_VERSION', 'ASSUMPTION_SET', 'DATASET_SNAPSHOT', 'REPORTING_DATE'])
  })

  it('hints when the snapshot lacks a schema the module requires (the server would block the run)', () => {
    expect(missingSchemas(moduleVersion(), snapshot())).toEqual([])
    expect(missingSchemas(moduleVersion(), snapshot([member('instrument', IMPORT_I)]))).toEqual(['cashflow_event'])
  })

  it('accepts only real calendar dates', () => {
    expect(isReportingDate('2026-12-31')).toBe(true)
    expect(isReportingDate('2028-02-29')).toBe(true)
    expect(isReportingDate('2026-02-30')).toBe(false)
    expect(isReportingDate('2026-1-1')).toBe(false)
    expect(isReportingDate('')).toBe(false)
  })
})

describe('projected-import candidates (FE-15.1)', () => {
  type Query = { projected?: boolean | null; schema_code?: string | null; limit?: number; offset?: number }
  const listing = (byCode: Record<string, { items: ImportSummary[]; total: number }>, status = 200) =>
    vi.spyOn(api, 'GET').mockImplementation(((path: string, init: { params: { query: Query } }) => {
      expect(path).toBe('/api/v1/imports')
      const page = byCode[init.params.query.schema_code ?? ''] ?? { items: [], total: 0 }
      return Promise.resolve(respond(status === 200 ? { status, body: { ...page, limit: init.params.query.limit, offset: 0 } } : { status, body: envelope('forbidden') }))
    }) as never)

  it('asks the server once per projector schema for its projected imports (GET /imports?projected=true&schema_code=)', async () => {
    const get = listing({
      instrument: { items: [job(IMPORT_I, 'instrument')], total: 1 },
      cashflow_event: { items: [job(IMPORT_C, 'cashflow_event', 'QUARANTINED')], total: 75 },
    })
    const signal = new AbortController().signal
    const out = await fetchCandidates(signal)
    expect(get).toHaveBeenCalledTimes(enumOf('DomainSchemaCode').length)
    for (const code of enumOf('DomainSchemaCode'))
      expect(get).toHaveBeenCalledWith('/api/v1/imports', {
        params: { query: { projected: true, schema_code: code, limit: CANDIDATES_PER_SCHEMA, offset: 0 } },
        signal,
      })
    expect(Object.keys(out).sort()).toEqual(enumOf('DomainSchemaCode'))
    expect(out.instrument).toEqual({ items: [job(IMPORT_I, 'instrument')], total: 1 })
    expect(out.cashflow_event.total).toBe(75)
    expect(out.cashflow_event.items.map((j) => j.id)).toEqual([IMPORT_C])
    expect(out.position).toEqual({ items: [], total: 0 })
  })

  it('never reads per-import projections any more: one call per schema code', async () => {
    const get = listing({})
    await fetchCandidates(new AbortController().signal)
    expect(get.mock.calls.every((c) => c[0] === '/api/v1/imports')).toBe(true)
  })

  it('fails as a whole on a refusal (e.g. no import:read), so the panel shows the 403 state', async () => {
    listing({}, 403)
    await expect(fetchCandidates(new AbortController().signal)).rejects.toMatchObject({ status: 403, envelope: { code: 'forbidden', correlation_id: CID } })
  })
})

describe('run list and drill-down links (FE-15.1/15.2)', () => {
  it('sends only set, well-formed filters', () => {
    expect(runQuery(NO_RUN_FILTERS)).toEqual({})
    expect(runQuery({ module_code: ' maturity_ladder ', status: 'SUCCEEDED' })).toEqual({ module_code: 'maturity_ladder', status: 'SUCCEEDED' })
    expect(runQuery({ module_code: '', status: 'DONE' })).toEqual({})
  })

  it('links a ladder cell to its contributions, and reads it back from the URL', () => {
    const href = drillHref(RUN_ID, 'EUR', 'D030_090')
    expect(href).toBe(`${runHref(RUN_ID)}?currency=EUR&bucket=D030_090`)
    expect(drillFrom(new URLSearchParams(href.split('?')[1]))).toEqual({ currency: 'EUR', bucket: 'D030_090' })
    expect(drillFrom(new URLSearchParams('currency=eur&bucket=x'))).toBeNull()
    expect(drillFrom(new URLSearchParams('currency=EUR'))).toBeNull()
  })

  it('describes a bucket as the pinned rule does: (lower, upper] days, the last one open', () => {
    expect(bucketRange({ lower_exclusive_days: 30, upper_inclusive_days: 90 })).toBe('(30, 90] days')
    expect(bucketRange({ lower_exclusive_days: 730, upper_inclusive_days: null })).toBe('(730, ∞) days')
  })
})

describe('ladder figures under D-33 minimum aggregation (FE-15.2)', () => {
  const MASKED = { masked: true as const, classification: 'confidential' }
  // EUR (30,90] of the e2e scenario as risk_analyst sees it: 2 contributions, minimum 3.
  const few = (over: Partial<LadderRow> = {}): LadderRow => ({
    liquidity_gap_result_id: 'gap-1',
    bucket_code: 'B04',
    bucket_label: '(30, 90] days',
    bucket_sequence: 4,
    classification: 'confidential',
    contribution_count: 2,
    inflows: MASKED,
    outflows: MASKED,
    net_gap: MASKED,
    cumulative_gap: MASKED,
    inflows_contractual: '0',
    outflows_contractual: MASKED,
    inflows_behavioural: MASKED,
    outflows_behavioural: '0',
    ...over,
  })
  const FIGURES: LadderFigure[] = [
    'inflows',
    'outflows',
    'net_gap',
    'cumulative_gap',
    'inflows_contractual',
    'outflows_contractual',
    'inflows_behavioural',
    'outflows_behavioural',
  ]

  it('explains every masked figure with the server minimum, and nothing for clear ones (zeros stay clear)', () => {
    const row = few()
    for (const figure of FIGURES) {
      const hint = ladderMaskHint(row, figure, 3)
      if (typeof row[figure] === 'string') expect(hint, figure).toBeNull()
      else expect(hint, figure).toBe('masked: fewer than 3 contributions')
    }
    expect(ladderMaskHint(row, 'inflows', 5)).toBe('masked: fewer than 5 contributions')
  })

  it('says a cumulative gap is masked by an earlier bucket when its own net gap is clear', () => {
    const later = few({ contribution_count: 4, inflows: '10.00', outflows: '4.00', net_gap: '6.00', cumulative_gap: MASKED })
    expect(ladderMaskHint(later, 'cumulative_gap', 3)).toBe("masked: an earlier bucket's net gap has fewer than 3 contributions")
    expect(ladderMaskHint(later, 'net_gap', 3)).toBeNull()
  })

  it('renders a masked figure with MaskedValue and the hint, a clear one as its exact text', () => {
    const masked = renderToStaticMarkup(createElement(LadderFigureValue, { row: few(), figure: 'inflows', minAggregateCount: 3, testId: 'ladder-inflows' }))
    expect(masked).toContain('data-testid="ladder-inflows" data-masked="true"')
    expect(masked).toContain('data-testid="masked-value"')
    expect(masked).toMatch(/data-testid="ladder-inflows-hint"[^>]*>masked: fewer than 3 contributions</)
    const zero = renderToStaticMarkup(createElement(LadderFigureValue, { row: few(), figure: 'inflows_contractual', minAggregateCount: 3, testId: 'x' }))
    expect(zero).toMatch(/data-testid="x"[^>]*>0</)
    expect(zero).not.toContain('masked')
    const exact = renderToStaticMarkup(
      createElement(LadderFigureValue, { row: few({ inflows: '9007199254740993.000000000000000001' }), figure: 'inflows', minAggregateCount: 3, testId: 'y' }),
    )
    // Grouped by thousands on the string (P-7): no digit lost to float; data-value keeps the server's text.
    expect(exact).toContain('>9,007,199,254,740,993.000000000000000001<')
    expect(exact).toContain('data-value="9007199254740993.000000000000000001"')
    expect(exact).not.toContain('-hint')
  })

  it('the ladder carries the minimum as the API defines it', () => {
    expect(openapi.components.schemas.MaturityLadderOut.required).toContain('min_aggregate_count')
  })
})

describe('labelling and identity on every result view (D-35 §3, FE-15.2)', () => {
  it('the banner says illustrative, not regulatory, no vendor parity, with the server statement', () => {
    const html = renderToStaticMarkup(createElement(IllustrativeBanner, {}))
    expect(html).toContain('Illustrative prototype — not regulatory')
    expect(html).toContain('no parity with any vendor system is claimed')
    const server = renderToStaticMarkup(createElement(IllustrativeBanner, { statement: 'Server statement.', compact: true }))
    expect(server).toContain('Server statement.')
    expect(server).toContain('data-testid="illustrative-statement"')
  })

  it('the run identity shows the run ID, status, reporting date and every pinned configuration version', () => {
    const html = renderToStaticMarkup(createElement(RunIdentity, { run: run() }))
    for (const text of [RUN_ID, '2026-12-31', 'maturity_ladder v1', 'rule-set-1', 'as-1', SNAPSHOT_ID, 'data-status="SUCCEEDED_WITH_WARNINGS"'])
      expect(html).toContain(text)
    expect(html).toMatch(/data-testid="identity-engine"[^>]*>1</)
  })

  it('a snapshot member shows its import link, manifest hash, versions and reconciliation outcome', () => {
    const html = renderToStaticMarkup(createElement(SnapshotMembers, { members: snapshot().members }))
    expect(html).toContain(`href="/ingestion/imports/${IMPORT_I}"`)
    for (const text of ['sha-manifest-instrument', 'mapping-instrument', 'projection-instrument', 'schema-instrument', 'data-outcome="MATCHED"'])
      expect(html).toContain(text)
  })
})

describe('Calculations navigation and reversibility (FE-15.1, D-02)', () => {
  afterEach(() => {
    vi.unstubAllEnvs()
    vi.resetModules()
  })

  it('is live and needs calc:read', async () => {
    const { mainWorkspaces } = await import('@/config/navigation')
    const calculations = mainWorkspaces.find((w) => w.slug === 'calculations')
    expect(calculations?.planned).toBeUndefined()
    expect(calculations?.permission).toBe('calc:read')
  })

  it('NEXT_PUBLIC_CALCULATIONS_UI=off restores the Phase 14 planned entry exactly', async () => {
    vi.stubEnv('NEXT_PUBLIC_CALCULATIONS_UI', 'off')
    vi.resetModules()
    const { mainWorkspaces } = await import('@/config/navigation')
    const calculations = mainWorkspaces.find((w) => w.slug === 'calculations')
    expect(calculations).toMatchObject({
      href: '/calculations',
      label: 'Calculations',
      description: 'Module selection, run submission, run history, warnings and results.',
      planned: { phase: 'Feature 03–04', workstream: 'WS3 / WS4' },
    })
    expect(calculations?.permission).toBeUndefined()
  })
})

/**
 * Amounts and gaps are decimal text from the API: the calculations code is grepped for every
 * way JavaScript turns text into a float (as FE-12.1/14.1), and for hand-written payload types.
 */
describe('calculations code hygiene (FE-15.1/15.2)', () => {
  const root = join(__dirname, '..', '..', '..')
  const files = [
    'components/calculations/calculations.ts',
    'components/calculations/CalculationsGate.tsx',
    'components/calculations/IllustrativeBanner.tsx',
    'components/calculations/LadderFigure.tsx',
    'components/calculations/LiquidityView.tsx',
    'components/calculations/ManifestPanel.tsx',
    'components/calculations/MaturityLadderView.tsx',
    'components/calculations/NewRunPanel.tsx',
    'components/calculations/RunIdentity.tsx',
    'components/calculations/RunList.tsx',
    'components/calculations/RunStatusPill.tsx',
    'components/calculations/RunView.tsx',
    'components/calculations/SnapshotMembers.tsx',
    'app/(platform)/calculations/page.tsx',
    'app/(platform)/calculations/new/page.tsx',
    'app/(platform)/calculations/runs/[runId]/page.tsx',
  ]
  const PARSING = [/parseFloat/, /parseInt/, /\bNumber\s*\(/, /\bNumber\./, /\bMath\./, /\.toFixed\s*\(/, /NumberFormat/, /\bBigInt\s*\(/, /(^|[=(,:?!&|{]|return)\s*\+\s*[A-Za-z_$(]/m]
  // A payload field declared with a primitive type is a hand-written mirror of the API.
  const HAND_WRITTEN =
    /\b(run_id|snapshot_id|dataset_snapshot_id|module_version_id|rule_version_id|rule_set_id|assumption_set_id|manifest_sha256|result_sha256|content_sha256|inflows|outflows|net_gap|cumulative_gap|contribution_count|intermediate_value_id|cashflow_event_id|import_id|source_ordinal|reporting_date)\??\s*:\s*(string|number|boolean)\b/

  it.each(files)('%s never parses a value to a number', (file) => {
    const source = readFileSync(join(root, file), 'utf8')
    for (const pattern of PARSING) expect(source, `${file} matches ${pattern}`).not.toMatch(pattern)
  })

  it.each([...files, '../e2e/calculations.spec.ts', '../e2e/liquidity.spec.ts', '../e2e/calc-support.ts'])('%s declares no hand-written payload type', (file) => {
    expect(readFileSync(join(root, file), 'utf8')).not.toMatch(HAND_WRITTEN)
  })

  it('the greps would catch a violation', () => {
    expect(HAND_WRITTEN.test('type R = { run_id: string }')).toBe(true)
    expect(HAND_WRITTEN.test('interface L { inflows?: string }')).toBe(true)
    expect(PARSING.some((p) => p.test('const x = parseFloat(row.inflows)'))).toBe(true)
    expect(PARSING.some((p) => p.test('const x = +row.inflows'))).toBe(true)
  })

  it('the run views only read; the panel POSTs snapshots and runs', () => {
    const calls = (file: string) => new Set(readFileSync(join(root, file), 'utf8').match(/api\.[A-Z]+\(/g) ?? [])
    expect(calls('components/calculations/RunList.tsx')).toEqual(new Set(['api.GET(']))
    expect(calls('components/calculations/RunView.tsx')).toEqual(new Set(['api.GET(']))
    expect(calls('components/calculations/MaturityLadderView.tsx')).toEqual(new Set(['api.GET(']))
    expect(calls('components/calculations/LiquidityView.tsx')).toEqual(new Set(['api.GET(']))
    expect(calls('components/calculations/calculations.ts')).toEqual(new Set(['api.GET(', 'api.POST(']))
  })
})
