import type { ReactNode } from 'react'
import { Lock } from 'lucide-react'
import type { Schemas } from '@/api/client'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'

/*
 * FE-29.2: the one place a value the server masked is rendered (BE-3.5, D-33, D-43 §1). Every
 * view passes the server's value through `<Masked>`: a masked marker renders as explicit
 * "masked" text with a tooltip explaining why, never as a blank, a zero or a guess; a clear
 * value renders as the caller says. Masking is the server's decision (D-04): nothing here
 * unmasks, infers or recomputes a withheld value.
 *
 * No 'use client' and no hooks: a server or a client tree may render it, and a render function
 * passed as `children` never crosses a client boundary (the tooltip parts are client components).
 */

type MaskedMarker = Schemas['MaskedValue']

/** True when an API value was withheld by the server's masking policy (BE-3.5). */
export function isMasked(value: unknown): value is MaskedMarker {
  return (
    typeof value === 'object' &&
    value !== null &&
    (value as { masked?: unknown }).masked === true &&
    typeof (value as { classification?: unknown }).classification === 'string'
  )
}

function Marker({ value }: { value: MaskedMarker }) {
  const level = value.classification || 'unclassified'
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span
            className="inline-flex items-center gap-1 rounded-sm border border-border bg-surface-2 px-1.5 py-0.5 font-mono text-xs text-muted-foreground"
            data-testid="masked-value"
            aria-label={`Masked by policy: ${level} data`}
          >
            <Lock className="h-3 w-3" />
            masked
          </span>
        </TooltipTrigger>
        <TooltipContent>
          Masked by policy: <strong>{level}</strong> data. Your role&apos;s
          clearance does not include it.
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}

/**
 * A server value that may be masked.
 *
 * - Masked: the "masked" marker. With `cell`, inside `<span data-testid={testId} data-masked="true">`
 *   so a test (and the e2e specs) can find the masked figure by its own test id; `attrs` adds
 *   further data attributes to that span.
 * - Clear: `children(value)`, or the value itself when there are no children.
 */
export function Masked<T>({
  value,
  cell = false,
  testId,
  attrs,
  children,
}: {
  value: T
  cell?: boolean
  testId?: string
  attrs?: Readonly<Record<`data-${string}`, string>>
  children?: (clear: Exclude<T, MaskedMarker>) => ReactNode
}) {
  if (isMasked(value)) {
    if (!cell) return <Marker value={value} />
    return (
      <span data-testid={testId} {...attrs} data-masked="true">
        <Marker value={value} />
      </span>
    )
  }
  const clear = value as Exclude<T, MaskedMarker>
  return <>{children ? children(clear) : (clear as ReactNode)}</>
}

/**
 * A server flag that reads a masked figure, so the server sent no answer (`null`): shown as
 * "<flag>: masked", never as "no".
 */
export function MaskedFlag({
  flag,
  testId,
  as: Tag = 'span',
  className,
  attrs,
}: {
  flag: string
  testId: string
  as?: 'span' | 'div'
  className?: string
  attrs?: Readonly<Record<`data-${string}`, string>>
}) {
  return (
    <Tag className={className} data-testid={testId} {...attrs} title="The flag reads a masked figure, so it is not shown.">
      {flag}: masked
    </Tag>
  )
}
