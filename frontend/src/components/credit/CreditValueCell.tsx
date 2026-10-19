import type { ReactNode } from 'react'
import { MaskedValue, isMasked } from '@/components/common/MaskedValue'
import { cn } from '@/lib/utils'
import { valueText, type CreditValue } from './credit'

/**
 * A credit value as the server sent it (FE-19.1): exact text in a monospace span, the
 * "masked" marker (`MaskedValue`) when the caller's clearance does not cover it (D-43 §1), or
 * `empty` when the server sent nothing. Decimal text is never parsed, rounded or reformatted.
 */
export function CreditValueCell({
  value,
  empty = '—',
  testId,
  className,
}: {
  value: CreditValue | undefined
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
  const text = valueText(value)
  if (text === null)
    return (
      <span className="text-muted-foreground" data-testid={testId} data-empty="true">
        {empty}
      </span>
    )
  return (
    <span className={cn('font-mono', className)} data-testid={testId}>
      {text}
    </span>
  )
}
