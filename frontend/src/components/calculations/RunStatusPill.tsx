import { cn } from '@/lib/utils'
import { IMPLEMENTATION_STYLE, RUN_STATUS_STYLE, type ImplementationStatus, type RunStatus } from './calculations'

/**
 * `calculation_run.status` (FE-15.2), in the backend's own status set (Data Model §9, D-31 §3).
 * The pill shows what the server says; RUNNING is never shown as success.
 */
export function RunStatusPill({ status, className, testId = 'run-status' }: { status: RunStatus; className?: string; testId?: string }) {
  const style = RUN_STATUS_STYLE[status]
  return (
    <span
      className={cn('inline-flex items-center rounded-sm border px-2 py-0.5 text-xs font-medium', style.className, className)}
      title={style.description}
      data-testid={testId}
      data-status={status}
    >
      {style.label}
    </span>
  )
}

/** `module_version.implementation_status`: PLANNED modules are extension points, never shown as ready. */
export function ImplementationPill({ status, className }: { status: ImplementationStatus; className?: string }) {
  const style = IMPLEMENTATION_STYLE[status]
  return (
    <span
      className={cn('inline-flex items-center rounded-sm border px-2 py-0.5 text-xs font-medium', style.className, className)}
      data-testid="module-implementation"
      data-status={status}
    >
      {style.label}
    </span>
  )
}
