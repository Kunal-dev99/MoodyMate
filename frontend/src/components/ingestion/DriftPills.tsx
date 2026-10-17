import { cn } from '@/lib/utils'
import { DRIFT_STATUS_STYLE, DRIFT_TYPE_STYLE, type DriftStatus, type DriftType } from './drift'

const PILL = 'inline-flex items-center whitespace-nowrap rounded-sm border px-2 py-0.5 text-xs font-medium'

/** A drift event's type (FE-12.2), in the backend's own set (D-23). */
export function DriftTypePill({ type, className }: { type: DriftType; className?: string }) {
  const style = DRIFT_TYPE_STYLE[type]
  return (
    <span className={cn(PILL, style.className, className)} data-testid="drift-type" data-drift-type={type} title={style.meaning}>
      {style.label}
    </span>
  )
}

/** A drift event's review status (FE-12.2), in the backend's own set (D-23). */
export function DriftStatusPill({ status, className }: { status: DriftStatus; className?: string }) {
  const style = DRIFT_STATUS_STYLE[status]
  return (
    <span className={cn(PILL, style.className, className)} data-testid="drift-status" data-status={status}>
      {style.label}
    </span>
  )
}
