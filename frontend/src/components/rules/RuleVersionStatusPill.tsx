import { StatusPill } from '@/components/common/StatusPill'
import { RULE_STATUS_STYLE, type RuleVersionStatus } from './rules'

/** `rule_version.status` (D-32 §2) as the server sent it. The UI never moves a status itself. */
export function RuleVersionStatusPill({ status, className, testId = 'rule-version-status' }: { status: RuleVersionStatus; className?: string; testId?: string }) {
  return <StatusPill value={status} variant={RULE_STATUS_STYLE[status]} titled testId={testId} className={className} />
}
