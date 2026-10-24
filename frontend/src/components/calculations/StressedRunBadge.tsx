import Link from 'next/link'
import { FlaskConical } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { STRESS_EXPLORER_UI_ENABLED } from '@/config/features'
import { approvalVariant, memberLabel, runHref, scenarioHref, type RunScenarioRef } from '@/lib/runs'
import { cn } from '@/lib/utils'

/**
 * FE-25.1: the label of a stressed run (`scenario` set, Phase 25), on the run list and the run
 * detail: the server's approval label ("Exploration, not approved" or "Approved scenario
 * (illustrative)"), which shocks it applied, and links to its baseline run and its scenario. A
 * non-stressed run (`scenario` null or absent) renders nothing, so Feature 03 runs look as before.
 * Labelling is not behind a switch: a stressed run is never shown unlabelled. The scenario link
 * is plain text while the explorer is off (NEXT_PUBLIC_STRESS_EXPLORER_UI), as /advanced is a 404.
 */
export function StressedRunBadge({ scenario, compact = false, className }: { scenario: RunScenarioRef | null | undefined; compact?: boolean; className?: string }) {
  if (!scenario) return null
  const scenarioText = `Scenario v${scenario.version}`
  return (
    <div
      className={cn('flex flex-wrap items-center gap-x-2 gap-y-1 text-xs', className)}
      data-testid="stressed-run"
      data-approval-state={scenario.approval_state}
      data-scenario-id={scenario.scenario_id}
      data-scenario-run-id={scenario.scenario_run_id}
      data-baseline-run-id={scenario.baseline_run_id}
      data-combined={scenario.combined}
    >
      <Badge variant={approvalVariant(scenario.approval_state)} data-testid="stressed-run-approval">
        <FlaskConical className="mr-1 h-3 w-3" />
        {scenario.approval_label}
      </Badge>
      <span className="text-muted-foreground" data-testid="stressed-run-subset">
        {memberLabel(scenario)}
      </span>
      <span className={cn('flex flex-wrap gap-x-2', compact && 'w-full')}>
        <Link href={runHref(scenario.baseline_run_id)} className="text-primary hover:underline" data-testid="stressed-run-baseline" title={`Baseline run: ${scenario.baseline_run_id}`}>
          baseline {scenario.baseline_run_id.slice(0, 8)}…
        </Link>
        {STRESS_EXPLORER_UI_ENABLED ? (
          <Link href={scenarioHref(scenario.scenario_id)} className="text-primary hover:underline" data-testid="stressed-run-scenario" title={`Scenario: ${scenario.scenario_id}`}>
            {scenarioText}
          </Link>
        ) : (
          <span data-testid="stressed-run-scenario" title={`Scenario: ${scenario.scenario_id}`}>
            {scenarioText}
          </span>
        )}
      </span>
    </div>
  )
}
