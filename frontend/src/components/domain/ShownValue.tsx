import type { ReactNode } from 'react'
import { Masked } from '@/components/common/Masked'
import { groupDecimal } from '@/lib/format'
import { cn } from '@/lib/utils'
import type { DomainValue } from './domain'

/**
 * A domain value as the server sent it (FE-14.1): exact text in a monospace span, the
 * "masked" marker (`MaskedValue`) when the caller's clearance does not cover it, or `empty`
 * when the server sent nothing. Decimal text is never parsed or rounded; with `money`, the
 * integer part is grouped by thousands on the string itself (P-7), and `data-value` keeps the
 * server's exact text.
 */
export function ShownValue({
  value,
  empty = '—',
  testId,
  className,
  money,
}: {
  value: DomainValue | undefined
  /** Group thousands (string-only, never via float). */
  money?: boolean
  empty?: ReactNode
  testId?: string
  className?: string
}) {
  return (
    <Masked value={value} cell testId={testId}>
      {(v) =>
        v === null || v === undefined ? (
          <span className="text-muted-foreground" data-testid={testId} data-empty="true">
            {empty}
          </span>
        ) : (
          <span className={cn('font-mono', className)} data-testid={testId} data-value={money ? String(v) : undefined}>
            {money && typeof v === 'string' ? groupDecimal(v) : v}
          </span>
        )
      }
    </Masked>
  )
}
