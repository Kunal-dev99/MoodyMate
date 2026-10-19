'use client'

import Link from 'next/link'
import { Fragment, useEffect, useMemo, useState } from 'react'
import { AlertTriangle, ChevronDown, ChevronRight, FileText, FileWarning, GitCompare, ListOrdered, Scale, TrendingDown } from 'lucide-react'
import { Forbidden } from '@/components/auth/Forbidden'
import { EmptyState } from '@/components/common/EmptyState'
import { LoadFailed } from '@/components/common/LoadFailed'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { MaskedValue, isMasked } from '@/components/common/MaskedValue'
import { PageHeader } from '@/components/common/PageHeader'
import { PageSection } from '@/components/common/PageSection'
import { createLoader, type Loaded } from '@/components/common/load'
import { RunIdentity } from '@/components/calculations/RunIdentity'
import { RunStatusPill } from '@/components/calculations/RunStatusPill'
import { hasResults, runHref, type RunDetail } from '@/components/calculations/calculations'
import { ShownValue } from '@/components/domain/ShownValue'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { cn } from '@/lib/utils'
import { ComparisonDrilldown } from './ComparisonDrilldown'
import { DeltaValue } from './DeltaValue'
import { StressExplorerBanner } from './StressExplorerBanner'
import { WhatIfComparison } from './WhatIfComparison'
import { approvalVariant, fetchBaseline } from './scenario-runs'
import { explorerHref, scenarioHref } from './scenarios'
import {
  FIGURE_LABEL,
  METRICS,
  bucketDays,
  columnLabel,
  columnsOf,
  combinedOnlyBuckets,
  combinedOnlyUnknown,
  fetchComparison,
  firstNegativeText,
  heatLevel,
  heatScale,
  scenarioRunsHref,
  subsetKey,
  toneOf,
  turnsNegative,
  type ComparisonBucket,
  type ComparisonCell,
  type ComparisonCurrency,
  type DrillTarget,
  type FigureKey,
  type FirstNegativeCumulative,
  type Metric,
  type ScenarioComparison,
  type Shown,
} from './comparison'

// Static class lists (Tailwind JIT): heat shades 1..4 for a fall (stress) and a rise.
const HEAT_NEGATIVE = ['', 'bg-destructive/10', 'bg-destructive/20', 'bg-destructive/30', 'bg-destructive/40']
const HEAT_POSITIVE = ['', 'bg-success/10', 'bg-success/20', 'bg-success/30', 'bg-success/40']

/**
 * One heat-map cell: the delta of the metric shown, shaded by its exact magnitude against the
 * currency's largest clear delta, with the server's turns-negative flag. A `null` flag reads a
 * masked figure: shown as unknown, never as "no".
 */
export function DeltaCell({
  cell,
  metric,
  max,
  active,
  onDrill,
}: {
  cell: ComparisonCell
  metric: Metric
  max: Shown | null
  active?: boolean
  onDrill?: () => void
}) {
  const delta = cell[metric].delta
  const tone = toneOf(delta)
  const level = heatLevel(delta, max)
  const flag = turnsNegative(cell, metric)
  return (
    <TableCell
      className={cn('text-right align-top text-sm', tone === 'negative' && HEAT_NEGATIVE[level], tone === 'positive' && HEAT_POSITIVE[level], active && 'ring-2 ring-inset ring-primary')}
      data-testid="comparison-cell"
      data-run-id={cell.calculation_run_id}
      data-subset={subsetKey(cell.shock_subset)}
      data-combined={cell.combined}
      data-metric={metric}
      data-delta={isMasked(delta) ? 'masked' : delta}
      data-heat={level}
      data-turns-negative={flag}
      data-changed={cell.changed_flow_count}
    >
      <DeltaValue value={delta} />
      <div className="mt-1 flex flex-wrap items-center justify-end gap-1">
        {flag === 'yes' && (
          <Badge variant="destructive" className="px-1.5 py-0 text-[10px]" data-testid="turns-negative">
            <TrendingDown className="mr-1 h-3 w-3" />
            Turns negative
          </Badge>
        )}
        {flag === 'unknown' && (
          <span className="text-[10px] text-muted-foreground" data-testid="turns-negative-unknown" title="The flag reads a masked figure, so it is not shown.">
            turns negative: masked
          </span>
        )}
        {onDrill && cell.changed_flow_count > 0 && (
          <Button size="sm" variant={active ? 'secondary' : 'ghost'} className="h-6 px-1.5 text-[11px]" onClick={onDrill} data-testid="comparison-drill">
            {cell.changed_flow_count} changed {cell.changed_flow_count === 1 ? 'flow' : 'flows'}
          </Button>
        )}
      </div>
    </TableCell>
  )
}

const FIGURES: readonly FigureKey[] = ['inflows', 'outflows', 'net_gap', 'cumulative_gap']

/** On expand: baseline, stressed and delta of every figure under every member run. */
function ExpandedFigures({ cells }: { cells: ComparisonCell[] }) {
  return (
    <div className="overflow-x-auto" data-testid="comparison-expanded">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Figure</TableHead>
            {cells.map((c) => (
              <TableHead key={c.calculation_run_id} colSpan={3} className="text-center">
                {columnLabel(c)}
              </TableHead>
            ))}
          </TableRow>
          <TableRow>
            <TableHead />
            {cells.map((c) => (
              <Fragment key={c.calculation_run_id}>
                <TableHead className="text-right text-xs">Baseline</TableHead>
                <TableHead className="text-right text-xs">Stressed</TableHead>
                <TableHead className="text-right text-xs">Δ</TableHead>
              </Fragment>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {FIGURES.map((f) => (
            <TableRow key={f} data-testid="comparison-figure" data-figure={f}>
              <TableCell className="text-xs">{FIGURE_LABEL[f]}</TableCell>
              {cells.map((c) => (
                <Fragment key={c.calculation_run_id}>
                  <TableCell className="text-right text-xs">
                    <ShownValue value={c[f].baseline} money testId="figure-baseline" />
                  </TableCell>
                  <TableCell className="text-right text-xs">
                    <ShownValue value={c[f].stressed} money testId="figure-stressed" />
                  </TableCell>
                  <TableCell className="text-right text-xs">
                    <DeltaValue value={c[f].delta} testId="figure-delta" />
                  </TableCell>
                </Fragment>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

/**
 * One currency's currency × bucket table and heat map (FE-25.2): a column per shock alone and one
 * for the combined run; turns-negative markers per cell, and a distinct highlight on a bucket
 * that breaks only when the shocks are combined. Rows expand to inflows and outflows.
 */
export function ComparisonTable({
  currency,
  buckets,
  metric,
  drill,
  onDrill,
  defaultExpanded = [],
}: {
  currency: ComparisonCurrency
  buckets: ComparisonBucket[]
  metric: Metric
  drill?: DrillTarget | null
  onDrill?: (target: DrillTarget) => void
  defaultExpanded?: string[]
}) {
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set(defaultExpanded))
  const max = heatScale(currency, metric)
  const columns = currency.rows[0]?.cells ?? []
  const toggle = (code: string) =>
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(code)) next.delete(code)
      else next.add(code)
      return next
    })
  return (
    <div className="space-y-2" data-testid="comparison-currency" data-currency={currency.currency}>
      <h3 className="font-mono text-sm font-medium">{currency.currency}</h3>
      <Table data-testid="comparison-table" data-currency={currency.currency} data-metric={metric}>
        <TableHeader>
          <TableRow>
            <TableHead className="w-8" />
            <TableHead>Bucket</TableHead>
            {columns.map((c) => (
              <TableHead key={c.calculation_run_id} className="text-right" data-testid="comparison-column" data-subset={subsetKey(c.shock_subset)} data-combined={c.combined}>
                {columnLabel(c)}
                <div className="text-[10px] font-normal text-muted-foreground">Δ {FIGURE_LABEL[metric].toLowerCase()}</div>
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {currency.rows.map((r) => {
            const open = expanded.has(r.bucket_code)
            const b = buckets.find((x) => x.code === r.bucket_code)
            const only = r.breaks_only_combined
            const unknown = combinedOnlyUnknown(r)
            return (
              <Fragment key={r.bucket_code}>
                <TableRow
                  className={cn(only === true && 'bg-warning/10 outline outline-2 -outline-offset-2 outline-warning')}
                  data-testid="comparison-row"
                  data-currency={currency.currency}
                  data-bucket={r.bucket_code}
                  data-combined-only={only === null ? 'null' : String(only)}
                >
                  <TableCell className="align-top">
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-6 w-6 p-0"
                      onClick={() => toggle(r.bucket_code)}
                      aria-expanded={open}
                      aria-label={`${open ? 'Hide' : 'Show'} inflows and outflows for ${r.bucket_label}`}
                      data-testid="comparison-expand"
                    >
                      {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                    </Button>
                  </TableCell>
                  <TableCell className="align-top text-sm">
                    {r.bucket_label}
                    <div className="font-mono text-xs text-muted-foreground">
                      {r.bucket_code}
                      {b && ` · ${bucketDays(b)}`}
                    </div>
                    {only === true && (
                      <Badge variant="warning" className="mt-1" data-testid="combined-only">
                        <AlertTriangle className="mr-1 h-3 w-3" />
                        Breaks only when combined
                      </Badge>
                    )}
                    {unknown === 'masked' && (
                      <div className="mt-1 text-[10px] text-muted-foreground" data-testid="combined-only-unknown" data-reason="masked" title="The flag reads a masked figure, so it is not shown.">
                        combined-only: masked
                      </div>
                    )}
                    {unknown === 'not_assessable' && (
                      <div
                        className="mt-1 text-[10px] text-muted-foreground"
                        data-testid="combined-only-unknown"
                        data-reason="not_assessable"
                        title="Fewer single-shock runs were compared than the scenario has shocks, so the flag cannot be assessed."
                      >
                        combined-only: not assessable (needs every single-shock run)
                      </div>
                    )}
                  </TableCell>
                  {r.cells.map((c) => (
                    <DeltaCell
                      key={c.calculation_run_id}
                      cell={c}
                      metric={metric}
                      max={max}
                      active={drill?.currency === currency.currency && drill.bucketCode === r.bucket_code && subsetKey(drill.shockSubset) === subsetKey(c.shock_subset)}
                      onDrill={
                        onDrill &&
                        (() => onDrill({ currency: currency.currency, bucketCode: r.bucket_code, bucketLabel: r.bucket_label, shockSubset: [...c.shock_subset], combined: c.combined }))
                      }
                    />
                  ))}
                </TableRow>
                {open && (
                  <TableRow data-testid="comparison-row-detail" data-bucket={r.bucket_code}>
                    <TableCell />
                    <TableCell colSpan={1 + r.cells.length} className="bg-surface-2">
                      <ExpandedFigures cells={r.cells} />
                    </TableCell>
                  </TableRow>
                )}
              </Fragment>
            )
          })}
        </TableBody>
      </Table>
    </div>
  )
}

/** A first-negative bucket: its code, "never negative", or masked. */
function FirstNegativeCode({ code, testId }: { code: FirstNegativeCumulative['baseline_bucket_code']; testId: string }) {
  if (isMasked(code))
    return (
      <span data-testid={testId} data-masked="true">
        <MaskedValue value={code} />
      </span>
    )
  return (
    <span className={cn('font-mono', code === null && 'text-muted-foreground')} data-testid={testId} data-value={code ?? 'null'}>
      {firstNegativeText(code)}
    </span>
  )
}

/** The first negative cumulative bucket, baseline versus each member run (illustrative, D-46 §2). */
function FirstNegative({ currencies }: { currencies: ComparisonCurrency[] }) {
  return (
    <Table data-testid="first-negative">
      <TableHeader>
        <TableRow>
          <TableHead>Currency</TableHead>
          <TableHead>Run</TableHead>
          <TableHead>Baseline</TableHead>
          <TableHead>Stressed</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {currencies.flatMap((c) =>
          c.first_negative_cumulative.map((f) => (
            <TableRow key={`${c.currency}-${subsetKey(f.shock_subset)}`} data-testid="first-negative-row" data-currency={c.currency} data-subset={subsetKey(f.shock_subset)} data-combined={f.combined}>
              <TableCell className="font-mono text-xs">{c.currency}</TableCell>
              <TableCell className="text-xs">{columnLabel(f)}</TableCell>
              <TableCell className="text-xs">
                <FirstNegativeCode code={f.baseline_bucket_code} testId="first-negative-baseline" />
              </TableCell>
              <TableCell className="text-xs">
                <FirstNegativeCode code={f.stressed_bucket_code} testId="first-negative-stressed" />
              </TableCell>
            </TableRow>
          )),
        )}
      </TableBody>
    </Table>
  )
}

/** The largest changes, ranked by the server over clear net deltas only (masked deltas are never ranked). */
function LargestChanges({ comparison, onDrill }: { comparison: ScenarioComparison; onDrill: (t: DrillTarget) => void }) {
  if (comparison.largest_changes.length === 0)
    return (
      <p className="text-sm text-muted-foreground" data-testid="largest-none">
        No clear net change to rank. Masked deltas are never ranked.
      </p>
    )
  return (
    <Table data-testid="largest-changes">
      <TableHeader>
        <TableRow>
          <TableHead className="w-12">#</TableHead>
          <TableHead>Currency</TableHead>
          <TableHead>Bucket</TableHead>
          <TableHead>Run</TableHead>
          <TableHead className="text-right">Δ net gap</TableHead>
          <TableHead />
        </TableRow>
      </TableHeader>
      <TableBody>
        {comparison.largest_changes.map((l) => {
          const label = comparison.buckets.find((b) => b.code === l.bucket_code)?.label ?? l.bucket_code
          return (
            <TableRow key={`${l.rank}`} data-testid="largest-change" data-rank={l.rank} data-currency={l.currency} data-bucket={l.bucket_code} data-subset={subsetKey(l.shock_subset)}>
              <TableCell className="font-mono text-xs">{l.rank}</TableCell>
              <TableCell className="font-mono text-xs">{l.currency}</TableCell>
              <TableCell className="text-xs">
                {label} <span className="font-mono text-muted-foreground">{l.bucket_code}</span>
              </TableCell>
              <TableCell className="text-xs">{columnLabel(l)}</TableCell>
              <TableCell className="text-right text-sm">
                <DeltaValue value={l.net_gap_delta} testId="largest-delta" />
              </TableCell>
              <TableCell className="text-right">
                <Button
                  size="sm"
                  variant="outline"
                  className="h-7"
                  onClick={() => onDrill({ currency: l.currency, bucketCode: l.bucket_code, bucketLabel: label, shockSubset: [...l.shock_subset], combined: l.combined })}
                  data-testid="largest-drill"
                >
                  Changed parts
                </Button>
              </TableCell>
            </TableRow>
          )
        })}
      </TableBody>
    </Table>
  )
}

/** The scenario run a comparison belongs to: run IDs, reporting date, version, approval state, member statuses and the baseline's pins. */
function ComparisonIdentity({ comparison, baseline }: { comparison: ScenarioComparison; baseline: Loaded<RunDetail> }) {
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
function MissingMembers({ comparison }: { comparison: ScenarioComparison }) {
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

const noFetch = () => new Promise<never>(() => undefined)

/**
 * FE-25.2: baseline versus stress for one scenario run (D-46), behind
 * NEXT_PUBLIC_STRESS_COMPARE_UI. The illustrative banner (the server's own statement) and the
 * approval label; the run IDs, reporting date and configuration versions; the templated (non-AI)
 * summary; the largest changes and the first negative cumulative bucket; per currency a heat map
 * of net or cumulative deltas with a column per shock alone and one combined; the drill-down to
 * the changed parts and their source rows; and, side by side, the parent version's run on the
 * same baseline. Every figure is the server's, masked by the server (`MaskedValue`).
 */
export function ComparisonView({ scenarioId, scenarioRunId }: { scenarioId: string; scenarioRunId: string }) {
  const [state, setState] = useState<Loaded<ScenarioComparison>>({ kind: 'loading' })
  const [baseline, setBaseline] = useState<Loaded<RunDetail>>({ kind: 'loading' })
  const [metric, setMetric] = useState<Metric>('net_gap')
  const [drill, setDrill] = useState<DrillTarget | null>(null)
  const loader = useMemo(() => createLoader(fetchComparison(scenarioId, scenarioRunId), setState, 'Could not load the comparison'), [scenarioId, scenarioRunId])
  useEffect(() => {
    loader.load()
    return loader.abort
  }, [loader])
  const baselineId = state.kind === 'ready' ? state.value.baseline_run_id : null
  const baselineLoader = useMemo(() => createLoader(baselineId ? fetchBaseline(baselineId) : noFetch, setBaseline, 'Could not load the baseline run'), [baselineId])
  useEffect(() => {
    if (!baselineId) return
    baselineLoader.load()
    return baselineLoader.abort
  }, [baselineId, baselineLoader])

  const crumbs = [
    { label: 'Advanced' },
    { label: 'Agentic Stress-Test Explorer', href: explorerHref },
    { label: 'Scenario', href: scenarioHref(scenarioId) },
    { label: 'Runs', href: scenarioRunsHref(scenarioId) },
    { label: 'Comparison' },
  ]

  if (state.kind !== 'ready') {
    return (
      <>
        <PageHeader title="Baseline versus stress" breadcrumbs={crumbs} />
        <div className="space-y-4 px-6 pb-6">
          {state.kind === 'loading' && <LoadingBlock rows={6} />}
          {state.kind === 'failed' && <LoadFailed icon={GitCompare} title="Could not load the comparison" cid={state.cid} onRetry={loader.load} />}
          {state.kind === 'forbidden' && <Forbidden permission="calc:read" correlationId={state.cid} />}
          {state.kind === 'not_found' && (
            <EmptyState icon={FileWarning} title="Scenario run not found" description={`It does not exist, belongs to another scenario or is not visible to you.${state.cid ? ` Correlation ID: ${state.cid}` : ''}`} />
          )}
        </div>
      </>
    )
  }

  const c = state.value
  const columns = columnsOf(c)
  const openDrill = (t: DrillTarget) => setDrill(t)
  return (
    <>
      <PageHeader
        title="Baseline versus stress"
        description={`Scenario version v${c.version} against baseline run ${c.baseline_run_id.slice(0, 8)}…, per currency and bucket. No cross-currency total.`}
        breadcrumbs={crumbs}
      />
      <div className="space-y-4 px-6 pb-6" data-testid="comparison-view" data-scenario-run-id={c.scenario_run_id} data-approval-state={c.approval_state}>
        <StressExplorerBanner statement={c.labels.statement} />
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={approvalVariant(c.approval_state)} data-testid="comparison-approval" data-approval-state={c.approval_state}>
            {c.approval_label}
          </Badge>
          {c.illustrative && (
            <Badge variant="outline" data-testid="comparison-illustrative">
              Illustrative
            </Badge>
          )}
          <span className="text-xs text-muted-foreground">
            {columns.length} compared {columns.length === 1 ? 'run' : 'runs'} · {c.members.length} member {c.members.length === 1 ? 'run' : 'runs'}
          </span>
        </div>

        <PageSection icon={Scale} title="Runs compared" description="The scenario run, its baseline and each member run, with the configuration versions they pin.">
          <ComparisonIdentity comparison={c} baseline={baseline} />
        </PageSection>
        <MissingMembers comparison={c} />

        <PageSection icon={FileText} title="Summary" description="Templated from the server's figures (deterministic, not AI). It cites clear figures only.">
          <div data-testid="comparison-summary" data-template-version={c.summary.template_version}>
            <Badge variant="outline" className="mb-2" data-testid="summary-not-ai">
              Templated summary · not AI · template {c.summary.template_version}
            </Badge>
            {c.summary.sentences.length === 0 ? (
              <p className="text-sm text-muted-foreground">The server returned no summary sentence.</p>
            ) : (
              <ul className="list-disc space-y-1 pl-5 text-sm">
                {c.summary.sentences.map((s, i) => (
                  <li key={i} data-testid="summary-sentence">
                    {s}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </PageSection>

        <PageSection icon={GitCompare} title="Baseline versus stress" description="Delta = stressed minus baseline, exactly, per currency and bucket. Darker shades are larger changes within the currency.">
          <div className="mb-3 flex flex-wrap items-center gap-2" role="group" aria-label="Metric">
            {METRICS.map((m) => (
              <Button key={m} size="sm" variant={metric === m ? 'default' : 'outline'} onClick={() => setMetric(m)} data-testid="comparison-metric" data-metric={m} aria-pressed={metric === m}>
                Δ {FIGURE_LABEL[m].toLowerCase()}
              </Button>
            ))}
            <span className="text-xs text-muted-foreground">Expand a bucket for inflows and outflows.</span>
          </div>
          {c.currencies.length === 0 ? (
            <EmptyState icon={GitCompare} title="Nothing to compare" description="The baseline has no ladder rows, or no member run succeeded." />
          ) : (
            <div className="space-y-6">
              {c.currencies.map((cur) => {
                const only = combinedOnlyBuckets(cur)
                return (
                  <div key={cur.currency} className="space-y-1">
                    <ComparisonTable currency={cur} buckets={c.buckets} metric={metric} drill={drill} onDrill={openDrill} />
                    {only.length > 0 && (
                      <p className="text-xs text-muted-foreground" data-testid="combined-only-note">
                        {cur.currency}: {only.join(', ')} {only.length === 1 ? 'breaks' : 'break'} only when the shocks are combined.
                      </p>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </PageSection>

        {drill && <ComparisonDrilldown key={`${drill.currency}-${drill.bucketCode}-${subsetKey(drill.shockSubset)}`} scenarioId={scenarioId} scenarioRunId={c.scenario_run_id} target={drill} onClose={() => setDrill(null)} />}

        <div className="grid gap-4 lg:grid-cols-2">
          <PageSection icon={ListOrdered} title="Largest changes" description="Ranked by absolute net delta, clear deltas only.">
            <LargestChanges comparison={c} onDrill={openDrill} />
          </PageSection>
          <PageSection icon={TrendingDown} title="First negative cumulative bucket" description="Illustrative. Baseline versus each run; masked when the deciding figure is masked.">
            <FirstNegative currencies={c.currencies} />
          </PageSection>
        </div>

        <WhatIfComparison scenarioId={scenarioId} comparison={c} />
      </div>
    </>
  )
}
