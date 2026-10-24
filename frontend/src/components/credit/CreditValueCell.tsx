import type { ReactNode } from 'react'
import { Masked } from '@/components/common/Masked'
import { groupDecimal } from '@/lib/format'
import { cn } from '@/lib/utils'
import { valueText, type CreditValue } from './credit'

/**
 * A credit value as the server sent it (FE-19.1): exact text in a monospace span, the
 * "masked" marker (`MaskedValue`) when the caller's clearance does not cover it (D-43 §1), or
 * `empty` when the server sent nothing. Decimal text is never parsed or rounded; with `money`,
 * the integer part is grouped by thousands on the string itself (P-7), and `data-value` keeps
 * the server's exact text.
 */
export function CreditValueCell({
  value,
  empty = '—',
  testId,
  className,
  money,
}: {
  value: CreditValue | undefined
  /** Group thousands (string-only, never via float). */
  money?: boolean
  empty?: ReactNode
  testId?: string
  className?: string
}) {
  return (
    <Masked value={value} cell testId={testId}>
      {(v) => {
        const text = valueText(v)
        if (text === null)
          return (
            <span className="text-muted-foreground" data-testid={testId} data-empty="true">
              {empty}
            </span>
          )
        return (
          <span className={cn('font-mono', className)} data-testid={testId} data-value={money ? text : undefined}>
            {money ? groupDecimal(text) : text}
          </span>
        )
      }}
    </Masked>
  )
}
