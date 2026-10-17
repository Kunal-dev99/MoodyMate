import type { ReactNode } from 'react'
import { MaskedValue, isMasked } from '@/components/common/MaskedValue'
import { cn } from '@/lib/utils'
import type { DomainValue } from './domain'

/**
 * A domain value as the server sent it (FE-14.1): exact text in a monospace span, the
 * "masked" marker (`MaskedValue`) when the caller's clearance does not cover it, or `empty`
 * when the server sent nothing. Decimal text is never parsed, rounded or reformatted.
 */
export function ShownValue({
  value,
  empty = '—',
  testId,
  className,
}: {
  value: DomainValue | undefined
  empty?: ReactNode
  testId?: string
  className?: string
}) {
  if (isMasked(value))
    return (
      <span data-testid={testId} data-masked="true">
        <MaskedValue value={value} />
      </span>
    )
  if (value === null || value === undefined)
    return (
      <span className="text-muted-foreground" data-testid={testId} data-empty="true">
        {empty}
      </span>
    )
  return (
    <span className={cn('font-mono', className)} data-testid={testId}>
      {value}
    </span>
  )
}
