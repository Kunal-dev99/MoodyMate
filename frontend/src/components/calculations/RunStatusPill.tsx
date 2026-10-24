import { StatusPill } from '@/components/common/StatusPill'
import { IMPLEMENTATION_STYLE, RUN_STATUS_STYLE, type ImplementationStatus, type RunStatus } from './calculations'

/**
 * `calculation_run.status` (FE-15.2), in the backend's own status set (Data Model §9, D-31 §3).
 * The pill shows what the server says; RUNNING is never shown as success.
 */
export function RunStatusPill({ status, className, testId = 'run-status' }: { status: RunStatus; className?: string; testId?: string }) {
  return <StatusPill value={status} variant={RUN_STATUS_STYLE[status]} titled testId={testId} className={className} />
}

/** `module_version.implementation_status`: PLANNED modules are extension points, never shown as ready. */
export function ImplementationPill({
  status,
  className,
  title,
  testId = 'module-implementation',
}: {
  status: ImplementationStatus
  className?: string
  title?: string
  testId?: string
}) {
  return <StatusPill value={status} variant={IMPLEMENTATION_STYLE[status]} title={title} testId={testId} className={className} />
}
