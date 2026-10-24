'use client'

import Link from 'next/link'
import { useEffect, useMemo, useState } from 'react'
import { GitCompare, History } from 'lucide-react'
import { Forbidden } from '@/components/auth/Forbidden'
import { EmptyState } from '@/components/common/EmptyState'
import { LoadFailed } from '@/components/common/LoadFailed'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { PageSection } from '@/components/common/PageSection'
import { createLoader, type Loaded } from '@/components/common/load'
import { ListPager, cursorRefusedBody, useCursorPaging } from '@/components/common/ListPager'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { LIST_PAGING_UI_ENABLED, STRESS_COMPARE_UI_ENABLED } from '@/config/features'
import { formatTimestamp } from '@/lib/format'
import { runHref } from '@/lib/runs'
import { approvalVariant, memberStatusText } from './scenario-runs'
import { comparisonHref, fetchScenarioRuns, type ScenarioRunPage } from './comparison'

/** The scenario's stressed runs, newest first, each with its approval label, baseline, member statuses and a link to its comparison. */
export function ScenarioRunsTable({ scenarioId, page }: { scenarioId: string; page: ScenarioRunPage }) {
  if (page.items.length === 0)
    return <EmptyState icon={GitCompare} title="No run yet" description="A version confirmed for exploration or approved for test runs against a baseline from the scenario page." />
  return (
    <Table data-testid="scenario-runs" data-total={page.total}>
      <TableHeader>
        <TableRow>
          <TableHead>Scenario run</TableHead>
          <TableHead>Version</TableHead>
          <TableHead>Label</TableHead>
          <TableHead>Baseline</TableHead>
          <TableHead>Reporting date</TableHead>
          <TableHead>Member runs</TableHead>
          <TableHead />
        </TableRow>
      </TableHeader>
      <TableBody>
        {page.items.map((r) => (
          <TableRow key={r.scenario_run_id} data-testid="scenario-run-row" data-id={r.scenario_run_id} data-version={r.version} data-approval-state={r.approval_state}>
            <TableCell className="text-xs">
              <span className="font-mono">{r.scenario_run_id.slice(0, 8)}…</span>
              <div className="text-muted-foreground">{formatTimestamp(r.created_at)}</div>
            </TableCell>
            <TableCell className="font-mono text-xs">v{r.version}</TableCell>
            <TableCell>
              <Badge variant={approvalVariant(r.approval_state)} data-testid="scenario-run-label">
                {r.approval_label}
              </Badge>
            </TableCell>
            <TableCell>
              <Link href={runHref(r.baseline_run_id)} className="font-mono text-xs text-primary hover:underline">
                {r.baseline_run_id.slice(0, 8)}…
              </Link>
            </TableCell>
            <TableCell className="font-mono text-xs">{r.reporting_date}</TableCell>
            <TableCell className="text-xs" data-testid="scenario-run-member-statuses">
              {memberStatusText(r.members)}
            </TableCell>
            <TableCell className="text-right">
              <Button asChild size="sm" variant="outline" data-testid="scenario-run-compare">
                <Link href={comparisonHref(scenarioId, r.scenario_run_id)}>Compare with baseline</Link>
              </Button>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

/**
 * FE-25.2: the scenario's runs with a link to each comparison (GET scenario-runs?scenario_id=,
 * calc:read). Renders nothing while NEXT_PUBLIC_STRESS_COMPARE_UI=off, so a page that mounts it
 * keeps its Phase 24 look (D-02).
 */
export function ScenarioRunsSection({ scenarioId }: { scenarioId: string }) {
  // FE-33.1: the server's next_cursor, bound to this scenario (NEXT_PUBLIC_LIST_PAGING_UI=off: the newest page only).
  const cursorPaging = useCursorPaging(scenarioId)
  const cursor = LIST_PAGING_UI_ENABLED ? cursorPaging.cursor : null
  const [state, setState] = useState<Loaded<ScenarioRunPage>>({ kind: 'loading' })
  const loader = useMemo(() => createLoader(fetchScenarioRuns(scenarioId, undefined, cursor), setState, 'Could not load the scenario runs'), [scenarioId, cursor])
  useEffect(() => {
    if (!STRESS_COMPARE_UI_ENABLED) return
    loader.load()
    return loader.abort
  }, [loader])
  if (!STRESS_COMPARE_UI_ENABLED) return null
  return (
    <PageSection icon={History} title="Runs against a baseline" description="Each scenario run: its single-shock runs and its combined run, compared with the baseline maturity ladder.">
      {state.kind === 'loading' && <LoadingBlock rows={2} />}
      {state.kind === 'failed' && <LoadFailed icon={History} title="Could not load the scenario runs" cid={state.cid} onRetry={loader.load} />}
      {state.kind === 'forbidden' && <Forbidden permission="calc:read" correlationId={state.cid} />}
      {state.kind === 'not_found' &&
        (cursorRefusedBody(state, cursorPaging, 'scenario-run-pager') ?? <EmptyState icon={History} title="Scenario runs not available" description={state.cid ? `Correlation ID: ${state.cid}` : undefined} />)}
      {state.kind === 'ready' && <ScenarioRunsTable scenarioId={scenarioId} page={state.value} />}
      {state.kind === 'ready' && LIST_PAGING_UI_ENABLED && state.value.items.length > 0 && (
        <ListPager page={state.value} paging={cursorPaging} testId="scenario-run-pager" />
      )}
    </PageSection>
  )
}
