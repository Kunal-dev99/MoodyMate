import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'
import { RunStatusPill } from './RunStatusPill'
import { ValidationStatusBadge } from './ValidationStatusBadge'
import type { RunSummary } from './calculations'

type Identity = Pick<
  RunSummary,
  'run_id' | 'status' | 'reporting_date' | 'module' | 'engine_version' | 'rule_set_id' | 'assumption_set_id' | 'dataset_snapshot_id'
> &
  // FE-30.1: shown when the read carries it (every run read since BE-30.2).
  Partial<Pick<RunSummary, 'validation_status'>>

function Item({ label, children, testId }: { label: string; children: ReactNode; testId?: string }) {
  return (
    <span className="inline-flex items-baseline gap-1">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-mono" data-testid={testId}>
        {children}
      </span>
    </span>
  )
}

/**
 * The run a result belongs to (FE-15.2): run ID, status, reporting date and every
 * configuration version it pinned, shown on every result view so no figure is read without
 * its run. FE-30.1: and the module version's validation status (D-56), when the read sends it.
 */
export function RunIdentity({ run, className }: { run: Identity; className?: string }) {
  return (
    <div
      className={cn('flex flex-wrap items-center gap-x-4 gap-y-1 rounded-md border border-border bg-surface-2 px-3 py-2 text-xs', className)}
      data-testid="run-identity"
      data-run-id={run.run_id}
      data-reporting-date={run.reporting_date}
    >
      <Item label="Run" testId="identity-run-id">
        {run.run_id}
      </Item>
      <RunStatusPill status={run.status} testId="identity-status" />
      {'validation_status' in run && <ValidationStatusBadge status={run.validation_status} testId="identity-validation-status" />}
      <Item label="Reporting date" testId="identity-reporting-date">
        {run.reporting_date}
      </Item>
      <Item label="Module" testId="identity-module">
        {run.module.code} v{run.module.version}
      </Item>
      <Item label="Engine" testId="identity-engine">
        {run.engine_version}
      </Item>
      <Item label="Rule set" testId="identity-rule-set">
        {run.rule_set_id}
      </Item>
      <Item label="Assumption set" testId="identity-assumption-set">
        {run.assumption_set_id}
      </Item>
      <Item label="Snapshot" testId="identity-snapshot">
        {run.dataset_snapshot_id}
      </Item>
    </div>
  )
}
