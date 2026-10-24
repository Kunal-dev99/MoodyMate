/**
 * Demo mode for the Agentic Stress-Test Explorer: a mock agent (no external AI call) and a
 * deterministic in-browser maturity-ladder overlay on a fixed ILLUSTRATIVE / SYNTHETIC cash-flow
 * set. It previews the Phase 25-27 workflow (run against baseline, compare by bucket, explain)
 * until the real engine and agent ship. This is browser arithmetic on synthetic data, not the
 * calculation engine. Amounts are integer hundredths of ₹ lakh. The baseline reproduces the design
 * doc's §5 worked example.
 */

/** Shown on both demo pages (FE-28.4): the numbers on them do not come from the calculation engine. */
export const DEMO_BANNER = 'Illustrative mock: browser arithmetic on synthetic data, not the calculation engine'

/** Label for the per-run hash: a DJB2 string hash, not the backend's SHA-256 result fingerprint. */
export const CHECKSUM_LABEL = 'illustrative checksum (not a SHA-256 result fingerprint)'

/** Zero guard for every ratio on the demo pages: null when the denominator is 0 or not finite. */
export function ratio(num: number, den: number): number | null {
  if (den === 0 || !Number.isFinite(den) || !Number.isFinite(num)) return null
  return num / den
}

export type TemplateCode = 'DELAYED_INFLOWS' | 'DEPOSIT_WITHDRAWAL' | 'FUNDING_UNAVAILABLE'
export type FieldKey = 'fraction' | 'days'

export interface DemoShock {
  t: TemplateCode
  fraction: number
  days?: number
  /** Parameters the agent filled with a default the analyst has not confirmed yet. */
  defaults: FieldKey[]
}

interface CashFlow {
  id: string
  type: 'LOAN' | 'DEPOSIT' | 'FUNDING' | 'OTHER'
  dir: 'IN' | 'OUT'
  day: number
  amt: number
  desc: string
}

export interface Flow extends CashFlow {
  moved?: boolean
  effects: { shock: number; text: string }[]
}

export interface LadderRow {
  label: string
  inflows: number
  outflows: number
  net: number
  cumulative: number
}

export const BUCKETS = [
  { label: 'Up to 7 days', hi: 7 },
  { label: '8–30 days', hi: 30 },
  { label: '31–90 days', hi: 90 },
  { label: '91–180 days', hi: 180 },
  { label: 'Over 180 days', hi: Number.POSITIVE_INFINITY },
]

const L = (lakh: number) => lakh * 100

const CASH_FLOWS: CashFlow[] = [
  { id: 'CF-001', type: 'LOAN', dir: 'IN', day: 3, amt: L(30), desc: 'Term loan repayment' },
  { id: 'CF-002', type: 'LOAN', dir: 'IN', day: 6, amt: L(20), desc: 'SME loan instalment' },
  { id: 'CF-003', type: 'FUNDING', dir: 'IN', day: 2, amt: L(20), desc: 'Interbank credit line draw' },
  { id: 'CF-004', type: 'OTHER', dir: 'IN', day: 5, amt: L(30), desc: 'Bond coupon' },
  { id: 'CF-101', type: 'DEPOSIT', dir: 'OUT', day: 1, amt: L(35), desc: 'Retail deposit maturity' },
  { id: 'CF-102', type: 'DEPOSIT', dir: 'OUT', day: 4, amt: L(25), desc: 'Corporate deposit maturity' },
  { id: 'CF-103', type: 'OTHER', dir: 'OUT', day: 7, amt: L(20), desc: 'Operating expenses' },
  { id: 'CF-005', type: 'LOAN', dir: 'IN', day: 9, amt: L(18), desc: 'Term loan repayment' },
  { id: 'CF-006', type: 'LOAN', dir: 'IN', day: 14, amt: L(12), desc: 'Retail loan EMI pool' },
  { id: 'CF-007', type: 'OTHER', dir: 'IN', day: 20, amt: L(30), desc: 'Bond maturity' },
  { id: 'CF-104', type: 'DEPOSIT', dir: 'OUT', day: 15, amt: L(30), desc: 'Term deposit maturity' },
  { id: 'CF-105', type: 'DEPOSIT', dir: 'OUT', day: 28, amt: L(20), desc: 'Corporate deposit maturity' },
  { id: 'CF-106', type: 'OTHER', dir: 'OUT', day: 18, amt: L(20), desc: 'Borrowing repayment' },
  { id: 'CF-008', type: 'LOAN', dir: 'IN', day: 45, amt: L(25), desc: 'Term loan repayment' },
  { id: 'CF-009', type: 'LOAN', dir: 'IN', day: 70, amt: L(15), desc: 'SME loan instalment' },
  { id: 'CF-010', type: 'OTHER', dir: 'IN', day: 60, amt: L(50), desc: 'Government bond maturity' },
  { id: 'CF-107', type: 'DEPOSIT', dir: 'OUT', day: 40, amt: L(25), desc: 'Term deposit maturity' },
  { id: 'CF-108', type: 'OTHER', dir: 'OUT', day: 70, amt: L(15), desc: 'Borrowing repayment' },
  { id: 'CF-011', type: 'LOAN', dir: 'IN', day: 120, amt: L(35), desc: 'Term loan repayment' },
  { id: 'CF-012', type: 'OTHER', dir: 'IN', day: 150, amt: L(40), desc: 'Bond maturity' },
  { id: 'CF-109', type: 'DEPOSIT', dir: 'OUT', day: 100, amt: L(30), desc: 'Term deposit maturity' },
  { id: 'CF-110', type: 'OTHER', dir: 'OUT', day: 160, amt: L(25), desc: 'Borrowing repayment' },
]

export const TEMPLATES: Record<TemplateCode, { name: string; fields: [FieldKey, string, number][] }> = {
  DELAYED_INFLOWS: { name: 'Delayed loan repayments', fields: [['fraction', 'Share delayed (%)', 20], ['days', 'Delay (days)', 14]] },
  DEPOSIT_WITHDRAWAL: { name: 'Increased deposit withdrawals', fields: [['fraction', 'Increase in outflows (%)', 15]] },
  FUNDING_UNAVAILABLE: { name: 'Funding source unavailable', fields: [['fraction', 'Share unavailable (%)', 100], ['days', 'Unavailable for (days)', 7]] },
}

/** Hundredths of ₹ lakh as text with Indian digit grouping, e.g. 123456 -> "1,234.56". */
export function money(n: number): string {
  const sign = n < 0 ? '-' : ''
  const a = Math.abs(n)
  return `${sign}${Math.floor(a / 100).toLocaleString('en-IN')}.${String(a % 100).padStart(2, '0')}`
}
export const signed = (n: number) => (n === 0 ? '0.00' : `${n > 0 ? '+' : ''}${money(n)}`)

const bucketOf = (day: number) => BUCKETS.findIndex((b) => day <= b.hi)
/** pct has at most two decimals (validated); the share is rounded to the nearest hundredth. */
const share = (amt: number, pct: number) => Math.round((amt * Math.round(pct * 100)) / 10000)

export function applyShocks(shocks: DemoShock[]): Flow[] {
  const flows: Flow[] = CASH_FLOWS.map((c) => ({ ...c, effects: [] }))
  const added: Flow[] = []
  shocks.forEach((s, i) => {
    for (const f of [...flows, ...added]) {
      if (s.t === 'DELAYED_INFLOWS' && f.type === 'LOAN' && f.dir === 'IN' && !f.moved) {
        const m = share(f.amt, s.fraction)
        if (!m) continue
        const days = s.days ?? 0
        f.amt -= m
        added.push({ ...f, id: `${f.id} (moved part)`, amt: m, day: f.day + days, moved: true, effects: [{ shock: i, text: `${money(m)} moved from day ${f.day} to day ${f.day + days}` }] })
        f.effects.push({ shock: i, text: `${money(m)} of ${money(f.amt + m)} delayed ${days} days` })
      } else if (s.t === 'DEPOSIT_WITHDRAWAL' && f.type === 'DEPOSIT' && f.dir === 'OUT') {
        const x = share(f.amt, s.fraction)
        f.amt += x
        f.effects.push({ shock: i, text: `outflow +${money(x)}` })
      } else if (s.t === 'FUNDING_UNAVAILABLE' && f.type === 'FUNDING' && f.dir === 'IN' && !f.moved && f.day > 0 && f.day <= (s.days ?? 0)) {
        const m = share(f.amt, s.fraction)
        if (!m) continue
        const until = (s.days ?? 0) + 1
        f.amt -= m
        added.push({ ...f, id: `${f.id} (moved part)`, amt: m, day: until, moved: true, effects: [{ shock: i, text: `${money(m)} unavailable until day ${until}` }] })
        f.effects.push({ shock: i, text: `${money(m)} unavailable for ${s.days} days` })
      }
    }
  })
  return [...flows, ...added]
}

export function ladder(flows: Flow[]): LadderRow[] {
  const rows = BUCKETS.map((b) => ({ label: b.label, inflows: 0, outflows: 0, net: 0, cumulative: 0 }))
  for (const f of flows) {
    const r = rows[bucketOf(f.day)]
    if (f.dir === 'IN') r.inflows += f.amt
    else r.outflows += f.amt
  }
  let cum = 0
  for (const r of rows) {
    r.net = r.inflows - r.outflows
    cum += r.net
    r.cumulative = cum
  }
  return rows
}

export const BASELINE = ladder(applyShocks([]))

/** Illustrative checksum of a ladder (DJB2 over its JSON). Not a SHA-256 result fingerprint. */
export function checksum(rows: LadderRow[]): string {
  const s = JSON.stringify(rows)
  let h = 5381
  for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) >>> 0
  return h.toString(16).padStart(8, '0')
}

export interface Proposal {
  shocks: DemoShock[]
  ask: string | null
  trace: { tag: string; text: string }[]
}

/** The mock agent: maps plain English to the three approved templates only. */
export function propose(text: string): Proposal {
  const t = ` ${text.toLowerCase()} `
  const parts = t.split(/\band\b|,|;|\bplus\b|\balso\b/).filter((p) => p.trim())
  const pct = (p: string) => {
    const m = p.match(/(\d+(?:\.\d+)?)\s*(%|percent|per cent)/)
    return m ? parseFloat(m[1]) : null
  }
  const days = (p: string) => {
    let m = p.match(/(\d+)\s*(?:calendar\s*)?days?/)
    if (m) return parseInt(m[1], 10)
    if (/two weeks|2 weeks|fortnight/.test(p)) return 14
    if (/one week|a week|1 week/.test(p)) return 7
    if (/a month|one month|1 month/.test(p)) return 30
    m = p.match(/(\d+)\s*weeks?/)
    return m ? parseInt(m[1], 10) * 7 : null
  }
  const trace = [{ tag: 'interpret', text: `Read the request and split it into ${parts.length} clause${parts.length > 1 ? 's' : ''}` }]
  const shocks: DemoShock[] = []
  for (const p of parts) {
    let type: TemplateCode | null = null
    if (/loan|repayment|receivable|inflow|instal/.test(p)) type = 'DELAYED_INFLOWS'
    else if (/deposit|withdraw|run-?off|outflow/.test(p)) type = 'DEPOSIT_WITHDRAWAL'
    else if (/funding|facility|credit line|interbank|unavailable|borrow/.test(p)) type = 'FUNDING_UNAVAILABLE'
    if (!type) continue
    const s: DemoShock = { t: type, fraction: 0, defaults: [] }
    for (const [k, , def] of TEMPLATES[type].fields) {
      let v = k === 'fraction' ? pct(p) : days(p)
      if (v === null) {
        v = def
        s.defaults.push(k)
      }
      s[k] = v
    }
    shocks.push(s)
    trace.push({ tag: 'select', text: `Matched “${p.trim()}” to the ${TEMPLATES[type].name.toLowerCase()} template` })
  }
  for (const s of shocks) {
    if (s.defaults.length) {
      trace.push({ tag: 'default', text: `${TEMPLATES[s.t].name}: ${s.defaults.map((k) => (k === 'days' ? 'days' : 'percentage')).join(' and ')} not stated, proposed a default for you to confirm` })
    }
  }
  if (shocks.length) trace.push({ tag: 'validate', text: 'Checked bounds, units and that matching cash flows exist in the baseline snapshot' })
  const ask = shocks.length
    ? null
    : 'I can model delayed loan repayments, higher deposit withdrawals, or a funding source becoming unavailable. Which one do you want to test, and by how much?'
  return { shocks, ask, trace }
}

export function problems(s: DemoShock): Partial<Record<FieldKey, string>> {
  const p: Partial<Record<FieldKey, string>> = {}
  if (!(s.fraction > 0 && s.fraction <= 100)) p.fraction = 'Must be above 0% and at most 100%.'
  if (s.days !== undefined && !(Number.isInteger(s.days) && s.days >= 1 && s.days <= 3650)) p.days = 'Whole days between 1 and 3650.'
  return p
}

export function describe(s: DemoShock): string {
  if (s.t === 'DELAYED_INFLOWS') return `${s.fraction}% of each loan repayment arrives ${s.days} calendar days later. Totals do not change; timing does.`
  if (s.t === 'DEPOSIT_WITHDRAWAL') return `Every deposit outflow is ${s.fraction}% larger than contracted.`
  return `${s.fraction}% of the interbank credit line due in the next ${s.days} days is unavailable until day ${(s.days ?? 0) + 1}.`
}

/** Parse an edited field: whole days, or a percentage with at most two decimals; NaN if invalid. */
export function parseField(k: FieldKey, raw: string): number {
  const v = raw.trim()
  if (k === 'days') return /^\d+$/.test(v) ? parseInt(v, 10) : Number.NaN
  return /^\d+(\.\d{1,2})?$/.test(v) ? parseFloat(v) : Number.NaN
}

export const EXAMPLES = [
  'Stress-test our 30-day liquidity if 20% of loan repayments are delayed by 14 days',
  '20% of loan repayments delayed two weeks, and deposit withdrawals up 15%, and the interbank line unavailable for 7 days',
  'What if deposits run off faster?',
  'Show me something scary',
]
