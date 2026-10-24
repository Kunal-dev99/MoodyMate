import { StatusPill } from '@/components/common/StatusPill'
import { DRIFT_STATUS_STYLE, DRIFT_TYPE_STYLE, type DriftStatus, type DriftType } from './drift'

/** A drift event's type (FE-12.2), in the backend's own set (D-23). */
export function DriftTypePill({ type, className }: { type: DriftType; className?: string }) {
  return <StatusPill value={type} variant={DRIFT_TYPE_STYLE[type]} nowrap titled testId="drift-type" attr="drift-type" className={className} />
}

/** A drift event's review status (FE-12.2), in the backend's own set (D-23). */
export function DriftStatusPill({ status, className }: { status: DriftStatus; className?: string }) {
  return <StatusPill value={status} variant={DRIFT_STATUS_STYLE[status]} nowrap testId="drift-status" className={className} />
}
