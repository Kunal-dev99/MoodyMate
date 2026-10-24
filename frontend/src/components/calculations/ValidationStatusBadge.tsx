import { ShieldQuestion } from 'lucide-react'
import { StatusPill } from '@/components/common/StatusPill'
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
  return (
    <StatusPill
      value={status ?? 'UNKNOWN'}
      variant={validationStyle(status)}
      titled
      testId={testId}
      attr="validation-status"
      icon={<ShieldQuestion className="mr-1 h-3 w-3" aria-hidden="true" />}
      className={className}
    />
  )
}
