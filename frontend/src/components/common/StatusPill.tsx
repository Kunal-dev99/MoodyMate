import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

/**
 * FE-29.2: the one status pill. Every lifecycle pill of the platform (import job, mapping and
 * schema versions, proposals, contracts, quarantine items, drift, reconciliation, rule and
 * scenario versions, ratings, calculation runs, validation status) is a thin wrapper that maps
 * its OpenAPI enum to a variant here. The pill only shows the status the server sent; it never
 * moves or infers one. A test keeps every map equal to its `openapi.json` enum.
 */

/** How one enum value looks: its label, a design-system status class and an optional help text. */
export interface PillVariant {
  label: string
  className: string
  /** Shown as the pill's title when the wrapper asks for it. */
  description?: string
  meaning?: string
}

/** One enum → variant map. The `Record` type turns a value the backend adds into a compile error. */
export type PillMap<V extends string> = Readonly<Record<V, PillVariant>>

const PILL = 'inline-flex items-center rounded-sm border px-2 py-0.5 text-xs font-medium'
const PILL_NOWRAP = 'inline-flex items-center whitespace-nowrap rounded-sm border px-2 py-0.5 text-xs font-medium'

/** A value the map does not know (a newer server): shown as sent, in the neutral style, never as success. */
const UNKNOWN_CLASS = 'status-draft'

export function StatusPill({
  value,
  variant,
  testId,
  attr = 'status',
  title,
  titled = false,
  nowrap = false,
  icon,
  className,
}: {
  /** The server's value, written to `data-<attr>`. */
  value: string
  /** The wrapper's map entry for it (undefined for a value the map does not know). */
  variant: PillVariant | undefined
  testId?: string
  /** The data attribute that carries the value (`status`, `severity`, `outcome`…); null for none. */
  attr?: string | null
  /** An explicit title; otherwise, with `titled`, the variant's description or meaning. */
  title?: string
  titled?: boolean
  nowrap?: boolean
  icon?: ReactNode
  className?: string
}) {
  const label = variant?.label ?? value
  const shownTitle = title ?? (titled ? (variant?.description ?? variant?.meaning) : undefined)
  const data = attr ? { [`data-${attr}`]: value } : {}
  return (
    <span className={cn(nowrap ? PILL_NOWRAP : PILL, variant?.className ?? UNKNOWN_CLASS, className)} title={shownTitle} data-testid={testId} {...data}>
      {icon}
      {label}
    </span>
  )
}
