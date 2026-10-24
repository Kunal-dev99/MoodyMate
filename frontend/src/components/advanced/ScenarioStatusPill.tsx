import { StatusPill } from '@/components/common/StatusPill'
import { SCENARIO_STATUS_STYLE, type ScenarioVersionStatus } from './scenarios'

/**
 * `scenario_version.status` (D-44 §5) as the server sent it. The UI never moves a status itself;
 * a version confirmed for exploration is never shown as approved.
 */
export function ScenarioStatusPill({ status, className, testId = 'scenario-status' }: { status: ScenarioVersionStatus; className?: string; testId?: string }) {
  return <StatusPill value={status} variant={SCENARIO_STATUS_STYLE[status]} titled testId={testId} className={className} />
}
