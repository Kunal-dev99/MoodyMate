import type { Schemas } from '@/api/client'

export type VersionStatus = Schemas['SchemaVersionStatus']
export type Compatibility = Schemas['Compatibility']
export type Field = Schemas['SchemaFieldOut']

/**
 * Platform lifecycle statuses (Data Model §9 + REJECTED, D-06) mapped onto the design
 * system's status styles. Every status has an entry: the Record type makes a status the
 * backend adds a compile error here rather than an unstyled pill.
 */
export const STATUS_STYLE: Record<VersionStatus, { label: string; className: string }> = {
  DRAFT: { label: 'Draft', className: 'status-draft' },
  IN_REVIEW: { label: 'In review', className: 'status-testing' },
  APPROVED: { label: 'Approved', className: 'status-building' },
  ACTIVE: { label: 'Active', className: 'status-published' },
  DEPRECATED: { label: 'Deprecated', className: 'status-testing' },
  RETIRED: { label: 'Retired', className: 'status-draft' },
  REJECTED: { label: 'Rejected', className: 'status-failed' },
}

export const COMPATIBILITY_STYLE: Record<
  Compatibility,
  { label: string; variant: 'success' | 'info' | 'destructive'; help: string }
> = {
  ADDITIVE: { label: 'Additive', variant: 'success', help: 'Only adds optional structure; nothing existing changes.' },
  COMPATIBLE: { label: 'Compatible', variant: 'info', help: 'Existing data stays valid.' },
  BREAKING: { label: 'Breaking', variant: 'destructive', help: 'Existing data or consumers may fail.' },
}

export interface AlignedField {
  name: string
  from?: Field
  to?: Field
}

/** Fields of two versions side by side, matched by name: `to`'s order, removed ones last. */
export function alignFields(from: Field[], to: Field[]): AlignedField[] {
  const before = new Map(from.map((f) => [f.name, f]))
  const rows: AlignedField[] = to.map((f) => ({ name: f.name, from: before.get(f.name), to: f }))
  const present = new Set(to.map((f) => f.name))
  for (const f of from) if (!present.has(f.name)) rows.push({ name: f.name, from: f })
  return rows
}

/** Compact type text, e.g. `DECIMAL(11,6) percent`. */
export function typeLabel(field: Field): string {
  const size = field.precision != null ? `(${field.precision}${field.scale != null ? `,${field.scale}` : ''})` : ''
  return `${field.data_type}${size}${field.unit ? ` ${field.unit}` : ''}`
}

/** A diff value for display: objects as `k=v` pairs, null as an em dash. */
export function valueLabel(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'object')
    return Object.entries(value as Record<string, unknown>)
      .map(([k, v]) => `${k}=${v ?? '—'}`)
      .join(', ')
  return String(value)
}

/** Default compare: effective → latest, or the version before when they are the same. */
export function defaultComparison(
  effective: number,
  latest: number,
  versions: { version: number }[],
): { from: number; to: number } {
  if (effective !== latest) return { from: effective, to: latest }
  const earlier = versions.map((v) => v.version).filter((v) => v < latest)
  return { from: earlier.length ? Math.max(...earlier) : latest, to: latest }
}

/** Is this diff response for the selection now on screen (stale-response guard)? */
export function isDiffFor(
  diff: { from_version: { version: number }; to_version: { version: number } },
  selection: { from: number; to: number },
): boolean {
  return diff.from_version.version === selection.from && diff.to_version.version === selection.to
}
