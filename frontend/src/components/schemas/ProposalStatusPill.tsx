import { cn } from '@/lib/utils'
import { PROPOSAL_STATUS_STYLE, type ProposalStatus } from './proposals'

export function ProposalStatusPill({ status }: { status: ProposalStatus }) {
  const style = PROPOSAL_STATUS_STYLE[status]
  return (
    <span
      className={cn('inline-flex items-center rounded-sm border px-2 py-0.5 text-xs font-medium', style.className)}
      data-testid="proposal-status"
      data-status={status}
    >
      {style.label}
    </span>
  )
}
