import Link from 'next/link'
import { AlertTriangle } from 'lucide-react'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import type { Loaded } from '@/components/common/load'
import { RunIdentity } from '@/components/calculations/RunIdentity'
import { RunStatusPill } from '@/components/calculations/RunStatusPill'
import type { RunDetail } from '@/components/calculations/calculations'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { hasResults, runHref } from '@/lib/runs'
import { columnLabel, subsetKey, type ScenarioComparison } from './comparison'

/*
 * FE-25.2 (split out of ComparisonView.tsx by FE-35.2, unchanged): which runs a comparison
 * compares. Every status is the server's: a member run without results is never shown as a
 * success.
 */

/** The scenario run a comparison belongs to: run IDs, reporting date, version, approval state, member statuses and the baseline's pins. */
export function ComparisonIdentity({ comparison, baseline }: { comparison: ScenarioComparison; baseline: Loaded<RunDetail> }) {
  return (
    <div className="space-y-2" data-testid="comparison-identity" data-scenario-run-id={comparison.scenario_run_id} data-reporting-date={comparison.reporting_date}>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-md border border-border bg-surface-2 px-3 py-2 text-xs">
        <span>
          <span className="text-muted-foreground">Scenario run </span>
          <span className="font-mono" data-testid="comparison-scenario-run-id">
            {comparison.scenario_run_id}
          </span>
        </span>
        <span>
          <span className="text-muted-foreground">Reporting date </span>
          <span className="font-mono" data-testid="comparison-reporting-date">
            {comparison.reporting_date}
          </span>
        </span>
        <span>
          <span className="text-muted-foreground">Scenario version </span>
          <span className="font-mono" data-testid="comparison-version">
            v{comparison.version}
          </span>{' '}
          <span className="font-mono text-muted-foreground">{comparison.scenario_version_id}</span>
        </span>
        <span>
          <span className="text-muted-foreground">Baseline </span>
          <Link href={runHref(comparison.baseline_run_id)} className="font-mono text-primary hover:underline" data-testid="comparison-baseline-link">
            {comparison.baseline_run_id}
          </Link>
        </span>
        <span>
          <span className="text-muted-foreground">Min aggregate count </span>
          <span className="font-mono">{comparison.min_aggregate_count}</span>
        </span>
      </div>
      {baseline.kind === 'ready' ? (
        <div>
          <p className="mb-1 text-xs text-muted-foreground">Baseline configuration versions (every stressed run pins the same ones):</p>
          <RunIdentity run={baseline.value} />
        </div>
      ) : baseline.kind === 'loading' ? (
        <LoadingBlock rows={1} />
      ) : (
        <p className="text-xs text-muted-foreground" data-testid="comparison-baseline-problem" data-kind={baseline.kind}>
          The baseline run&apos;s configuration versions could not be read ({baseline.kind === 'forbidden' ? 'HTTP 403' : baseline.kind}).
          {baseline.cid && <> Correlation ID: {baseline.cid}</>}
        </p>
      )}
      <Table data-testid="comparison-members">
        <TableHeader>
          <TableRow>
            <TableHead>Member run</TableHead>
            <TableHead>Shocks</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Manifest SHA-256</TableHead>
            <TableHead>Result SHA-256</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {comparison.members.map((m) => (
            <TableRow key={m.calculation_run_id} data-testid="comparison-member" data-run-id={m.calculation_run_id} data-status={m.status} data-subset={subsetKey(m.shock_subset)}>
              <TableCell>
                <Link href={runHref(m.calculation_run_id)} className="font-mono text-xs text-primary hover:underline" data-testid="comparison-member-link">
                  {m.calculation_run_id}
                </Link>
              </TableCell>
              <TableCell className="text-xs">{columnLabel(m)}</TableCell>
              <TableCell>
                <RunStatusPill status={m.status} testId="comparison-member-status" />
              </TableCell>
              <TableCell className="font-mono text-xs" title={m.manifest_sha256}>
                {m.manifest_sha256.slice(0, 16)}…
              </TableCell>
              <TableCell className="font-mono text-xs" title={m.result_sha256 ?? undefined}>
                {m.result_sha256 ? `${m.result_sha256.slice(0, 16)}…` : '—'}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

/** Member runs without results (FAILED, BLOCKED, …): never compared, never shown as a success. */
export function MissingMembers({ comparison }: { comparison: Pick<ScenarioComparison, 'members'> }) {
  const missing = comparison.members.filter((m) => !hasResults(m.status))
  if (missing.length === 0) return null
  return (
    <Alert variant="warning" data-testid="comparison-missing-members">
      <AlertTriangle className="h-4 w-4" />
      <AlertTitle>Not every member run has results</AlertTitle>
      <AlertDescription className="text-foreground">
        {missing.map((m) => `${columnLabel(m)} ended ${m.status}`).join('; ')}. Its column is absent: only runs the server reports as succeeded are compared.
      </AlertDescription>
    </Alert>
  )
}
