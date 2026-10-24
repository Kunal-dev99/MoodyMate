'use client'

import Link from 'next/link'
import { useEffect, useMemo, useState } from 'react'
import { GitBranch } from 'lucide-react'
import { EmptyState } from '@/components/common/EmptyState'
import { TruncationNote } from '@/components/common/ListPager'
import { createLoader, type Loaded } from '@/components/common/load'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { PageSection } from '@/components/common/PageSection'
import { Badge } from '@/components/ui/badge'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { formatTimestamp } from '@/lib/format'
import { truncationOf, type Truncation } from '@/lib/paging'
import { DeltaValue } from './DeltaValue'
import { approvalVariant } from './scenario-runs'
import { fetchScenario, scenarioHref } from './scenarios'
import { comparisonHref, fetchComparison, fetchScenarioRuns, flagState, parentRunOf, whatIfRows, type ComparisonCell, type ScenarioComparison, type ScenarioRunSummary, type WhatIfRow } from './comparison'

/** What the side by side resolves before it can show anything. */
export type Resolved =
  | { kind: 'no_parent' }
  /** Q18: `truncation` is set when the search read one capped page of scenario runs and the server cut it. */
  | { kind: 'no_parent_run'; parentVersion: number; truncation?: Truncation | null }
  | { kind: 'ready'; parentVersion: number; run: ScenarioRunSummary; comparison: ScenarioComparison }

/**
 * Resolves the parent: the version's `parent_version` (scenario detail), the parent's newest
 * scenario run on the same baseline (GET scenario-runs), then its comparison. Every step is a
 * calc:read the server checks.
 */
const resolveParent = (scenarioId: string, version: number, baselineRunId: string) => async (signal: AbortSignal): Promise<Resolved> => {
  const scenario = await fetchScenario(scenarioId)(signal)
  const parentVersion = scenario.versions.find((v) => v.version === version)?.parent_version ?? null
  if (parentVersion === null) return { kind: 'no_parent' }
  const runs = await fetchScenarioRuns(scenarioId, baselineRunId)(signal)
  const run = parentRunOf(runs.items, parentVersion, baselineRunId)
  if (!run) return { kind: 'no_parent_run', parentVersion, truncation: truncationOf(runs, 'scenario runs on this baseline') }
  return { kind: 'ready', parentVersion, run, comparison: await fetchComparison(scenarioId, run.scenario_run_id)(signal) }
}

function Delta({ cell, figure, testId }: { cell: ComparisonCell | undefined; figure: 'net_gap' | 'cumulative_gap'; testId: string }) {
  if (!cell) return <span className="text-muted-foreground">—</span>
  return <DeltaValue value={cell[figure].delta} testId={testId} />
}

/** The side-by-side table: the combined run's net and cumulative deltas under the parent and under this version. */
export function WhatIfTable({ rows, parentVersion, version }: { rows: WhatIfRow[]; parentVersion: number; version: number }) {
  return (
    <Table data-testid="what-if-table">
      <TableHeader>
        <TableRow>
          <TableHead>Currency</TableHead>
          <TableHead>Bucket</TableHead>
          <TableHead className="text-right">v{parentVersion} Δ net</TableHead>
          <TableHead className="text-right">v{version} Δ net</TableHead>
          <TableHead className="text-right">v{parentVersion} Δ cumulative</TableHead>
          <TableHead className="text-right">v{version} Δ cumulative</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((r) => (
          <TableRow
            key={`${r.currency}-${r.bucketCode}`}
            data-testid="what-if-row"
            data-currency={r.currency}
            data-bucket={r.bucketCode}
            data-parent-turns-negative={flagState(r.parent?.turns_negative_net)}
            data-child-turns-negative={flagState(r.child?.turns_negative_net)}
          >
            <TableCell className="font-mono text-xs">{r.currency}</TableCell>
            <TableCell className="text-xs">
              {r.bucketLabel} <span className="font-mono text-muted-foreground">{r.bucketCode}</span>
            </TableCell>
            <TableCell className="text-right text-sm">
              <Delta cell={r.parent} figure="net_gap" testId="what-if-parent-net" />
            </TableCell>
            <TableCell className="text-right text-sm">
              <Delta cell={r.child} figure="net_gap" testId="what-if-child-net" />
            </TableCell>
            <TableCell className="text-right text-sm">
              <Delta cell={r.parent} figure="cumulative_gap" testId="what-if-parent-cumulative" />
            </TableCell>
            <TableCell className="text-right text-sm">
              <Delta cell={r.child} figure="cumulative_gap" testId="what-if-child-cumulative" />
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

/**
 * FE-25.2 "what if": this version next to its parent version, both run on the same baseline, the
 * combined run's deltas side by side. When the parent has not run on this baseline, it says so
 * and links to the scenario, where the analyst can run it (FE-25.1); nothing is run from here.
 */
export function WhatIfComparison({ scenarioId, comparison }: { scenarioId: string; comparison: ScenarioComparison }) {
  const [state, setState] = useState<Loaded<Resolved>>({ kind: 'loading' })
  const loader = useMemo(
    () => createLoader(resolveParent(scenarioId, comparison.version, comparison.baseline_run_id), setState, 'Could not load the parent version'),
    [scenarioId, comparison.version, comparison.baseline_run_id],
  )
  useEffect(() => {
    loader.load()
    return loader.abort
  }, [loader])

  return (
    <PageSection icon={GitBranch} title="What if: this version and its parent" description="The combined run's deltas, side by side, for the parent version and this one on the same baseline.">
      <WhatIfBody scenarioId={scenarioId} comparison={comparison} state={state} />
    </PageSection>
  )
}

/**
 * What the side by side shows for each resolved state (Q30): the chosen parent scenario run (its
 * ID, created time and link; the newest on the same baseline, no picker), or an empty state when
 * the parent version has no run on this baseline.
 */
export function WhatIfBody({ scenarioId, comparison, state }: { scenarioId: string; comparison: Pick<ScenarioComparison, 'version' | 'currencies'>; state: Loaded<Resolved> }) {
  let body
  if (state.kind === 'loading') body = <LoadingBlock rows={2} />
  else if (state.kind !== 'ready')
    body = (
      <p className="text-sm text-muted-foreground" data-testid="what-if-problem" data-kind={state.kind}>
        The parent version&apos;s comparison could not be read{state.kind === 'forbidden' ? ' (HTTP 403)' : ''}.{state.cid && ` Correlation ID: ${state.cid}`}
      </p>
    )
  else if (state.value.kind === 'no_parent')
    body = (
      <p className="text-sm text-muted-foreground" data-testid="what-if-none">
        v{comparison.version} has no parent version, so there is nothing to set beside it.
      </p>
    )
  else if (state.value.kind === 'no_parent_run')
    body = (
      <div data-testid="what-if-no-run" data-parent-version={state.value.parentVersion}>
        <EmptyState
          icon={GitBranch}
          title={`No run of parent v${state.value.parentVersion} on this baseline`}
          description={
            <>
              Only a parent run on the same baseline can be set beside this one. Run v{state.value.parentVersion} on this baseline from the{' '}
              <Link href={scenarioHref(scenarioId)} className="text-primary hover:underline">
                scenario
              </Link>{' '}
              to compare it here.
            </>
          }
        />
        {/* Q18: only the newest page of scenario runs was searched; an older parent run may exist beyond it. */}
        <TruncationNote note={state.value.truncation ?? null} testId="what-if-truncated" className="mt-2 text-center" />
      </div>
    )
  else {
    const { parentVersion, run, comparison: parent } = state.value
    body = (
      <div className="space-y-2" data-testid="what-if" data-parent-scenario-run-id={run.scenario_run_id}>
        <p className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          Parent v{parentVersion}: its newest scenario run on this baseline,{' '}
          <Link href={comparisonHref(scenarioId, run.scenario_run_id)} className="font-mono text-primary hover:underline" data-testid="what-if-parent-link">
            {run.scenario_run_id}
          </Link>
          <span data-testid="what-if-parent-created">created {formatTimestamp(run.created_at)}</span>
          <Badge variant={approvalVariant(parent.approval_state)}>{parent.approval_label}</Badge>
          <span>· same baseline, reporting date {parent.reporting_date}</span>
        </p>
        <WhatIfTable rows={whatIfRows(comparison, parent)} parentVersion={parentVersion} version={comparison.version} />
      </div>
    )
  }
  return body
}
