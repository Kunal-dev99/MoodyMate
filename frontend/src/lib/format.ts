/**
 * Display formatters shared across the app (demo polish P-5 / P-7).
 *
 * - `formatTimestamp` renders every timestamp the same way, in UTC, so a reviewer in
 *   another timezone reads the same moment as the audit log: `19 Oct 2026, 09:15 UTC`.
 * - `groupDecimal` / `formatMoney` group thousands on the exact decimal string the API
 *   returns. They never parse to a JS number, so no precision is lost to float.
 */

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'] as const

const pad2 = (n: number) => String(n).padStart(2, '0')

/** `19 Oct 2026, 09:15 UTC`. Null, undefined or an unparseable value renders as an em dash. */
export function formatTimestamp(value: string | Date | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—'
  const d = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(d.getTime())) return typeof value === 'string' ? value : '—'
  return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}, ${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())} UTC`
}

const DECIMAL = /^([+-]?)(\d+)(?:\.(\d+))?$/

/**
 * Group the integer part of an exact decimal string with commas: `4443500.86` →
 * `4,443,500.86`. String-only: the fraction is kept digit for digit. Anything that is
 * not a plain decimal (exponent notation, already grouped, empty) is returned unchanged.
 */
export function groupDecimal(value: string): string {
  const m = DECIMAL.exec(value.trim())
  if (!m) return value
  const [, sign, int, frac] = m
  const grouped = int.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  return `${sign}${grouped}${frac !== undefined ? `.${frac}` : ''}`
}

/** `4,443,500.86 EUR`. The currency is appended only when given. */
export function formatMoney(amount: string, currency?: string | null): string {
  const grouped = groupDecimal(amount)
  return currency ? `${grouped} ${currency}` : grouped
}
