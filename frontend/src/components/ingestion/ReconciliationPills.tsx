import { cn } from '@/lib/utils'
import { ITEM_STATUS_STYLE, OUTCOME_STYLE, type ReconciliationItemStatus, type ReconciliationOutcome } from './reconciliation'

const PILL = 'inline-flex items-center whitespace-nowrap rounded-sm border px-2 py-0.5 text-xs font-medium'

/** A reconciliation run's outcome (FE-12.1), in the backend's own set (D-23). */
export function ReconciliationOutcomePill({ outcome, className }: { outcome: ReconciliationOutcome; className?: string }) {
  const style = OUTCOME_STYLE[outcome]
  return (
    <span className={cn(PILL, style.className, className)} data-testid="recon-outcome" data-outcome={outcome}>
      {style.label}
    </span>
  )
}

/** One reconciliation check's status (FE-12.1), in the backend's own set (D-23). */
export function ReconciliationItemStatusPill({ status, className }: { status: ReconciliationItemStatus; className?: string }) {
  const style = ITEM_STATUS_STYLE[status]
  return (
    <span className={cn(PILL, style.className, className)} data-testid="recon-item-status" data-status={status}>
      {style.label}
    </span>
  )
}
