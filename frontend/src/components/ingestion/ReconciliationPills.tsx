import { StatusPill } from '@/components/common/StatusPill'
import { ITEM_STATUS_STYLE, OUTCOME_STYLE, type ReconciliationItemStatus, type ReconciliationOutcome } from './reconciliation'

/** A reconciliation run's outcome (FE-12.1), in the backend's own set (D-23). */
export function ReconciliationOutcomePill({ outcome, className }: { outcome: ReconciliationOutcome; className?: string }) {
  return <StatusPill value={outcome} variant={OUTCOME_STYLE[outcome]} nowrap testId="recon-outcome" attr="outcome" className={className} />
}

/** One reconciliation check's status (FE-12.1), in the backend's own set (D-23). */
export function ReconciliationItemStatusPill({ status, className }: { status: ReconciliationItemStatus; className?: string }) {
  return <StatusPill value={status} variant={ITEM_STATUS_STYLE[status]} nowrap testId="recon-item-status" className={className} />
}
