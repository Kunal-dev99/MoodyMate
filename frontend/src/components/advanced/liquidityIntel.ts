/**
 * Liquidity Intelligence prototype: "Why did the LCR change between the last two runs?" plus an
 * optional 25% deposit-outflow shock. All data is ILLUSTRATIVE / SYNTHETIC and the agent is a mock;
 * no API calls. Amounts are integer hundredths of ₹ crore and ratios are integer ten-thousandths
 * of a percent, so every figure (and the attribution bridge) reconciles exactly.
 */

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
  /** LCR in ten-thousandths of a percent (1500000 = 150.0000%). */
  lcr: number
}

const C = (crore: number) => Math.round(crore * 100)

/** The pinned illustrative methodology: inflows recognised up to 75% of total outflows. */
export const INFLOW_CAP_BP = 7500

export function calculate(i: LcrInputs): LcrResult {
  const totalOutflows = i.depositOutflows + i.otherOutflows
  const inflowCap = Math.floor((totalOutflows * INFLOW_CAP_BP) / 10000)
  const recognisedInflows = Math.min(i.inflows, inflowCap)
  const netOutflows = totalOutflows - recognisedInflows
  const lcr = Math.round((i.hqla * 1_000_000) / netOutflows)
  return { ...i, totalOutflows, inflowCap, recognisedInflows, netOutflows, lcr }
}

export interface Run {
  id: string
  date: string
  snapshot: string
  fingerprint: string
  rules: string
  inputs: LcrInputs
}

export const PREVIOUS: Run = {
  id: 'RUN-LCR-0412',
  date: '2026-09-30',
  snapshot: 'SNAP-0930-A7',
  fingerprint: '9f3c1a07',
  rules: 'lcr_runoff v3 · hqla_haircuts v2 · inflow_cap v1',
  inputs: { hqla: C(150), depositOutflows: C(110), otherOutflows: C(50), inflows: C(60) },
}
export const LATEST: Run = {
  id: 'RUN-LCR-0419',
  date: '2026-10-31',
  snapshot: 'SNAP-1031-C2',
  fingerprint: '4be0d915',
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
  from: number
  to: number
  contribution: number
}

/** Input-replacement bridge (methodology bridge_v1, fixed order): previous -> latest, one group at a time. */
export function bridge(prev: LcrInputs, latest: LcrInputs): { steps: BridgeStep[]; reconciles: boolean } {
  let cur = { ...prev }
  let ratio = calculate(cur).lcr
  const steps: BridgeStep[] = []
  for (const d of DRIVERS) {
    cur = { ...cur, [d.key]: latest[d.key] }
    const next = calculate(cur).lcr
    steps.push({ label: d.label, from: ratio, to: next, contribution: next - ratio })
    ratio = next
  }
  steps.push({ label: 'Rule / methodology versions', from: ratio, to: ratio, contribution: 0 })
  const total = calculate(latest).lcr - calculate(prev).lcr
  return { steps, reconciles: steps.reduce((a, s) => a + s.contribution, 0) === total }
}

/** Scenario: eligible deposit-outflow amounts x (1 + shock), before the configured runoff methodology. */
export function shockDeposits(i: LcrInputs, shockPct: number): LcrInputs {
  return { ...i, depositOutflows: i.depositOutflows + Math.round((i.depositOutflows * shockPct) / 100) }
}

export const DEPOSIT_EVIDENCE = [
  { segment: 'Corporate operational deposits', prev: C(42), latest: C(53), runoff: '25%', source: 'IMP-1031-DEP · rows 1–184' },
  { segment: 'Retail less-stable deposits', prev: C(38), latest: C(44), runoff: '10%', source: 'IMP-1031-DEP · rows 185–2210' },
  { segment: 'Retail stable deposits', prev: C(19), latest: C(20), runoff: '5%', source: 'IMP-1031-DEP · rows 2211–5980' },
  { segment: 'Non-operational wholesale', prev: C(11), latest: C(11), runoff: '40%', source: 'IMP-1031-DEP · rows 5981–6050' },
]

export const crore = (n: number) => `₹${(n / 100).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} Cr`
export const pct = (n: number) => `${(n / 10000).toFixed(4)}%`
export const pp = (n: number) => `${n > 0 ? '+' : n < 0 ? '−' : ''}${Math.abs(n / 10000).toFixed(4)} pp`
