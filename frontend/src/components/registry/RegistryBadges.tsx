import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import { RULE_VERSION_STATUS_STYLE, type RuleVersionStatus } from './registry'

/**
 * A rule or assumption-set version's lifecycle status (D-06, D-32 §2), in the backend's own
 * status set. The pill shows what the server says.
 */
export function VersionStatusPill({ status, className, testId = 'version-status' }: { status: RuleVersionStatus; className?: string; testId?: string }) {
  const style = RULE_VERSION_STATUS_STYLE[status]
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

/**
 * The D-11 / D-35 labels the server sets on a seeded rule, version, module or assumption set:
 * ILLUSTRATIVE (a prototype parameter, not regulatory) and PROVISIONAL (awaiting the client's
 * own value), plus "seeded" for a version a tagged migration activated (D-32 §7).
 */
export function LabelBadges({
  illustrative,
  provisional,
  seeded,
  className,
}: {
  illustrative: boolean
  provisional: boolean
  seeded?: boolean
  className?: string
}) {
  if (!illustrative && !provisional && !seeded) return null
  return (
    <span className={cn('inline-flex flex-wrap items-center gap-1', className)}>
      {illustrative && (
        <Badge variant="warning" data-testid="illustrative-badge" title="Illustrative prototype. Not a regulatory calculation (D-35).">
          Illustrative
        </Badge>
      )}
      {provisional && (
        <Badge variant="warning" data-testid="provisional-badge" title="Provisional: awaiting the client's own value (D-11).">
          Provisional
        </Badge>
      )}
      {seeded && (
        <Badge variant="outline" data-testid="seeded-badge" title="Activated by a tagged migration (D-32 §7), not by an approver.">
          Seeded
        </Badge>
      )}
    </span>
  )
}
