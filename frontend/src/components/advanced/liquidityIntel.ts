/**
 * Liquidity Intelligence prototype: "Why did the LCR change between the last two runs?" plus an
 * optional 25% deposit-outflow shock. All data is ILLUSTRATIVE / SYNTHETIC and the agent is a mock;
 * no API calls. This is browser arithmetic, not the calculation engine. Amounts are integer
 * hundredths of ₹ crore and ratios are integer ten-thousandths of a percent. In this mock the
 * deposit outflows are the deposit balances: no run-off rate is applied to them.
 */

/** Shown on the page (FE-28.4, FE-35.6): its numbers do not come from the calculation engine. */
export const DEMO_BANNER = 'Illustrative mock: browser arithmetic on synthetic data, not the calculation engine'

/** Zero guard for every ratio on the page: null when the denominator is 0 or not finite. */
export function ratio(num: number, den: number): number | null {
  if (den === 0 || !Number.isFinite(den) || !Number.isFinite(num)) return null
  return num / den
}

export interface LcrInputs {
  hqla: number
  depositOutflows: number
  otherOutflows: number
  inflows: number
}

export interface LcrResult extends LcrInputs {
  totalOutflows: number
  inflowCap: number
  recognisedInflows: number
  netOutflows: number
  /** LCR in ten-thousandths of a percent (1500000 = 150.0000%); null when net outflows ≤ 0. */
  lcr: number | null
}

const C = (crore: number) => Math.round(crore * 100)

/** The pinned illustrative methodology: inflows recognised up to 75% of total outflows. */
export const INFLOW_CAP_BP = 7500

export function calculate(i: LcrInputs): LcrResult {
  const totalOutflows = i.depositOutflows + i.otherOutflows
  const inflowCap = Math.floor((totalOutflows * INFLOW_CAP_BP) / 10000)
  const recognisedInflows = Math.min(i.inflows, inflowCap)
  const netOutflows = totalOutflows - recognisedInflows
  // Zero guard: with no positive net outflow the ratio is not computable, never Infinity or NaN.
  const lcr = netOutflows > 0 ? Math.round((i.hqla * 1_000_000) / netOutflows) : null
  return { ...i, totalOutflows, inflowCap, recognisedInflows, netOutflows, lcr }
}

export interface Run {
  id: string
  date: string
  snapshot: string
  rules: string
  inputs: LcrInputs
}

export const PREVIOUS: Run = {
  id: 'RUN-LCR-0412',
  date: '2026-09-30',
  snapshot: 'SNAP-0930-A7',
  rules: 'lcr_runoff v3 · hqla_haircuts v2 · inflow_cap v1',
  inputs: { hqla: C(150), depositOutflows: C(110), otherOutflows: C(50), inflows: C(60) },
}
export const LATEST: Run = {
  id: 'RUN-LCR-0419',
  date: '2026-10-31',
  snapshot: 'SNAP-1031-C2',
  rules: 'lcr_runoff v3 · hqla_haircuts v2 · inflow_cap v1',
  inputs: { hqla: C(150), depositOutflows: C(128), otherOutflows: C(54), inflows: C(62) },
}

export const DRIVERS: { key: keyof LcrInputs; label: string }[] = [
  { key: 'hqla', label: 'HQLA (numerator)' },
  { key: 'depositOutflows', label: 'Deposit outflows' },
  { key: 'otherOutflows', label: 'Other outflows' },
  { key: 'inflows', label: 'Recognised inflows' },
]

export interface BridgeStep {
  label: string
  from: number | null
  to: number | null
  /** null when either side of the step is not computable. */
  contribution: number | null
}

/** Difference of two LCRs; null when either is not computable. */
export const lcrDelta = (from: number | null, to: number | null): number | null => (from === null || to === null ? null : to - from)

/** Input-replacement bridge (methodology bridge_v1, fixed order): previous -> latest, one group at a time. */
export function bridge(prev: LcrInputs, latest: LcrInputs): { steps: BridgeStep[]; reconciles: boolean } {
  let cur = { ...prev }
  let ratio = calculate(cur).lcr
  const steps: BridgeStep[] = []
  for (const d of DRIVERS) {
    cur = { ...cur, [d.key]: latest[d.key] }
    const next = calculate(cur).lcr
    steps.push({ label: d.label, from: ratio, to: next, contribution: lcrDelta(ratio, next) })
    ratio = next
  }
  steps.push({ label: 'Rule / methodology versions', from: ratio, to: ratio, contribution: ratio === null ? null : 0 })
  const total = lcrDelta(calculate(prev).lcr, calculate(latest).lcr)
  const parts = steps.map((s) => s.contribution)
  const reconciles = total !== null && parts.every((c) => c !== null) && parts.reduce<number>((a, c) => a + (c ?? 0), 0) === total
  return { steps, reconciles }
}

/** Scenario: eligible deposit-outflow amounts x (1 + shock), before the configured runoff methodology. */
export function shockDeposits(i: LcrInputs, shockPct: number): LcrInputs {
  return { ...i, depositOutflows: i.depositOutflows + Math.round((i.depositOutflows * shockPct) / 100) }
}

/** Column label: the rates are shown for context only; the mock's deposit outflows are balances. */
export const RUNOFF_COLUMN = 'Illustrative run-off (not applied)'
export const BALANCES_NOTE = 'In this mock, deposit outflows are the deposit balances: the run-off rates shown are illustrative and are not applied to any figure.'

export const DEPOSIT_EVIDENCE = [
  { segment: 'Corporate operational deposits', prev: C(42), latest: C(53), runoff: '25%', source: 'IMP-1031-DEP · rows 1–184' },
  { segment: 'Retail less-stable deposits', prev: C(38), latest: C(44), runoff: '10%', source: 'IMP-1031-DEP · rows 185–2210' },
  { segment: 'Retail stable deposits', prev: C(19), latest: C(20), runoff: '5%', source: 'IMP-1031-DEP · rows 2211–5980' },
  { segment: 'Non-operational wholesale', prev: C(11), latest: C(11), runoff: '40%', source: 'IMP-1031-DEP · rows 5981–6050' },
]

export const crore = (n: number) => `₹${(n / 100).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} Cr`
export const NOT_COMPUTABLE = 'not computable'
export const pct = (n: number | null) => (n === null ? NOT_COMPUTABLE : `${(n / 10000).toFixed(4)}%`)
export const pp = (n: number | null) => (n === null ? NOT_COMPUTABLE : `${n > 0 ? '+' : n < 0 ? '−' : ''}${Math.abs(n / 10000).toFixed(4)} pp`)

/** "LCR fell/rose … pp, from … to …", derived from the two computed ratios, never hard-coded. */
export function movementText(from: number | null, to: number | null): string {
  const d = lcrDelta(from, to)
  if (d === null) return `LCR movement ${NOT_COMPUTABLE} (${pct(from)} → ${pct(to)})`
  if (d === 0) return `LCR unchanged at ${pct(to)}`
  return `LCR ${d < 0 ? 'fell' : 'rose'} ${Math.abs(d / 10000).toFixed(4)} pp, from ${pct(from)} to ${pct(to)}`
}

/** The deposit segment with the largest absolute change (ties: first listed). */
export function largestDepositChange(rows: typeof DEPOSIT_EVIDENCE) {
  return rows.reduce((best, r) => (Math.abs(r.latest - r.prev) > Math.abs(best.latest - best.prev) ? r : best), rows[0])
}
