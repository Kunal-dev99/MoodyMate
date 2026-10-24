import { StatusPill } from '@/components/common/StatusPill'
import { MAPPING_STATUS_STYLE, type MappingVersionStatus } from './mappings'

/** Mapping version lifecycle status (FE-10.2), in the backend's own status set (D-06/D-23). */
export function MappingStatusPill({ status, className }: { status: MappingVersionStatus; className?: string }) {
  return <StatusPill value={status} variant={MAPPING_STATUS_STYLE[status]} testId="mapping-status" className={className} />
}
