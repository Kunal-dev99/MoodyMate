import { cn } from '@/lib/utils'
import { MAPPING_STATUS_STYLE, type MappingVersionStatus } from './mappings'

/** Mapping version lifecycle status (FE-10.2), in the backend's own status set (D-06/D-23). */
export function MappingStatusPill({ status, className }: { status: MappingVersionStatus; className?: string }) {
  const style = MAPPING_STATUS_STYLE[status]
  return (
    <span
      className={cn('inline-flex items-center rounded-sm border px-2 py-0.5 text-xs font-medium', style.className, className)}
      data-testid="mapping-status"
      data-status={status}
    >
      {style.label}
    </span>
  )
}
