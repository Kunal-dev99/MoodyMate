import { cn } from '@/lib/utils'
import { STATUS_STYLE, type VersionStatus } from './registry'

/** Schema version lifecycle status in the platform's status set (FE-4.1). */
export function StatusPill({ status, className }: { status: VersionStatus; className?: string }) {
  const style = STATUS_STYLE[status]
  return (
    <span
      className={cn('inline-flex items-center rounded-sm border px-2 py-0.5 text-xs font-medium', style.className, className)}
      data-testid="status-pill"
      data-status={status}
    >
      {style.label}
    </span>
  )
}
