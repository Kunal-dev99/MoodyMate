/**
 * FE-29.1: the form checks every area shares. They are cosmetic (D-04): the server validates
 * again and its refusal is shown as it came.
 */

/** The decision and review comment limit every `*Decision` / `DriftReviewIn` schema declares. */
export const COMMENT_MAX = 1000

/** A text's length as the server counts it: code points after trimming (Pydantic `max_length`). */
export const textLength = (text: string): number => [...text.trim()].length

export interface CommentOptions {
  /** An empty comment is a problem (default true). */
  required?: boolean
  /** The schema's `maxLength` (default `COMMENT_MAX`). */
  max?: number
  /** "At most 1,000 characters (1,001 now)." (default) or the short "At most 1000 characters." */
  style?: 'counted' | 'short'
}

/**
 * Why a comment cannot be sent yet, or null. The server strips surrounding whitespace too.
 * `options` may be a boolean, read as `required`.
 */
export function commentProblem(text: string, options: CommentOptions | boolean = {}): string | null {
  const { required = true, max = COMMENT_MAX, style = 'counted' } = typeof options === 'boolean' ? { required: options } : options
  const n = textLength(text)
  if (n === 0) return required ? 'A comment is required.' : null
  if (n <= max) return null
  return style === 'short' ? `At most ${max} characters.` : `At most ${max.toLocaleString('en')} characters (${n.toLocaleString('en')} now).`
}

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/

/**
 * A real calendar date written YYYY-MM-DD, as the API's `date` fields take it (year 1 to 9999,
 * the Gregorian leap rule). No time zone arithmetic: the parts are checked.
 */
export function isIsoDate(text: string): boolean {
  const m = ISO_DATE.exec(text)
  if (!m) return false
  const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])]
  if (y < 1 || mo < 1 || mo > 12 || d < 1) return false
  const leap = (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][mo - 1]
  return d <= days
}

export interface DateOptions {
  /** An empty value is a problem (default true). */
  required?: boolean
  /** The problem for an empty value (default "A date is required."). */
  missing?: string
}

/** Why a date field cannot be sent yet, or null. */
export function dateProblem(text: string, options: DateOptions = {}): string | null {
  const { required = true, missing = 'A date is required.' } = options
  if (!text) return required ? missing : null
  return isIsoDate(text) ? null : 'A valid date, YYYY-MM-DD.'
}
