import { ApiError, api, call, callWithResponse, type Schemas } from '@/api/client'
import { toastApiError } from '@/components/common/api-error-toast'
import { BACKGROUND_RUNS_UI_ENABLED, LIQUIDITY_UI_ENABLED } from '@/config/features'
import { isMasked } from '@/components/common/MaskedValue'
import { DOMAIN_SCHEMA_CODES, type DomainSchemaCode } from '@/components/domain/domain'
import type { ImportSummary } from '@/components/ingestion/imports'
import { newIdempotencyKey } from '@/lib/idempotency'
import { isIsoDate } from '@/lib/forms'
import { refusalOf, type Refusal } from '@/lib/refusal'

/*
 * FE-15.1/15.2: the Calculations workspace's pure helpers and the New-run panel's state
 * machine. Every payload type comes from the generated API schema. Amounts, gaps and dates
 * are exact text from the server and are shown as that text: this file and the calculations
 * components never turn them into numbers (a test greps them for number parsing). Nothing
 * here decides that a run succeeded: the status is always the server's (D-31 §3).
 */

export type RunStatus = Schemas['CalculationRunStatus']
export type RunSummary = Schemas['RunSummary']
export type RunPage = Schemas['RunPage']
export type RunDetail = Schemas['RunDetail']
export type RunCreate = Schemas['RunCreate']
export type RunManifest = Schemas['RunManifest']
export type RunOutcome = Schemas['RunOutcome']
export type RunWarning = Schemas['WarningOut']
export type InputRef = Schemas['InputRefOut']
export type InputRefType = Schemas['InputRefType']
export type ModuleVersion = Schemas['ModuleVersionOut']
export type ModulePage = Schemas['ModulePage']
export type ModuleRule = Schemas['ModuleRuleOut']
export type ImplementationStatus = Schemas['ImplementationStatus']
export type Snapshot = Schemas['SnapshotOut']
export type SnapshotMember = Schemas['SnapshotMemberOut']
export type MaturityLadder = Schemas['MaturityLadderOut']
export type LadderCurrency = Schemas['LadderCurrency']
export type LadderRow = Schemas['LadderRow']
export type Bucket = Schemas['BucketOut']
export type ResultPage = Schemas['ResultPage']
export type ResultItem = Schemas['ResultItem']
export type WarningCode = Schemas['CalculationWarningCode']
export type WarningSeverity = Schemas['WarningSeverity']
export type PostCheck = Schemas['PostCheck']
export type CashflowDirection = Schemas['CashflowDirection']
export type Liquidity = Schemas['LiquidityOut']
export type LcrCurrency = Schemas['LiquidityCurrency']
export type HqlaAsset = Schemas['HqlaAssetOut']
export type HqlaLevelTotal = Schemas['HqlaLevelOut']
export type HqlaLevel = Schemas['HqlaLevel']
export type HqlaExclusionReason = Schemas['HqlaExclusionReason']
export type LcrWarningCode = Schemas['LcrWarningCode']

/** D-35 §3: the label on every seeded module and every result, when the server sends none. */
export const ILLUSTRATIVE_STATEMENT =
  'Illustrative prototype. Not a regulatory calculation; no parity with any vendor system is claimed.'

/**
 * `calculation_run.status` (Data Model §9, D-06, D-31 §3) on the design system's status
 * styles. The Record type turns a status the backend adds into a compile error here, and a
 * test keeps it equal to the OpenAPI enum. Only SUCCEEDED and SUCCEEDED_WITH_WARNINGS are
 * shown as finished with a result; RUNNING is "in progress", never success.
 */
export const RUN_STATUS_STYLE: Record<RunStatus, { label: string; className: string; description: string }> = {
  QUEUED: {
    label: 'Queued',
    className: 'status-draft',
    // FE-33.1: still reserved (D-59 §3); a background run is written RUNNING, never QUEUED.
    description: BACKGROUND_RUNS_UI_ENABLED
      ? 'Reserved for a later release: a background run is written RUNNING, never QUEUED.'
      : 'Reserved for a later release: runs are synchronous in this release.',
  },
  RUNNING: {
    label: 'Running',
    className: 'status-building',
    description: 'Written before the module ran; no outcome yet, so nothing is shown as a result.',
  },
  SUCCEEDED: { label: 'Succeeded', className: 'status-published', description: 'Ended; every post-check passed.' },
  SUCCEEDED_WITH_WARNINGS: {
    label: 'Succeeded with warnings',
    className: 'status-testing',
    description: 'Ended; every post-check passed, and warnings were raised (inputs excluded with a code, never silently).',
  },
  FAILED: {
    label: 'Failed',
    className: 'status-failed',
    description: 'A post-check or the engine failed: the outputs are not a result (never a silent success).',
  },
  CANCELLED: { label: 'Cancelled', className: 'status-draft', description: 'Reserved for a later release.' },
  BLOCKED: {
    label: 'Blocked',
    className: 'status-failed',
    description: 'The input contract or the pinned configuration was not met: nothing was calculated.',
  },
}
export const RUN_STATUSES = Object.keys(RUN_STATUS_STYLE) as RunStatus[]

/** The statuses whose outputs are results (the server's word, D-31 §3). */
export const RESULT_STATUSES: readonly RunStatus[] = ['SUCCEEDED', 'SUCCEEDED_WITH_WARNINGS']
export const hasResults = (status: RunStatus): boolean => RESULT_STATUSES.includes(status)

/** `module_version.implementation_status`: PLANNED is an extension point, never shown as ready. */
export const IMPLEMENTATION_STYLE: Record<ImplementationStatus, { label: string; className: string }> = {
  AVAILABLE: { label: 'Available · illustrative prototype', className: 'status-building' },
  PLANNED: { label: 'Planned — extension point', className: 'status-draft' },
}

/**
 * FE-18.1: why a PLANNED module cannot be picked. It is a declared extension point (D-31 §1):
 * its contracts are fixed, no engine implements it, and the server refuses a run of it.
 */
export const PLANNED_MODULE_REASON =
  'Not selectable: a declared extension point. Its input and output contracts are fixed, but no engine implements it in this release, and the server refuses a run of it (409 module_not_available).'

export const SEVERITY_VARIANT: Record<WarningSeverity, 'info' | 'warning' | 'destructive'> = {
  INFO: 'info',
  WARNING: 'warning',
  ERROR: 'destructive',
}

/** What each warning code means (value-free, as the server's messages are). */
export const WARNING_TEXT: Record<WarningCode, string> = {
  INSTRUMENT_NOT_IN_SNAPSHOT: 'Excluded: the cash flow names an instrument key with no instrument version in the snapshot.',
  UNMAPPED_INSTRUMENT_TYPE: 'Excluded: the instrument type is not a key of the pinned cashflow_direction rule.',
  PAST_DUE_EXCLUDED: 'Excluded: due before the reporting date (past_due_treatment EXCLUDE_WITH_WARNING).',
  PAST_DUE_IN_FIRST_BUCKET: 'Past due, counted in the first bucket (past_due_treatment INCLUDE_IN_FIRST_BUCKET).',
  OUTSIDE_BUCKETS: 'Excluded: its days after the reporting date fall in no pinned bucket (e.g. due on the reporting date).',
  MISSING_VALUE: 'Excluded: its due date, amount or currency is missing.',
  NEGATIVE_AMOUNT: 'Excluded: a negative amount is ambiguous, as cash-flow v1 has no direction field.',
  UNKNOWN_ORIGIN: 'Excluded: its origin is neither CONTRACTUAL nor BEHAVIOURAL.',
  INPUT_SCHEMA_MISSING: 'Blocked: a schema the module requires is not in the snapshot.',
  PARAMETERS_INVALID: "Blocked: a pinned rule version's parameters do not fit the module.",
  BUCKET_DEFINITIONS_MISMATCH: 'Blocked: the bucket definitions do not match the pinned bucket rule version.',
  AMBIGUOUS_INSTRUMENT_KEY: 'Blocked: one instrument key resolves to two instruments in the snapshot.',
  POST_CHECK_FAILED: 'Failed: a post-check did not hold (Arch §6.2 step 5).',
  ENGINE_ERROR: 'Failed: the engine raised an error; no output is a result.',
}

export const POST_CHECK_TEXT: Record<PostCheck, string> = {
  COVERAGE: 'Every in-scope cash flow is bucketed once or excluded with a warning code.',
  BUCKET_DEFINED: 'Every contribution lands in a pinned bucket.',
  BUCKET_TOTALS: 'Every bucket total equals the sum of its contributions.',
  CUMULATIVE_GAP: 'The cumulative gap is the running sum of the net gaps.',
  HQLA_TOTALS: 'Every HQLA level total equals the sum of its included asset rows (lcr_hqla).',
  LCR_FIGURES: 'Every per-currency LCR figure is recomputed from its contributions (lcr_hqla).',
  RERUN_REPRODUCED: 'A re-run reproduces the original run\'s result hash on the original pins.',
  // Phase 25 (D-45 §6): stressed runs only.
  SCENARIO_OVERLAY: 'The overlay recomputed from the unstressed inputs equals the stored parts and shock applications (stressed runs).',
  SCENARIO_CONSERVATION:
    'Per cash flow the parts sum to the original amount (times 1 + f for a deposit withdrawal), and per currency and direction the stressed total equals the baseline total plus the declared deltas (stressed runs).',
}

export const INPUT_REF_TEXT: Record<InputRefType, string> = {
  DATASET_SNAPSHOT: 'Dataset snapshot',
  IMPORT_MANIFEST: 'Import manifest',
  DOMAIN_PROJECTION: 'Domain projection',
  SCHEMA_VERSION: 'Schema version',
  MAPPING_VERSION: 'Mapping version',
  MODULE_VERSION: 'Module version',
  ENGINE: 'Engine',
  RULE_SET: 'Rule set',
  RULE_VERSION: 'Rule version',
  ASSUMPTION_SET: 'Assumption set',
}

// --- links -----------------------------------------------------------------------------------

export const runHref = (runId: string): string => `/calculations/runs/${encodeURIComponent(runId)}`
export const snapshotLabel = (id: string): string => `${id.slice(0, 8)}…`

/** A ladder bucket's interval as the pinned rule defines it: (lower, upper] calendar days; the last one open. */
export function bucketRange(b: Pick<Bucket, 'lower_exclusive_days' | 'upper_inclusive_days'>): string {
  return b.upper_inclusive_days === null ? `(${b.lower_exclusive_days}, ∞) days` : `(${b.lower_exclusive_days}, ${b.upper_inclusive_days}] days`
}

// --- the ladder: D-33 minimum aggregation ---------------------------------------------------

/** The ladder figures a row carries (the generated row's own keys). */
export type LadderFigure = Extract<
  keyof LadderRow,
  'inflows' | 'outflows' | 'net_gap' | 'cumulative_gap' | 'inflows_contractual' | 'outflows_contractual' | 'inflows_behavioural' | 'outflows_behavioural'
>

/**
 * Why the server masked a ladder figure (D-33 minimum aggregation): a figure built from fewer
 * than `min_aggregate_count` contributions takes their classification, as does every figure
 * derived from it, and the cumulative gap stays masked for the rest of the currency once a net
 * gap is. Null when the figure is shown. The figure itself is always the server's (masked or
 * exact text); this only explains it.
 */
export function ladderMaskHint(row: LadderRow, figure: LadderFigure, minAggregateCount: number): string | null {
  if (!isMasked(row[figure])) return null
  if (figure === 'cumulative_gap' && !isMasked(row.net_gap))
    return `masked: an earlier bucket's net gap has fewer than ${minAggregateCount} contributions`
  return `masked: fewer than ${minAggregateCount} contributions`
}

// --- the liquidity view (lcr_hqla, FE-17.1) -------------------------------------------------

/** The module whose runs the run detail shows as the liquidity view. */
export const LCR_MODULE = 'lcr_hqla'

/**
 * Whether the run detail shows this run as the liquidity view (in the ladder's place): an
 * `lcr_hqla` run, unless NEXT_PUBLIC_LIQUIDITY_UI=off, which restores the Phase 15 layout (D-02).
 */
export const showsLiquidity = (run: Pick<RunSummary, 'module'>, enabled: boolean = LIQUIDITY_UI_ENABLED): boolean =>
  enabled && run.module.code === LCR_MODULE

/** `lcr_result.warning_codes` (BE-17.2): value-free warnings about one currency's LCR. */
export const LCR_WARNING_TEXT: Record<LcrWarningCode, string> = {
  LCR_DENOMINATOR_ZERO: 'Outflows minus capped inflows is zero or negative, so the ratio is not computable (NULL).',
  LCR_LEVEL_CAPS_NOT_APPLIED: 'Level 2A/2B assets are included without the L2 caps: a documented v1 limitation.',
}

/** `hqla_asset_result.exclusion_reason` (BE-17.1): why an asset row adds nothing to HQLA. */
export const EXCLUSION_TEXT: Record<HqlaExclusionReason, string> = {
  NOT_HQLA_ELIGIBLE: 'Its instrument type has no HQLA level in the pinned hqla_level rule.',
}

/** The HQLA levels, in the order the server lists them (L1, L2A, L2B). */
export const HQLA_LEVEL_TEXT: Record<HqlaLevel, string> = {
  L1: 'Level 1',
  L2A: 'Level 2A',
  L2B: 'Level 2B',
}

/**
 * Why the server masked an LCR figure (D-33 minimum aggregation): a figure built from fewer
 * than `min` contributions takes their classification, and so does every figure derived from
 * one. `count` is the figure's own contribution count, or null for a derived figure. Null when
 * the figure is shown. The figure itself is always the server's; this only explains it.
 */
export function lcrMaskHint(value: unknown, count: number | null, min: number): string | null {
  if (!isMasked(value)) return null
  if (count !== null && count < min) return `masked: fewer than ${min} contributions`
  return `masked: derived from a figure with fewer than ${min} contributions`
}

/** How many included asset rows the HQLA total is built from (the levels' own counts). */
export const hqlaAssetCount = (row: Pick<LcrCurrency, 'hqla_levels'>): number => row.hqla_levels.reduce((n, l) => n + l.asset_count, 0)

// --- the run list ----------------------------------------------------------------------------

export interface RunFilters {
  module_code: string
  status: string
}
export const NO_RUN_FILTERS: RunFilters = { module_code: '', status: '' }

const isRunStatus = (s: string): s is RunStatus => (RUN_STATUSES as string[]).includes(s)

/** The GET /calculation-runs query: only set, well-formed filters. */
export function runQuery(f: RunFilters): { module_code?: string; status?: RunStatus } {
  const query: { module_code?: string; status?: RunStatus } = {}
  const code = f.module_code.trim()
  if (code) query.module_code = code
  if (isRunStatus(f.status)) query.status = f.status
  return query
}

// --- the contributions drill-down ------------------------------------------------------------

const CURRENCY = /^[A-Z]{3}$/

/** The drill-down a run page shows, from its URL (`?currency=&bucket=`); null when none or malformed. */
export function drillFrom(params: URLSearchParams): { currency: string; bucket: string } | null {
  const currency = params.get('currency') ?? ''
  const bucket = params.get('bucket') ?? ''
  if (!CURRENCY.test(currency) || !bucket || bucket.length > 20) return null
  return { currency, bucket }
}

export const drillHref = (runId: string, currency: string, bucket: string): string =>
  `${runHref(runId)}?${new URLSearchParams({ currency, bucket }).toString()}`

// --- the New-run panel: candidates -----------------------------------------------------------

/** How many of the newest projected imports per schema the panel offers (GET /imports page size). */
export const CANDIDATES_PER_SCHEMA = 50

/** The projected imports offered per schema code, newest first, and how many exist in all. */
export type Candidates = Record<DomainSchemaCode, { items: ImportSummary[]; total: number }>

/**
 * GET /imports?projected=true&schema_code=<code> (import:read), once per projector schema: the
 * server lists only imports projected into the WS3 domain (D-29), tenant-scoped, newest first.
 */
export const fetchCandidates = async (signal: AbortSignal): Promise<Candidates> => {
  const pages = await Promise.all(
    DOMAIN_SCHEMA_CODES.map((schema_code) =>
      call(() =>
        api.GET('/api/v1/imports', { params: { query: { projected: true, schema_code, limit: CANDIDATES_PER_SCHEMA, offset: 0 } }, signal }),
      ),
    ),
  )
  return Object.fromEntries(DOMAIN_SCHEMA_CODES.map((code, i) => [code, { items: pages[i].items, total: pages[i].total }])) as Candidates
}

/** GET /calculation-modules (rule:read): AVAILABLE and PLANNED, with what a run now would pin. */
export const fetchModules = (signal: AbortSignal): Promise<ModulePage> =>
  call(() => api.GET('/api/v1/calculation-modules', { signal }))

/** A module the panel lets the user pick: AVAILABLE only (PLANNED never runs, D-31 §1). */
export const isSelectable = (m: Pick<ModuleVersion, 'implementation_status'>): boolean => m.implementation_status === 'AVAILABLE'

/** The schemas the module requires that the snapshot lacks: such a run would be BLOCKED (cosmetic hint; the server decides). */
export function missingSchemas(mod: Pick<ModuleVersion, 'input_contract'>, snapshot: Pick<Snapshot, 'members'> | null): string[] {
  const have = new Set((snapshot?.members ?? []).map((m) => m.schema_code as string))
  return mod.input_contract.schemas.filter((s) => s.required && !have.has(s.schema_code)).map((s) => s.schema_code)
}

// --- the New-run panel: the versions a run would pin -----------------------------------------

export interface PinRow {
  kind: InputRefType | 'REPORTING_DATE'
  label: string
  id: string | null
  version: string | null
  hash: string | null
  note: string | null
  /** True when the pin is absent, so the server would refuse the run (409 module_not_available). */
  missing: boolean
}

/**
 * FE-15.1: what a run submitted now would pin, shown before submitting: the module version,
 * the engine, each rule's ACTIVE version, the baseline assumption set, the snapshot and the
 * reporting date. All of it is the server's (GET /calculation-modules, the snapshot answer);
 * the rule set itself is content-addressed from these versions when the run is written.
 */
export function pinsPreview(mod: ModuleVersion, snapshot: Snapshot | null, reportingDate: string): PinRow[] {
  const rows: PinRow[] = [
    {
      kind: 'MODULE_VERSION',
      label: `${mod.code} v${mod.version}`,
      id: mod.module_version_id,
      version: `v${mod.version}`,
      hash: null,
      note: mod.illustrative ? 'illustrative prototype' : null,
      missing: false,
    },
    { kind: 'ENGINE', label: 'Calculation engine', id: null, version: mod.engine_version, hash: null, note: null, missing: false },
  ]
  for (const rule of mod.rules) {
    const v = rule.active_version
    rows.push(
      v
        ? {
            kind: 'RULE_VERSION',
            label: rule.code,
            id: v.rule_version_id,
            version: `v${v.version}`,
            hash: v.parameters_sha256,
            note: [v.status, v.illustrative ? 'illustrative' : null, v.provisional ? 'provisional' : null, v.approved_by ? `approved by ${v.approved_by}` : null]
              .filter(Boolean)
              .join(' · '),
            missing: false,
          }
        : { kind: 'RULE_VERSION', label: rule.code, id: null, version: null, hash: null, note: 'No ACTIVE version: the module cannot run.', missing: true },
    )
  }
  const a = mod.assumption_set
  rows.push(
    a
      ? {
          kind: 'ASSUMPTION_SET',
          label: a.name,
          id: a.assumption_set_id,
          version: `v${a.version}`,
          hash: a.content_sha256,
          note: [a.code, a.status, a.illustrative ? 'illustrative' : null, a.provisional ? 'provisional' : null].filter(Boolean).join(' · '),
          missing: false,
        }
      : { kind: 'ASSUMPTION_SET', label: 'Baseline assumption set', id: null, version: null, hash: null, note: 'No ACTIVE baseline assumption set.', missing: true },
  )
  rows.push(
    snapshot
      ? {
          kind: 'DATASET_SNAPSHOT',
          label: `${snapshot.member_count} member${snapshot.member_count === 1 ? '' : 's'}: ${snapshot.members.map((m) => m.schema_code).join(', ')}`,
          id: snapshot.snapshot_id,
          version: null,
          hash: snapshot.content_sha256,
          note: null,
          missing: false,
        }
      : { kind: 'DATASET_SNAPSHOT', label: 'Dataset snapshot', id: null, version: null, hash: null, note: 'Build the snapshot first.', missing: true },
  )
  rows.push({
    kind: 'REPORTING_DATE',
    label: 'Reporting date',
    id: null,
    version: isReportingDate(reportingDate) ? reportingDate : null,
    hash: null,
    note: isReportingDate(reportingDate) ? 'Buckets count calendar days after it.' : 'Pick a date.',
    missing: !isReportingDate(reportingDate),
  })
  return rows
}

/** A calendar date as the API takes it (YYYY-MM-DD, a real day): the shared check (FE-29.1). */
export const isReportingDate = (value: string): boolean => isIsoDate(value)

// --- the New-run panel: the state machine ----------------------------------------------------

export type SnapshotStep =
  | { kind: 'none' }
  | { kind: 'building' }
  | { kind: 'built'; snapshot: Snapshot; origin: 'created' | 'reused' }
  | { kind: 'refused'; refusal: Refusal }

export type SubmitStep =
  | { kind: 'idle' }
  | { kind: 'submitting' }
  | { kind: 'submitted'; run: RunDetail; origin: 'created' | 'replayed' }
  | { kind: 'refused'; refusal: Refusal }

export interface PanelState {
  /** The import picked per schema code (at most one, D-30 §1). */
  selection: Partial<Record<DomainSchemaCode, string>>
  snapshot: SnapshotStep
  moduleVersionId: string
  reportingDate: string
  /**
   * One Idempotency-Key per run request (D-31 §5): kept for a retry of the same body, dropped
   * as soon as anything the body holds changes, so a different run never reuses it.
   */
  attemptKey: string | null
  submit: SubmitStep
}

export type PanelEvent =
  | { type: 'select'; schema: DomainSchemaCode; importId: string }
  | { type: 'snapshot_started' }
  | { type: 'snapshot_built'; snapshot: Snapshot; status: number }
  | { type: 'snapshot_refused'; refusal: Refusal }
  | { type: 'module'; moduleVersionId: string }
  | { type: 'reporting_date'; value: string }
  | { type: 'submit_started'; key: string }
  | { type: 'submitted'; run: RunDetail; status: number }
  | { type: 'submit_refused'; refusal: Refusal }
  /** FE-33.1: a later read of the submitted background run (its status as the server reports it now). */
  | { type: 'run_polled'; run: RunDetail }

export const INITIAL_PANEL: PanelState = {
  selection: {},
  snapshot: { kind: 'none' },
  moduleVersionId: '',
  reportingDate: '',
  attemptKey: null,
  submit: { kind: 'idle' },
}

/** The import ids picked, in schema-code order. */
export const selectedImports = (s: Pick<PanelState, 'selection'>): string[] =>
  DOMAIN_SCHEMA_CODES.map((c) => s.selection[c]).filter((id): id is string => !!id)

const busy = (s: PanelState): boolean => s.snapshot.kind === 'building' || s.submit.kind === 'submitting'

/** Why "Build snapshot" is disabled (empty: it may be clicked). */
export function snapshotProblems(s: PanelState): string[] {
  const problems: string[] = []
  if (busy(s)) problems.push('A request is in flight.')
  if (selectedImports(s).length === 0) problems.push('Pick at least one projected import.')
  return problems
}

/** Why "Submit run" is disabled (empty: it may be clicked). Cosmetic: the server decides. */
export function submitProblems(s: PanelState, modules: readonly ModuleVersion[]): string[] {
  const problems: string[] = []
  if (busy(s)) problems.push('A request is in flight.')
  if (s.snapshot.kind !== 'built') problems.push('Build or reuse a dataset snapshot first.')
  const picked = modules.find((m) => m.module_version_id === s.moduleVersionId)
  if (!picked) problems.push('Pick a module.')
  else if (!isSelectable(picked)) problems.push('Only an AVAILABLE module can run.')
  if (!isReportingDate(s.reportingDate)) problems.push('Pick a reporting date.')
  return problems
}

const resetRun = { attemptKey: null, submit: { kind: 'idle' } as SubmitStep }

/**
 * The New-run panel (FE-15.1): pick one projected import per schema, build (or reuse) the
 * snapshot, pick an AVAILABLE module and a reporting date, review the pins, submit. A change
 * to anything a request holds forgets that request's outcome; events that do not fit the
 * current state (e.g. a late answer) leave it unchanged.
 */
export function panelReducer(s: PanelState, e: PanelEvent): PanelState {
  switch (e.type) {
    case 'select': {
      if (busy(s)) return s
      const selection = { ...s.selection }
      if (e.importId) selection[e.schema] = e.importId
      else delete selection[e.schema]
      return { ...s, selection, snapshot: { kind: 'none' }, ...resetRun }
    }
    case 'snapshot_started':
      if (snapshotProblems(s).length > 0) return s
      return { ...s, snapshot: { kind: 'building' }, ...resetRun }
    case 'snapshot_built':
      if (s.snapshot.kind !== 'building') return s
      return { ...s, snapshot: { kind: 'built', snapshot: e.snapshot, origin: e.status === 200 ? 'reused' : 'created' } }
    case 'snapshot_refused':
      if (s.snapshot.kind !== 'building') return s
      return { ...s, snapshot: { kind: 'refused', refusal: e.refusal } }
    case 'module':
      if (busy(s) || e.moduleVersionId === s.moduleVersionId) return s
      return { ...s, moduleVersionId: e.moduleVersionId, ...resetRun }
    case 'reporting_date':
      if (busy(s) || e.value === s.reportingDate) return s
      return { ...s, reportingDate: e.value, ...resetRun }
    case 'submit_started':
      if (busy(s) || s.snapshot.kind !== 'built' || !s.moduleVersionId || !isReportingDate(s.reportingDate)) return s
      return { ...s, attemptKey: e.key, submit: { kind: 'submitting' } }
    case 'submitted':
      if (s.submit.kind !== 'submitting') return s
      return { ...s, submit: { kind: 'submitted', run: e.run, origin: e.status === 200 ? 'replayed' : 'created' } }
    case 'submit_refused':
      if (s.submit.kind !== 'submitting') return s
      return { ...s, submit: { kind: 'refused', refusal: e.refusal } }
    case 'run_polled':
      // Only the run this panel submitted, and only while it is still the panel's.
      if (s.submit.kind !== 'submitted' || s.submit.run.run_id !== e.run.run_id) return s
      return { ...s, submit: { ...s.submit, run: e.run } }
  }
}

/** The POST /calculation-runs body of the panel's state; null until it is complete. */
export function runBody(s: PanelState): RunCreate | null {
  if (s.snapshot.kind !== 'built' || !s.moduleVersionId || !isReportingDate(s.reportingDate)) return null
  return { dataset_snapshot_id: s.snapshot.snapshot.snapshot_id, module_version_id: s.moduleVersionId, reporting_date: s.reportingDate }
}

// --- the New-run panel: refusals and actions -------------------------------------------------

/** POST /dataset-snapshots refusals (BE-15.3, D-30 §3), with a title each. */
export const SNAPSHOT_REFUSAL_TITLES: Record<string, string> = {
  not_projected: 'An import is not projected yet',
  reconciliation_broken: "An import's reconciliation is BROKEN",
  duplicate_schema: 'Two imports of the same schema',
  ambiguous_business_key: 'A business key resolves to two instruments',
  calculations_disabled: 'Calculations are switched off',
  not_found: 'Import not found',
  forbidden: 'You may not build dataset snapshots',
  validation_error: 'The request was rejected',
}

/** POST /calculation-runs refusals (BE-15.4, D-31 §3), with a title each. */
export const RUN_REFUSAL_TITLES: Record<string, string> = {
  module_not_available: 'The module is not available',
  calculations_disabled: 'Calculations are switched off',
  idempotency_conflict: 'This run request was already used with another body',
  not_found: 'Snapshot not found',
  forbidden: 'You may not submit runs',
  validation_error: 'The request was rejected',
}

export const snapshotRefusal = (err: unknown): Refusal => refusalOf(err, 'Could not build the snapshot', SNAPSHOT_REFUSAL_TITLES)
export const runRefusal = (err: unknown): Refusal => refusalOf(err, 'Could not submit the run', RUN_REFUSAL_TITLES)

/**
 * POST /dataset-snapshots with the picked imports: 201 a new snapshot, 200 the existing one
 * with the same members (content-addressed, nothing written). A refusal is toasted with its
 * correlation ID and kept in the panel.
 */
export async function buildSnapshot(state: PanelState, dispatch: (e: PanelEvent) => void): Promise<void> {
  if (snapshotProblems(state).length > 0) return
  const import_ids = selectedImports(state)
  dispatch({ type: 'snapshot_started' })
  try {
    const { data, response } = await callWithResponse(() => api.POST('/api/v1/dataset-snapshots', { body: { import_ids } }))
    dispatch({ type: 'snapshot_built', snapshot: data, status: response.status })
  } catch (err) {
    const refusal = snapshotRefusal(err)
    toastApiError(err, refusal.title)
    dispatch({ type: 'snapshot_refused', refusal })
  }
}

/** A fresh `Idempotency-Key` for one run request (1–255 visible ASCII), as the import wizard makes them. */
export const newRunKey = (): string => `run-${newIdempotencyKey()}`

/** RFC 7240 preference for a background run (D-59 §2). */
export const RESPOND_ASYNC = 'respond-async'

/**
 * POST /calculation-runs with the panel's body and its attempt key (a retry of the same body
 * reuses it): 201 a new run, 200 the original run of this key. The run's status is the
 * server's; a refusal is toasted with its correlation ID and kept in the panel.
 * FE-33.1: with `respondAsync` (NEXT_PUBLIC_BACKGROUND_RUNS_UI, on by default) the request sends
 * `Prefer: respond-async`, and the server answers 202 with the run RUNNING, which the panel then
 * follows; without it the request is exactly Phase 32's. A 503 `workers_busy` or 429
 * `rate_limited` is a refusal with its retry time; no run was written.
 */
export async function submitRun(
  state: PanelState,
  modules: readonly ModuleVersion[],
  dispatch: (e: PanelEvent) => void,
  newKey: () => string = newRunKey,
  respondAsync: boolean = BACKGROUND_RUNS_UI_ENABLED,
): Promise<RunDetail | null> {
  const body = runBody(state)
  if (!body || submitProblems(state, modules).length > 0) return null
  const key = state.attemptKey ?? newKey()
  dispatch({ type: 'submit_started', key })
  try {
    const { data, response } = await callWithResponse(() =>
      api.POST('/api/v1/calculation-runs', { params: { header: { 'Idempotency-Key': key, ...(respondAsync ? { Prefer: RESPOND_ASYNC } : {}) } }, body }),
    )
    dispatch({ type: 'submitted', run: data, status: response.status })
    return data
  } catch (err) {
    const refusal = runRefusal(err)
    toastApiError(err, refusal.title)
    dispatch({ type: 'submit_refused', refusal })
    return null
  }
}
