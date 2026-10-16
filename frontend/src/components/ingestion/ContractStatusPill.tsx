import { cn } from '@/lib/utils'
import { CONTRACT_STATUS_STYLE, type ContractStatus } from './sources'

/** Source contract lifecycle status (FE-7.2), in the backend's own status set. */
export function ContractStatusPill({ status, className }: { status: ContractStatus; className?: string }) {
  const style = CONTRACT_STATUS_STYLE[status]
  return (
    <span
      className={cn('inline-flex items-center rounded-sm border px-2 py-0.5 text-xs font-medium', style.className, className)}
      data-testid="contract-status"
      data-status={status}
    >
      {style.label}
    </span>
  )
}
