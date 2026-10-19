import { cn } from '@/lib/utils'
import { SCENARIO_STATUS_STYLE, type ScenarioVersionStatus } from './scenarios'

/**
 * `scenario_version.status` (D-44 §5) as the server sent it. The UI never moves a status itself;
 * a version confirmed for exploration is never shown as approved.
 */
export function ScenarioStatusPill({ status, className, testId = 'scenario-status' }: { status: ScenarioVersionStatus; className?: string; testId?: string }) {
  const style = SCENARIO_STATUS_STYLE[status]
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
