import { cn } from '@/lib/utils'
import { RULE_STATUS_STYLE, type RuleVersionStatus } from './rules'

/** `rule_version.status` (D-32 §2) as the server sent it. The UI never moves a status itself. */
export function RuleVersionStatusPill({ status, className, testId = 'rule-version-status' }: { status: RuleVersionStatus; className?: string; testId?: string }) {
  const style = RULE_STATUS_STYLE[status]
  return (
    <span
      className={cn('inline-flex items-center rounded-sm border px-2 py-0.5 text-xs font-medium', style.className, className)}
      title={style.description}
      data-testid={testId}
      data-status={status}
    >
      {style.label}
    </span>
  )
}
