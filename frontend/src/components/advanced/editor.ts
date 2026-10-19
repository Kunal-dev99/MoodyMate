import type { Schemas } from '@/api/client'
import { isMasked } from '@/components/common/MaskedValue'
import { DESCRIPTION_MAX, NAME_MAX, SUMMARY_MAX, fractionToPercent, type Shock, type TemplateField, type TemplateOut, type ScenarioTemplate } from './scenarios'

/*
 * FE-24.2: the scenario editor's pure model. Every field and bound comes from GET
 * scenario-templates (D-44 §2-4); nothing here invents a template or a limit. A fraction is
 * typed as a percentage and stored as exact text at scale 4 ("20" -> "0.2000"): the decimal
 * point is moved in the text, never through a binary float. There is no formula field: a value
 * that is not a plain number is refused here and, again, by the server (422 invalid_shock).
 */

/** One shock being edited: the text of each field (empty while unset) and the picked instruments. */
export interface ShockDraft {
  /** A local key for React lists; never sent. */
  key: string
  template: ScenarioTemplate
  parameters: Record<string, string>
  selector: Record<string, string>
  instrumentKeys: string[]
  /**
   * Picked instruments of a stored shock that the server masked for this caller (D-33). Shown as
   * masked, never sent back: a write takes plain keys only, so they block the shock until removed.
   */
  maskedKeys: Schemas['MaskedValue'][]
}

/** Problems by field path ("parameters.affected_fraction", "selector.due_to_day"). */
export type FieldProblems = Record<string, string>

const PLAIN = /^\d+(\.\d+)?$/
const INTEGER = /^\d+$/
const CURRENCY = /^[A-Z]{3}$/
/** A fraction's scale in the contract, and so a percentage's: 4 - 2 places. */
export const FRACTION_SCALE = 4
export const PERCENT_SCALE = FRACTION_SCALE - 2

let counter = 0
const nextKey = () => `shock-${++counter}`

export const isPercent = (f: Pick<TemplateField, 'kind' | 'unit'>) => f.kind === 'DECIMAL' && f.unit === 'fraction'
export const placesOf = (text: string) => (text.includes('.') ? text.length - text.indexOf('.') - 1 : 0)

/** Digits of `text` x 10^scale (plain decimal text with at most `scale` places), leading zeros stripped. */
export function unitsOf(text: string, scale: number): string {
  const [whole, part = ''] = text.split('.')
  return (whole + part.padEnd(scale, '0')).replace(/^0+(?=\d)/, '')
}

/** Compares two non-negative digit strings without leading zeros. */
export function compareUnits(a: string, b: string): number {
  if (a.length !== b.length) return a.length < b.length ? -1 : 1
  return a < b ? -1 : a > b ? 1 : 0
}

/** Percent text ("20", "15.5") as the exact fraction text the API stores ("0.2000", "0.1550"). */
export function percentToFraction(percent: string): string {
  const units = unitsOf(percent, PERCENT_SCALE).padStart(FRACTION_SCALE + 1, '0')
  return `${units.slice(0, -FRACTION_SCALE).replace(/^0+(?=\d)/, '')}.${units.slice(-FRACTION_SCALE)}`
}

const humanName: Record<string, string> = {
  cashflow_type: 'Cash-flow type',
  affected_fraction: 'Affected share',
  increase_fraction: 'Withdrawal increase',
  delay_days: 'Delay',
  delay_calendar: 'Day count',
  unavailable_days: 'Unavailable for',
  instrument_type: 'Funding source (instrument type)',
  instrument_keys: 'Instruments',
  source_system_code: 'Source system code',
  currency: 'Currency',
  origin: 'Flow origin',
  due_from_day: 'Due from day',
  due_to_day: 'Due to day',
}

/** A field's label; unknown names are humanised as they came. */
export const fieldLabel = (name: string) => humanName[name] ?? name.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())

/** The bound text of a percent field ("0" -> "0%", "1" -> "100%"). */
const percentBound = (fraction: string | null) => (fraction === null ? '' : `${fractionToPercent(fraction)}%`)

/**
 * Why one field's text cannot be sent, or null. Bounds, scale and allowed values come from the
 * template field; an empty optional field is fine.
 */
export function fieldProblem(field: TemplateField, raw: string): string | null {
  const text = raw.trim()
  if (!text) return field.required ? 'Required.' : null
  switch (field.kind) {
    case 'DECIMAL': {
      const percent = isPercent(field)
      const scale = percent ? PERCENT_SCALE : field.scale ?? FRACTION_SCALE
      if (!PLAIN.test(text)) return percent ? 'Enter a plain percentage, e.g. 20 or 15.5: no formulas, signs or exponents.' : 'Enter a plain decimal number: no formulas, signs or exponents.'
      if (placesOf(text) > scale)
        return percent
          ? `At most ${scale} decimal places in percent (scale ${field.scale ?? FRACTION_SCALE} as a fraction); never rounded.`
          : `At most ${scale} decimal places; never rounded.`
      const value = unitsOf(text, scale)
      const bound = (b: string) => (percent ? unitsOf(b, FRACTION_SCALE) : unitsOf(b, scale))
      const show = (b: string | null) => (percent ? percentBound(b) : b ?? '')
      if (field.min !== null) {
        const c = compareUnits(value, bound(field.min))
        if (c < 0 || (c === 0 && field.min_exclusive)) return `Must be ${field.min_exclusive ? 'more than' : 'at least'} ${show(field.min)}.`
      }
      if (field.max !== null && compareUnits(value, bound(field.max)) > 0) return `At most ${show(field.max)}.`
      return null
    }
    case 'INTEGER': {
      if (!INTEGER.test(text)) return 'Enter a whole number.'
      const value = text.replace(/^0+(?=\d)/, '')
      const unit = field.unit ? ` ${field.unit}` : ''
      const low = field.min !== null && compareUnits(value, field.min) < 0
      const high = field.max !== null && compareUnits(value, field.max) > 0
      if (low || high) return `Between ${field.min ?? '0'} and ${field.max ?? '…'}${unit}.`
      return null
    }
    case 'ENUM':
    case 'INSTRUMENT_TYPE':
      if (field.values.includes(text)) return null
      if (field.kind === 'INSTRUMENT_TYPE' && field.values.length === 0) return 'No instrument type fits this template under the ACTIVE cashflow_direction rule.'
      return `Only ${field.values.join(' or ')}.`
    case 'CURRENCY':
      return CURRENCY.test(text) ? null : 'A three-letter ISO 4217 code in capitals, e.g. EUR.'
    case 'TEXT':
      return field.max !== null && compareUnits(String(text.length), field.max) > 0 ? `At most ${field.max} characters.` : null
    default:
      return null
  }
}

/** Why a picked instrument list cannot be sent, or null (at most `max_items`, no duplicates). */
export function keysProblem(field: TemplateField, keys: string[]): string | null {
  if (field.required && keys.length === 0) return 'Pick at least one instrument.'
  if (field.max_items !== null && keys.length > field.max_items) return `At most ${field.max_items} instruments.`
  if (new Set(keys).size !== keys.length) return 'An instrument is picked twice.'
  return null
}

/** Masked picks cannot be re-sent (the analyst never sees the key), so they must be removed first. */
export function maskedKeysProblem(masked: readonly unknown[] | undefined): string | null {
  const n = masked?.length ?? 0
  if (n === 0) return null
  return `${n} picked instrument${n === 1 ? ' is' : 's are'} masked for your role and cannot be kept. Remove ${n === 1 ? 'it' : 'them'} to continue.`
}

/** A new shock of `template`: empty fields, except a one-value optional ENUM (CALENDAR_DAYS in v1). */
export function newShock(template: TemplateOut): ShockDraft {
  const prefill = (fields: TemplateField[]) =>
    Object.fromEntries(fields.filter((f) => f.kind !== 'INSTRUMENT_KEYS').map((f) => [f.name, f.kind === 'ENUM' && !f.required && f.values.length === 1 ? f.values[0] : '']))
  return { key: nextKey(), template: template.template, parameters: prefill(template.parameters), selector: prefill(template.selector), instrumentKeys: [], maskedKeys: [] }
}

/** The problems of one shock against its template, by field path. */
export function shockProblems(template: TemplateOut, draft: ShockDraft): FieldProblems {
  const problems: FieldProblems = {}
  for (const f of template.parameters) {
    const p = fieldProblem(f, draft.parameters[f.name] ?? '')
    if (p) problems[`parameters.${f.name}`] = p
  }
  for (const f of template.selector) {
    const p = f.kind === 'INSTRUMENT_KEYS' ? maskedKeysProblem(draft.maskedKeys) ?? keysProblem(f, draft.instrumentKeys) : fieldProblem(f, draft.selector[f.name] ?? '')
    if (p) problems[`selector.${f.name}`] = p
  }
  const from = (draft.selector.due_from_day ?? '').trim()
  const to = (draft.selector.due_to_day ?? '').trim()
  if (from && to && INTEGER.test(from) && INTEGER.test(to) && !problems['selector.due_to_day'] && compareUnits(to.replace(/^0+(?=\d)/, ''), from.replace(/^0+(?=\d)/, '')) < 0)
    problems['selector.due_to_day'] = 'Not before "Due from day".'
  return problems
}

export interface EditorProblems {
  name: string | null
  description: string | null
  summary: string | null
  count: string | null
  shocks: FieldProblems[]
}

/** Everything the editor would refuse before sending; `name` is checked only when it is edited. */
export function editorProblems(
  catalogue: TemplateOut[],
  maxShocks: number,
  shocks: ShockDraft[],
  text: { name?: string; description?: string; summary: string },
): EditorProblems {
  const name = text.name === undefined ? null : !text.name.trim() ? 'A name is required.' : text.name.trim().length > NAME_MAX ? `At most ${NAME_MAX} characters.` : null
  const description = text.description !== undefined && text.description.trim().length > DESCRIPTION_MAX ? `At most ${DESCRIPTION_MAX} characters.` : null
  const summary = text.summary.trim().length > SUMMARY_MAX ? `At most ${SUMMARY_MAX} characters.` : null
  const count = shocks.length === 0 ? 'Add at least one shock.' : shocks.length > maxShocks ? `At most ${maxShocks} shocks.` : null
  const byName = new Map(catalogue.map((t) => [t.template, t]))
  return {
    name,
    description,
    summary,
    count,
    shocks: shocks.map((s) => {
      const t = byName.get(s.template)
      return t ? shockProblems(t, s) : { template: 'This template is not offered by the server.' }
    }),
  }
}

export const hasProblems = (p: EditorProblems) => !!(p.name || p.description || p.summary || p.count || p.shocks.some((s) => Object.keys(s).length > 0))

/** One field's text as the value the API takes; null leaves an optional field out. */
function valueOf(field: TemplateField, raw: string): string | number | null {
  const text = raw.trim()
  if (!text) return null
  if (field.kind === 'DECIMAL') return isPercent(field) ? percentToFraction(text) : text
  if (field.kind === 'INTEGER') return parseInt(text, 10)
  return text
}

/**
 * A valid draft as the generated `Shock` (call only when `shockProblems` is empty). The server
 * validates it again; the selector is left out when nothing selects.
 */
export function buildShock(template: TemplateOut, draft: ShockDraft): Shock {
  const parameters: Record<string, string | number> = {}
  for (const f of template.parameters) {
    const v = valueOf(f, draft.parameters[f.name] ?? '')
    if (v !== null) parameters[f.name] = v
  }
  const selector: Record<string, string | number | string[]> = {}
  for (const f of template.selector) {
    if (f.kind === 'INSTRUMENT_KEYS') {
      if (draft.instrumentKeys.length) selector[f.name] = [...draft.instrumentKeys]
      continue
    }
    const v = valueOf(f, draft.selector[f.name] ?? '')
    if (v !== null) selector[f.name] = v
  }
  const shock = Object.keys(selector).length ? { template: template.template, parameters, selector } : { template: template.template, parameters }
  return shock as unknown as Shock
}

/** A stored shock as a draft (e.g. editing a DRAFT, or a new version adapting a parent). */
export function draftFromShock(template: TemplateOut, shock: Shock): ShockDraft {
  const params = shock.parameters as Record<string, unknown>
  const sel = (shock.selector ?? {}) as Record<string, unknown>
  const textOf = (field: TemplateField, v: unknown) => {
    if (v === undefined || v === null) return ''
    if (typeof v === 'string') return isPercent(field) ? fractionToPercent(v) : v
    return String(v)
  }
  const draft = newShock(template)
  for (const f of template.parameters) draft.parameters[f.name] = textOf(f, params[f.name]) || draft.parameters[f.name]
  for (const f of template.selector) {
    if (f.kind === 'INSTRUMENT_KEYS') {
      const keys = Array.isArray(sel[f.name]) ? (sel[f.name] as unknown[]) : []
      draft.instrumentKeys = keys.filter((k): k is string => typeof k === 'string')
      draft.maskedKeys = keys.filter(isMasked)
    }
    else draft.selector[f.name] = textOf(f, sel[f.name])
  }
  return draft
}
