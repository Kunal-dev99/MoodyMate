import type { Schemas } from '@/api/client'

/*
 * FE-30.1 (D-56): a module version's validation status and the `context` block of a result
 * read, as the server sends them. Both are response-wrapper fields built from the run's pins
 * (BE-30.2/30.3); the UI only shows them. Nothing here promotes a status or computes a context:
 * an unknown or missing status is shown as "not validated", never as a stronger claim (D-04).
 */

export type ValidationStatus = Schemas['ValidationStatus']
export type ResultContext = Schemas['ResultContext']
export type CurrencyBasis = Schemas['ContextCurrency']['basis']

/**
 * `ValidationStatus` (D-56 §1) on the design system's status styles. The Record type turns a
 * status the backend adds into a compile error here, and a test keeps it equal to the OpenAPI
 * enum. Every current module version is ILLUSTRATIVE; the other two exist only once a
 * validation record names an approver, a date and evidence (D-56 §3, a client hard stop), and
 * even then they say what was recorded, not that the module meets any regulation.
 */
export const VALIDATION_STATUS_STYLE: Record<ValidationStatus, { label: string; className: string; description: string }> = {
  ILLUSTRATIVE: {
    label: 'Illustrative — not validated',
    className: 'status-testing',
    description:
      'Illustrative prototype: the methodology has not been validated by anyone. Not a regulatory calculation; nothing here may be used for client decisions.',
  },
  // PROVISIONAL wording: not yet approved by the client. Display wording for the two validated
  // statuses is part of the D-56 §3 client hard stop; no module version uses them today.
  INTERNAL_VALIDATED: {
    label: 'Internal validation recorded',
    className: 'status-building',
    description:
      'A validation record names an approver, an approval date and its evidence (methodology document version and approved golden pack). Internal use only; not a regulatory validation.',
  },
  // PROVISIONAL wording: see INTERNAL_VALIDATED above (D-56 §3 client hard stop).
  REGULATORY_VALIDATED: {
    label: 'Regulatory validation recorded',
    className: 'status-published',
    description:
      'A validation record for regulatory use names an approver, an approval date and its evidence. The record is the claim; the platform makes no claim of its own.',
  },
}
export const VALIDATION_STATUSES = Object.keys(VALIDATION_STATUS_STYLE) as ValidationStatus[]

/** What a badge shows when the server sent no status it knows: never more than "not validated". */
export const VALIDATION_STATUS_UNKNOWN = {
  label: 'Not validated — status not reported',
  className: 'status-draft',
  description: 'The server did not report a validation status the UI knows, so the result is treated as not validated.',
} as const

export function validationStyle(status: ValidationStatus | null | undefined): { label: string; className: string; description: string } {
  return status && Object.prototype.hasOwnProperty.call(VALIDATION_STATUS_STYLE, status) ? VALIDATION_STATUS_STYLE[status] : VALIDATION_STATUS_UNKNOWN
}

/** `ContextCurrency.basis`: how figures in different currencies relate (D-35: they do not). */
export const CURRENCY_BASIS_TEXT: Record<CurrencyBasis, string> = {
  PER_CURRENCY_NO_FX: 'Per currency, no FX conversion and no cross-currency total',
}

/** The pins a context block is checked against (a run detail's own fields). */
export type ContextPins = Pick<Schemas['RunDetail'], 'reporting_date' | 'module' | 'engine_version' | 'validation_status'> & {
  manifest: { rule_set: { members: Pick<Schemas['ManifestRuleVersion'], 'rule_code' | 'rule_version_id' | 'version'>[] } }
}

export type ContextField = 'as_of_date' | 'module' | 'engine_version' | 'rule_versions' | 'validation_status'

export const CONTEXT_FIELD_TEXT: Record<ContextField, string> = {
  as_of_date: 'as-of date (the reporting date)',
  module: 'module code and version',
  engine_version: 'engine version',
  rule_versions: 'rule versions',
  validation_status: 'validation status',
}

const ruleKey = (r: { rule_code: string; rule_version_id: string; version: number }) => `${r.rule_code}\u0000${r.rule_version_id}\u0000${r.version}`

/**
 * The fields where a result's `context` differs from the run's own pins. The server builds the
 * block from those pins (BE-30.3), so this is empty unless something is wrong; the UI says so
 * rather than silently showing one of the two. A comparison, not a computation. Display only:
 * it never blocks or hides the result; the server's checks are the control (D-04).
 */
export function contextMismatches(context: ResultContext, run: ContextPins): ContextField[] {
  const out: ContextField[] = []
  if (context.as_of_date !== run.reporting_date) out.push('as_of_date')
  if (context.methodology.module_code !== run.module.code || context.methodology.module_version !== run.module.version) out.push('module')
  if (context.methodology.engine_version !== run.engine_version) out.push('engine_version')
  // Set-based: the same rule versions in any order (or listed twice) are the same pins.
  const shown = new Set(context.methodology.rule_versions.map(ruleKey))
  const pinned = new Set(run.manifest.rule_set.members.map(ruleKey))
  if (shown.size !== pinned.size || [...shown].some((k) => !pinned.has(k))) out.push('rule_versions')
  if (context.validation_status !== run.validation_status) out.push('validation_status')
  return out
}
