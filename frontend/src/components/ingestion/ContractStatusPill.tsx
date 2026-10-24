import { StatusPill } from '@/components/common/StatusPill'
import { CONTRACT_STATUS_STYLE, type ContractStatus } from './sources'

/** Source contract lifecycle status (FE-7.2), in the backend's own status set. */
export function ContractStatusPill({ status, className }: { status: ContractStatus; className?: string }) {
  return <StatusPill value={status} variant={CONTRACT_STATUS_STYLE[status]} testId="contract-status" className={className} />
}
