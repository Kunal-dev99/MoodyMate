import { cn } from '@/lib/utils'
import { IMPORT_STATUS_STYLE, type ImportStatus } from './imports'

/** Import job lifecycle status (FE-8.2), in the backend's own status set (D-21). */
export function ImportStatusPill({ status, className }: { status: ImportStatus; className?: string }) {
  const style = IMPORT_STATUS_STYLE[status]
  return (
    <span
      className={cn('inline-flex items-center rounded-sm border px-2 py-0.5 text-xs font-medium', style.className, className)}
      data-testid="import-status"
      data-status={status}
    >
      {style.label}
    </span>
  )
}
