import { ShieldQuestion } from 'lucide-react'
import { cn } from '@/lib/utils'
import { validationStyle, type ValidationStatus } from './validation'

/**
 * FE-30.1 (D-56): a module version's validation status, as the server reports it on the module
 * registry, the runs, the result reads and the comparison. Every current version is
 * ILLUSTRATIVE ("Illustrative — not validated"). An unknown or missing status reads as not
 * validated, never as more (D-04). Labels have no switch, as the D-35 §3 statements have none.
 */
export function ValidationStatusBadge({
  status,
  className,
  testId = 'validation-status',
}: {
  status: ValidationStatus | null | undefined
  className?: string
  testId?: string
}) {
  const style = validationStyle(status)
  return (
    <span
      className={cn('inline-flex items-center rounded-sm border px-2 py-0.5 text-xs font-medium', style.className, className)}
      title={style.description}
      data-testid={testId}
      data-validation-status={status ?? 'UNKNOWN'}
    >
      <ShieldQuestion className="mr-1 h-3 w-3" aria-hidden="true" />
      {style.label}
    </span>
  )
}
