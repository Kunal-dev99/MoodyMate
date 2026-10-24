import { StatusPill } from '@/components/common/StatusPill'
import { PROPOSAL_STATUS_STYLE, type ProposalStatus } from './proposals'

export function ProposalStatusPill({ status }: { status: ProposalStatus }) {
  return <StatusPill value={status} variant={PROPOSAL_STATUS_STYLE[status]} testId="proposal-status" />
}
