import { cn } from '@/lib/utils'
import { QUARANTINE_STATUS_STYLE, SEVERITY_STYLE, type QuarantineStatus, type ValidationSeverity } from './validation'

const PILL = 'inline-flex items-center whitespace-nowrap rounded-sm border px-2 py-0.5 text-xs font-medium'

/** A quarantine item's status (FE-11.2), in the backend's own status set (D-20 §2). */
export function QuarantineStatusPill({ status, className }: { status: QuarantineStatus; className?: string }) {
  const style = QUARANTINE_STATUS_STYLE[status]
  return (
    <span className={cn(PILL, style.className, className)} data-testid="quarantine-status" data-status={status}>
      {style.label}
    </span>
  )
}

/** A validation issue's severity (FE-11.1). */
export function SeverityPill({ severity, className }: { severity: ValidationSeverity; className?: string }) {
  const style = SEVERITY_STYLE[severity]
  return (
    <span className={cn(PILL, style.className, className)} data-testid="issue-severity" data-severity={severity}>
      {style.label}
    </span>
  )
}
