import { api, call, type Schemas } from '@/api/client'
import { isMasked } from '@/components/common/MaskedValue'
import { scenarioHref } from './scenarios'

/*
 * FE-25.2: the baseline-versus-stress comparison view's pure helpers (D-46). Every payload type
 * is the generated one (src/api/schema.d.ts). The server computes every figure, delta, flag,
 * ranking and the templated summary, and masks them (D-33, D-46 §3); this file only arranges
 * what it sent. Decimal text is never parsed as a float: the heat map compares magnitudes with
 * BigInt on the exact text. Visibility is cosmetic: the server checks `calc:read` on every read
 * (403) and answers 404 `not_found` for a foreign scenario or run (D-04).
 */

export type ScenarioComparison = Schemas['ScenarioComparisonOut']
export type ComparisonCurrency = Schemas['ComparisonCurrency']
export type ComparisonRow = Schemas['ComparisonRow']
export type ComparisonCell = Schemas['ComparisonCell']
export type ComparisonFigure = Schemas['ComparisonFigure']
export type ComparisonBucket = Schemas['ComparisonBucket']
export type LargestChange = Schemas['LargestChange']
export type FirstNegativeCumulative = Schemas['FirstNegativeCumulative']
export type ComparisonSummary = Schemas['ComparisonSummary']
export type ScenarioRunPage = Schemas['ScenarioRunPage']
export type ScenarioRunSummary = Schemas['ScenarioRunOut']
export type ScenarioContributionPage = Schemas['ScenarioContributionPage']
export type ScenarioContribution = Schemas['ScenarioContributionOut']
export type PartChange = Schemas['PartChange']
type Masked = Schemas['MaskedValue']

/** A figure as the server sent it: exact decimal text, or masked. */
export type Shown = ComparisonFigure['delta']

/** The four figures of a cell. Net and cumulative are the table's metrics; inflows and outflows show on expand. */
export type FigureKey = 'inflows' | 'outflows' | 'net_gap' | 'cumulative_gap'
export type Metric = Extract<FigureKey, 'net_gap' | 'cumulative_gap'>
export const METRICS: readonly Metric[] = ['net_gap', 'cumulative_gap']
export const FIGURE_LABEL: Record<FigureKey, string> = {
  inflows: 'Inflows',
  outflows: 'Outflows',
  net_gap: 'Net gap',
  cumulative_gap: 'Cumulative gap',
}

/** GET scenario-runs `limit` maximum. */
export const SCENARIO_RUN_PAGE = 200
/** Drill-down page size (the route allows up to 500). */
export const PARTS_PAGE = 100

// --- routes -------------------------------------------------------------------------------------

/** The scenario's stressed runs, each with a link to its comparison. */
export const scenarioRunsHref = (scenarioId: string): string => `${scenarioHref(scenarioId)}/runs`

/** One scenario run's comparison (with the side-by-side "what if" against its parent version's run, when there is one). */
export const comparisonHref = (scenarioId: string, scenarioRunId: string): string => `${scenarioRunsHref(scenarioId)}/${encodeURIComponent(scenarioRunId)}`

// --- reads --------------------------------------------------------------------------------------

/** GET scenarios/{scenario_id}/comparison?scenario_run_id= (calc:read, D-46). */
export const fetchComparison = (scenarioId: string, scenarioRunId: string) => (signal: AbortSignal): Promise<ScenarioComparison> =>
  call(() =>
    api.GET('/api/v1/scenarios/{scenario_id}/comparison', {
      params: { path: { scenario_id: scenarioId }, query: { scenario_run_id: scenarioRunId } },
      signal,
    }),
  )

/** GET scenario-runs?scenario_id= (and `baseline_run_id`), newest first (calc:read). */
export const fetchScenarioRuns = (scenarioId: string, baselineRunId?: string) => (signal: AbortSignal): Promise<ScenarioRunPage> =>
  call(() =>
    api.GET('/api/v1/scenario-runs', {
      params: { query: { scenario_id: scenarioId, ...(baselineRunId ? { baseline_run_id: baselineRunId } : {}), limit: SCENARIO_RUN_PAGE, offset: 0 } },
      signal,
    }),
  )

/** What the drill-down shows: one member run's changed parts in one currency and bucket. */
export interface DrillTarget {
  currency: string
  bucketCode: string
  bucketLabel: string
  shockSubset: number[]
  combined: boolean
}

/** GET …/comparison/contributions for one member run (the combined run by default), one page. */
export const fetchParts = (scenarioId: string, scenarioRunId: string, target: Pick<DrillTarget, 'currency' | 'bucketCode' | 'shockSubset'>, offset: number) => (signal: AbortSignal): Promise<ScenarioContributionPage> =>
  call(() =>
    api.GET('/api/v1/scenarios/{scenario_id}/comparison/contributions', {
      params: {
        path: { scenario_id: scenarioId },
        query: { scenario_run_id: scenarioRunId, currency: target.currency, bucket_code: target.bucketCode, shock_subset: [...target.shockSubset], limit: PARTS_PAGE, offset },
      },
      signal,
    }),
  )

// --- columns ------------------------------------------------------------------------------------

/** "1+2": a stable key for a shock subset. */
export const subsetKey = (subset: readonly number[]): string => [...subset].sort((a, b) => a - b).join('+')

/** A column's heading: "Shock 1 alone", or "Combined (shocks 1 + 2)"; a one-shock version has one column, "Shock 1". */
export function columnLabel(c: Pick<ComparisonCell, 'combined' | 'shock_subset'>): string {
  const subset = [...c.shock_subset].sort((a, b) => a - b)
  if (c.combined) return subset.length === 1 ? `Shock ${subset[0]}` : `Combined (shocks ${subset.join(' + ')})`
  return subset.length === 1 ? `Shock ${subset[0]} alone` : `Shocks ${subset.join(' + ')}`
}

/** The table's columns: single-shock runs by shock index, then the combined run (the server's order, kept). */
export function columnsOf(comparison: Pick<ScenarioComparison, 'currencies'>): Pick<ComparisonCell, 'calculation_run_id' | 'shock_subset' | 'combined'>[] {
  const first = comparison.currencies.find((c) => c.rows.length > 0)?.rows[0]
  return first ? first.cells.map(({ calculation_run_id, shock_subset, combined }) => ({ calculation_run_id, shock_subset, combined })) : []
}

/** A row's combined cell, if the combined run succeeded. */
export const combinedCell = (row: Pick<ComparisonRow, 'cells'>): ComparisonCell | undefined => row.cells.find((c) => c.combined)

// --- exact decimal text (no float) --------------------------------------------------------------

const DECIMAL = /^([+-]?)(\d+)(?:\.(\d+))?$/

/** A clear decimal text as an exact scaled integer; null for masked or non-decimal text. */
function units(value: Shown | string): { value: bigint; scale: number } | null {
  if (typeof value !== 'string') return null
  const m = DECIMAL.exec(value.trim())
  if (!m) return null
  const [, sign, whole, frac = ''] = m
  const v = BigInt(`${whole}${frac}`)
  return { value: sign === '-' ? -v : v, scale: frac.length }
}

const TEN = BigInt(10)
const ZERO = BigInt(0)
const abs = (v: bigint) => (v < ZERO ? -v : v)
const at = (u: { value: bigint; scale: number }, scale: number) => u.value * TEN ** BigInt(scale - u.scale)

/** The sign of a figure as text: masked, zero, negative or positive. Never parsed as a number. */
export type Tone = 'masked' | 'zero' | 'negative' | 'positive'
export function toneOf(value: Shown): Tone {
  const u = units(value)
  if (u === null) return 'masked'
  return u.value === ZERO ? 'zero' : u.value < ZERO ? 'negative' : 'positive'
}

/** |a| compared with |b| on exact text: -1, 0 or 1. Masked counts as smaller than anything clear. */
export function compareMagnitude(a: Shown, b: Shown): number {
  const x = units(a)
  const y = units(b)
  if (x === null || y === null) return x === y ? 0 : x === null ? -1 : 1
  const scale = Math.max(x.scale, y.scale)
  const l = abs(at(x, scale))
  const r = abs(at(y, scale))
  return l === r ? 0 : l < r ? -1 : 1
}

/** HEAT_LEVELS shades of the heat map; 0 is no change (or masked). */
export const HEAT_LEVELS = 4

/**
 * The heat level of a delta against the largest clear magnitude in its scale: 0 for zero or
 * masked, else the quartile ceiling 1..4 of |delta| / |max|, computed exactly with BigInt.
 */
export function heatLevel(delta: Shown, max: Shown | null): number {
  const d = units(delta)
  const m = max === null ? null : units(max)
  if (d === null || d.value === ZERO || m === null || m.value === ZERO) return 0
  const scale = Math.max(d.scale, m.scale)
  const dv = abs(at(d, scale)) * BigInt(HEAT_LEVELS)
  const mv = abs(at(m, scale))
  for (let k = 1; k <= HEAT_LEVELS; k++) if (dv <= mv * BigInt(k)) return k
  return HEAT_LEVELS
}

/** The largest clear |delta| of one metric in one currency, over every member: the heat scale. Null when none is clear. */
export function heatScale(currency: Pick<ComparisonCurrency, 'rows'>, metric: Metric): Shown | null {
  let max: Shown | null = null
  for (const row of currency.rows)
    for (const cell of row.cells) {
      const d = cell[metric].delta
      if (toneOf(d) === 'masked') continue
      if (max === null || compareMagnitude(d, max) > 0) max = d
    }
  return max
}

/** A delta as display text: thousands grouped on the string, a "+" on increases. Masked returns null (render MaskedValue). */
export function deltaText(value: Shown): string | null {
  if (typeof value !== 'string') return null
  const m = DECIMAL.exec(value.trim())
  if (!m) return value
  const [, , whole, frac] = m
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  const tone = toneOf(value)
  const prefix = tone === 'negative' ? '-' : tone === 'positive' ? '+' : ''
  return `${prefix}${grouped}${frac !== undefined ? `.${frac}` : ''}`
}

// --- flags --------------------------------------------------------------------------------------

/** A server flag: `null` means it reads a masked figure, so it is unknown, never "no". */
export type FlagState = 'yes' | 'no' | 'unknown'
export const flagState = (flag: boolean | null | undefined): FlagState => (flag === true ? 'yes' : flag === false ? 'no' : 'unknown')

/** A cell's turns-negative flag for the metric shown. */
export const turnsNegative = (cell: Pick<ComparisonCell, 'turns_negative_net' | 'turns_negative_cumulative'>, metric: Metric): FlagState =>
  flagState(metric === 'net_gap' ? cell.turns_negative_net : cell.turns_negative_cumulative)

/** The buckets of a currency that break only when the shocks are combined (`true` only; `null` is unknown). */
export const combinedOnlyBuckets = (currency: Pick<ComparisonCurrency, 'rows'>): string[] =>
  currency.rows.filter((r) => r.breaks_only_combined === true).map((r) => r.bucket_code)

/** Rows whose combined-only flag is unknown because it reads a masked figure. */
export const unknownCombinedOnly = (currency: Pick<ComparisonCurrency, 'rows'>): string[] =>
  currency.rows.filter((r) => r.breaks_only_combined === null).map((r) => r.bucket_code)

// --- first negative cumulative bucket -----------------------------------------------------------

/** A first-negative bucket code in words: the code, "never negative" (null) or masked (render MaskedValue). */
export function firstNegativeText(code: string | Masked | null): string | null {
  if (code === null) return 'never negative'
  return isMasked(code) ? null : code
}

/** The combined run's first negative cumulative bucket (one per currency), as the headline. */
export const combinedFirstNegative = (currency: Pick<ComparisonCurrency, 'first_negative_cumulative'>): FirstNegativeCumulative | undefined =>
  currency.first_negative_cumulative.find((f) => f.combined)

// --- buckets ------------------------------------------------------------------------------------

/** "(7, 30] days", "(90, ∞) days". */
export function bucketDays(b: Pick<ComparisonBucket, 'lower_exclusive_days' | 'upper_inclusive_days'>): string {
  return b.upper_inclusive_days === null ? `(${b.lower_exclusive_days}, ∞) days` : `(${b.lower_exclusive_days}, ${b.upper_inclusive_days}] days`
}

// --- the drill-down -----------------------------------------------------------------------------

export const CHANGE_TEXT: Record<PartChange, string> = {
  MOVED: 'Moved',
  STAYED: 'Stayed',
  ADDED: 'Added',
  UNCHANGED: 'Unchanged',
}
export const CHANGE_VARIANT: Record<PartChange, 'warning' | 'secondary' | 'destructive' | 'outline'> = {
  MOVED: 'warning',
  STAYED: 'secondary',
  ADDED: 'destructive',
  UNCHANGED: 'outline',
}

/** How many parts of a page did each thing (counts are public, D-46 §3). */
export function changeCounts(items: readonly Pick<ScenarioContribution, 'change'>[]): Record<PartChange, number> {
  const out: Record<PartChange, number> = { MOVED: 0, STAYED: 0, ADDED: 0, UNCHANGED: 0 }
  for (const i of items) out[i.change] += 1
  return out
}

/** "Day 3 → day 17 · bucket 1 → 2", or "Day 3 · bucket 1" when the part did not move. */
export function partMove(p: Pick<ScenarioContribution, 'days_before' | 'days_after' | 'bucket_code_before' | 'bucket_code_after'>): string {
  const days = p.days_before === p.days_after ? `day ${p.days_before}` : `day ${p.days_before} → day ${p.days_after}`
  const bucket = p.bucket_code_before === p.bucket_code_after ? `bucket ${p.bucket_code_before}` : `bucket ${p.bucket_code_before} → ${p.bucket_code_after}`
  return `${days[0].toUpperCase()}${days.slice(1)} · ${bucket}`
}

// --- side by side: a version and its parent ("what if") ------------------------------------------

/**
 * The parent version's newest scenario run on the same baseline, or null. The comparison is only
 * like for like when both ran on one baseline (identical pins, D-45 §1).
 */
export function parentRunOf(runs: readonly ScenarioRunSummary[], parentVersion: number | null | undefined, baselineRunId: string): ScenarioRunSummary | null {
  if (parentVersion === null || parentVersion === undefined) return null
  const candidates = runs.filter((r) => r.version === parentVersion && r.baseline_run_id === baselineRunId)
  candidates.sort((a, b) => (a.created_at < b.created_at ? 1 : a.created_at > b.created_at ? -1 : 0))
  return candidates[0] ?? null
}

/** One row of the side by side: a bucket's combined cell under the version and under its parent. */
export interface WhatIfRow {
  currency: string
  bucketCode: string
  bucketLabel: string
  child: ComparisonCell | undefined
  parent: ComparisonCell | undefined
}

/** Aligns the combined cells of two comparisons by currency and bucket (the child's bucket order, then any of the parent's). */
export function whatIfRows(child: Pick<ScenarioComparison, 'currencies'>, parent: Pick<ScenarioComparison, 'currencies'>): WhatIfRow[] {
  const currencies = [...new Set([...child.currencies.map((c) => c.currency), ...parent.currencies.map((c) => c.currency)])].sort()
  const out: WhatIfRow[] = []
  for (const currency of currencies) {
    const c = child.currencies.find((x) => x.currency === currency)
    const p = parent.currencies.find((x) => x.currency === currency)
    const rows = [...(c?.rows ?? [])]
    for (const r of p?.rows ?? []) if (!rows.some((x) => x.bucket_code === r.bucket_code)) rows.push(r)
    for (const r of rows)
      out.push({
        currency,
        bucketCode: r.bucket_code,
        bucketLabel: r.bucket_label,
        child: c && combinedCell(c.rows.find((x) => x.bucket_code === r.bucket_code) ?? { cells: [] }),
        parent: p && combinedCell(p.rows.find((x) => x.bucket_code === r.bucket_code) ?? { cells: [] }),
      })
  }
  return out
}
