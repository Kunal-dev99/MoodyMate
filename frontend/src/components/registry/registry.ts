import type { Schemas } from '@/api/client'

/*
 * FE-16.1: the registry pages' pure helpers (rules, modules, assumption sets under /domain).
 * Every payload type comes from the generated API schema. Rule parameters arrive as an untyped
 * object (`parameters: {[key: string]: unknown}`): they are read structurally here and shown as
 * the server's exact text. Decimal parameters are JSON strings and are never parsed, rounded or
 * reformatted (a test greps the registry code for number parsing).
 */

export type RulePage = Schemas['RulePage']
export type RuleSummary = Schemas['RuleSummary']
export type RuleDetail = Schemas['RuleDetail']
export type RuleVersion = Schemas['RuleVersionOut']
export type RuleVersionStatus = Schemas['RuleVersionStatus']
export type RuleCategory = Schemas['RuleCategory']
export type PinningRun = Schemas['PinningRun']
export type ModulePage = Schemas['ModulePage']
export type ModuleVersion = Schemas['ModuleVersionOut']
export type ImplementationStatus = Schemas['ImplementationStatus']
export type AssumptionSetList = Schemas['AssumptionSetList']
export type AssumptionSet = Schemas['AssumptionSetItem']
export type AssumptionSetDetail = Schemas['AssumptionSetDetailOut']
export type AssumptionSetVersion = Schemas['AssumptionSetVersionSummary']
/** A parameter object or parameter schema, as the API sends it (untyped in the contract). */
export type ParameterObject = RuleVersion['parameters']

// --- links -----------------------------------------------------------------------------------

export const rulesHref = '/domain/rules'
export const modulesHref = '/domain/modules'
export const assumptionSetsHref = '/domain/assumption-sets'
export const ruleHref = (ruleId: string): string => `${rulesHref}/${encodeURIComponent(ruleId)}`
export const assumptionSetHref = (assumptionSetId: string): string => `${assumptionSetsHref}/${encodeURIComponent(assumptionSetId)}`

// --- lifecycle -------------------------------------------------------------------------------

/**
 * `rule_version.status` (Data Model §9 plus REJECTED, D-06, D-32 §2), also the status set of
 * assumption-set versions, on the design system's status styles. The Record type turns a
 * status the backend adds into a compile error here, and a test keeps it equal to the OpenAPI
 * enum. Only ACTIVE is what a run submitted now pins.
 */
export const RULE_VERSION_STATUS_STYLE: Record<RuleVersionStatus, { label: string; className: string; description: string }> = {
  DRAFT: { label: 'Draft', className: 'status-draft', description: 'Being written by its author. No run pins it.' },
  IN_REVIEW: {
    label: 'In review',
    className: 'status-testing',
    description: 'Submitted and immutable. Waiting for an approver who is not the author (D-32 §3).',
  },
  APPROVED: {
    label: 'Approved',
    className: 'status-building',
    description: 'Reserved: an approval activates the version in the same transaction (D-32 §2).',
  },
  ACTIVE: { label: 'Active', className: 'status-published', description: 'What a run submitted now pins. At most one per rule.' },
  SUPERSEDED: {
    label: 'Superseded',
    className: 'status-draft',
    description: 'Replaced by a later ACTIVE version. Kept, never deleted: the runs that pinned it stay reproducible.',
  },
  RETIRED: { label: 'Retired', className: 'status-draft', description: 'No longer pinned by new runs. Kept for the runs that pinned it.' },
  REJECTED: { label: 'Rejected', className: 'status-failed', description: 'Not approved. No run ever pinned it.' },
}
export const RULE_VERSION_STATUSES = Object.keys(RULE_VERSION_STATUS_STYLE) as RuleVersionStatus[]

/** What each rule category decides (`rule_definition.category`). */
export const CATEGORY_TEXT: Record<RuleCategory, string> = {
  BUCKETING: 'Bucketing',
  CLASSIFICATION: 'Classification',
  TREATMENT: 'Treatment',
  FACTOR: 'Factor',
}

/** How the registry groups module versions: PLANNED ones are extension points, never ready. */
export const MODULE_GROUPS: Record<ImplementationStatus, { title: string; description: string }> = {
  AVAILABLE: {
    title: 'Available',
    description:
      'Implemented in this release as illustrative prototypes (D-35). Not regulatory calculations; a version runs only while every rule it reads has an ACTIVE version.',
  },
  PLANNED: {
    title: 'Planned',
    description: 'Declared extension points (D-31 §1). Not implemented in this release: they never run and are not production-ready.',
  },
}
export const IMPLEMENTATION_STATUSES = Object.keys(MODULE_GROUPS) as ImplementationStatus[]

/** Module versions split by implementation status, each group in the server's order. */
export function groupModules(items: readonly ModuleVersion[]): Record<ImplementationStatus, ModuleVersion[]> {
  const groups = Object.fromEntries(IMPLEMENTATION_STATUSES.map((s) => [s, [] as ModuleVersion[]])) as Record<ImplementationStatus, ModuleVersion[]>
  for (const m of items) groups[m.implementation_status].push(m)
  return groups
}

/** "Used by N runs": the server's `run_count`, as text. */
export function usedByText(count: number): string {
  if (count === 0) return 'Not used by any run'
  return count === 1 ? 'Used by 1 run' : `Used by ${count} runs`
}

// --- parameters ------------------------------------------------------------------------------

/** The closed parameter kinds (D-32 §1), described. An unknown kind is shown as itself. */
const KIND_TEXT: Record<string, string> = {
  DECIMAL: 'Decimal (min, max, scale)',
  INTEGER: 'Integer (min, max)',
  ENUM: 'One of a closed list of values',
  DAY_BUCKETS: 'Day buckets: ordered, contiguous half-open intervals, the last one open',
  ENUM_MAP: 'Map from a key vocabulary to a closed list of values',
  DECIMAL_MAP: 'Map from a key vocabulary to decimals',
}

/** The `kind` of a parameter schema, or null when the server sent none. */
export function parameterKind(schema: ParameterObject): string | null {
  return typeof schema.kind === 'string' ? schema.kind : null
}

export function kindText(kind: string | null): string {
  if (kind === null) return 'No kind declared'
  return KIND_TEXT[kind] ?? `Unknown kind ${kind}`
}

/** A JSON value as exact text: strings (decimal text included) verbatim, never reformatted. */
export function leafText(value: unknown): string {
  if (value === undefined) return '—'
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean' || value === null) return String(value)
  return JSON.stringify(value)
}

/** The schema's declared constraints (everything but `kind`), as label/text pairs in the server's order. */
export function schemaConstraints(schema: ParameterObject): [string, string][] {
  return Object.entries(schema)
    .filter(([key]) => key !== 'kind')
    .map(([key, value]) => [key, Array.isArray(value) ? value.map(leafText).join(', ') : leafText(value)])
}

const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)

/** A DAY_BUCKETS bucket's interval: (lower, upper] calendar days, the last one (lower, ∞). */
export function bucketInterval(lower: unknown, upper: unknown): string {
  return upper === null || upper === undefined ? `(${leafText(lower)}, ∞)` : `(${leafText(lower)}, ${leafText(upper)}]`
}

/** The rows of a DAY_BUCKETS parameter set (`buckets`), or null when it has none. */
export function bucketRows(parameters: ParameterObject): { code: string; label: string; interval: string }[] | null {
  const buckets = parameters.buckets
  if (!Array.isArray(buckets)) return null
  return buckets.map((b) => {
    const r = isRecord(b) ? b : {}
    return { code: leafText(r.code), label: leafText(r.label), interval: bucketInterval(r.lower_exclusive, r.upper_inclusive) }
  })
}

/** The entries of a map kind (`map`), sorted by key, or null when it has none. */
export function mapEntries(parameters: ParameterObject): [string, unknown][] | null {
  const map = parameters.map
  if (!isRecord(map)) return null
  return Object.entries(map).sort(([a], [b]) => a.localeCompare(b))
}

/**
 * Parameters flattened to path → exact leaf text, in the server's order. An array whose items
 * all carry a distinct `code` (DAY_BUCKETS) is keyed by code, so inserting a bucket does not
 * show every later bucket as changed.
 */
export function flattenParameters(value: unknown, path = ''): Map<string, string> {
  const out = new Map<string, string>()
  const walk = (v: unknown, at: string) => {
    if (Array.isArray(v)) {
      const codes = v.map((item) => (isRecord(item) && typeof item.code === 'string' ? item.code : null))
      const byCode = v.length > 0 && codes.every((c) => c !== null) && new Set(codes).size === codes.length
      if (v.length === 0) out.set(at, '[]')
      v.forEach((item, i) => walk(item, `${at}[${byCode ? codes[i] : i}]`))
    } else if (isRecord(v)) {
      const keys = Object.keys(v)
      if (keys.length === 0) out.set(at, '{}')
      for (const k of keys) walk(v[k], at ? `${at}.${k}` : k)
    } else out.set(at, leafText(v))
  }
  walk(value, path)
  return out
}

export type ParameterChange = { path: string; change: 'added' | 'removed' | 'changed'; from: string | null; to: string | null }

/** What changed from one version's parameters to another's: added, removed and changed leaves only. */
export function diffParameters(from: ParameterObject, to: ParameterObject): ParameterChange[] {
  const a = flattenParameters(from)
  const b = flattenParameters(to)
  const changes: ParameterChange[] = []
  for (const [path, before] of a) {
    if (!b.has(path)) changes.push({ path, change: 'removed', from: before, to: null })
    else if (b.get(path) !== before) changes.push({ path, change: 'changed', from: before, to: b.get(path) ?? null })
  }
  for (const [path, after] of b) if (!a.has(path)) changes.push({ path, change: 'added', from: null, to: after })
  return changes
}

/** Versions newest first (the server lists them by version number, oldest first). */
export const newestFirst = (versions: readonly RuleVersion[]): RuleVersion[] => [...versions].sort((x, y) => y.version - x.version)

/** The version a rule page opens on: the ACTIVE one, else the newest. */
export function defaultVersion(versions: readonly RuleVersion[]): RuleVersion | null {
  return versions.find((v) => v.status === 'ACTIVE') ?? newestFirst(versions)[0] ?? null
}

/** The default diff: the version before the default one, against it. Null with fewer than two versions. */
export function defaultDiffPair(versions: readonly RuleVersion[]): { from: number; to: number } | null {
  const to = defaultVersion(versions)
  if (!to) return null
  const before = newestFirst(versions).find((v) => v.version < to.version)
  if (before) return { from: before.version, to: to.version }
  const after = [...versions].sort((x, y) => x.version - y.version).find((v) => v.version > to.version)
  return after ? { from: to.version, to: after.version } : null
}
