import { StatusPill } from '@/components/common/StatusPill'
import { QUARANTINE_STATUS_STYLE, SEVERITY_STYLE, type QuarantineStatus, type ValidationSeverity } from './validation'

/** A quarantine item's status (FE-11.2), in the backend's own status set (D-20 §2). */
export function QuarantineStatusPill({ status, className }: { status: QuarantineStatus; className?: string }) {
  return <StatusPill value={status} variant={QUARANTINE_STATUS_STYLE[status]} nowrap testId="quarantine-status" className={className} />
}

/** A validation issue's severity (FE-11.1). */
export function SeverityPill({ severity, className }: { severity: ValidationSeverity; className?: string }) {
  return <StatusPill value={severity} variant={SEVERITY_STYLE[severity]} nowrap testId="issue-severity" attr="severity" className={className} />
}
