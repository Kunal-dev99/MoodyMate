import { StatusPill } from '@/components/common/StatusPill'
import { STATUS_STYLE, type VersionStatus } from './registry'

/** Schema version lifecycle status in the platform's status set (FE-4.1). */
export function SchemaStatusPill({ status, className }: { status: VersionStatus; className?: string }) {
  return <StatusPill value={status} variant={STATUS_STYLE[status]} testId="status-pill" className={className} />
}
