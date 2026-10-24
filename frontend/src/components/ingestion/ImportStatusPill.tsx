import { StatusPill } from '@/components/common/StatusPill'
import { IMPORT_STATUS_STYLE, type ImportStatus } from './imports'

/** Import job lifecycle status (FE-8.2), in the backend's own status set (D-21). */
export function ImportStatusPill({ status, className }: { status: ImportStatus; className?: string }) {
  return <StatusPill value={status} variant={IMPORT_STATUS_STYLE[status]} testId="import-status" className={className} />
}
