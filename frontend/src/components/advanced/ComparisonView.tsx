'use client'

import { useState } from 'react'
import { FileText, FileWarning, GitCompare, ListOrdered, Scale, TrendingDown } from 'lucide-react'
import { Forbidden } from '@/components/auth/Forbidden'
import { EmptyState } from '@/components/common/EmptyState'
import { LoadFailed } from '@/components/common/LoadFailed'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { PageHeader } from '@/components/common/PageHeader'
import { PageSection } from '@/components/common/PageSection'
import { ResultContextPanel } from '@/components/calculations/ResultContextPanel'
import { ValidationStatusBadge } from '@/components/calculations/ValidationStatusBadge'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { approvalVariant, explorerHref, scenarioHref } from '@/lib/runs'
import { ComparisonDrilldown } from './ComparisonDrilldown'
import { ComparisonIdentity, MissingMembers } from './ComparisonIdentity'
import { FirstNegative, LargestChanges } from './ComparisonRankings'
import { ComparisonTable } from './ComparisonTable'
import { StressExplorerBanner } from './StressExplorerBanner'
import { WhatIfComparison } from './WhatIfComparison'
import { FIGURE_LABEL, METRICS, columnsOf, combinedOnlyBuckets, scenarioRunsHref, subsetKey, type DrillTarget, type Metric } from './comparison'
import { useComparison } from './useComparison'

/*
 * FE-35.2: the view's parts sit beside it, unchanged: the heat map (ComparisonTable.tsx), the
 * runs compared (ComparisonIdentity.tsx), the rankings (ComparisonRankings.tsx) and the loading
 * (useComparison.ts). The heat-map parts are re-exported from here, where they always lived.
 */
export { ComparisonTable, DeltaCell } from './ComparisonTable'

/**
 * FE-25.2: baseline versus stress for one scenario run (D-46), behind
 * NEXT_PUBLIC_STRESS_COMPARE_UI. The illustrative banner (the server's own statement) and the
 * approval label; the run IDs, reporting date and configuration versions; the templated (non-AI)
 * summary; the largest changes and the first negative cumulative bucket; per currency a heat map
 * of net or cumulative deltas with a column per shock alone and one combined; the drill-down to
 * the changed parts and their source rows; and, side by side, the parent version's run on the
 * same baseline. Every figure is the server's, masked by the server (`MaskedValue`).
 * FE-30.1: the validation status (D-56) beside the approval label, and the "Result context" panel.
 */
export function ComparisonView({ scenarioId, scenarioRunId }: { scenarioId: string; scenarioRunId: string }) {
  const { state, baseline, reload } = useComparison(scenarioId, scenarioRunId)
  const [metric, setMetric] = useState<Metric>('net_gap')
  const [drill, setDrill] = useState<DrillTarget | null>(null)

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
          {state.kind === 'failed' && <LoadFailed icon={GitCompare} title="Could not load the comparison" cid={state.cid} onRetry={reload} />}
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
          {/* FE-30.1: the compared module version's validation status (D-56). */}
          <ValidationStatusBadge status={c.validation_status} testId="comparison-validation-status" />
          <span className="text-xs text-muted-foreground">
            {columns.length} compared {columns.length === 1 ? 'run' : 'runs'} · {c.members.length} member {c.members.length === 1 ? 'run' : 'runs'}
          </span>
        </div>

        <PageSection icon={Scale} title="Runs compared" description="The scenario run, its baseline and each member run, with the configuration versions they pin.">
          <ComparisonIdentity comparison={c} baseline={baseline} />
        </PageSection>
        {/* FE-30.1: the comparison's context block (D-56 §5, from the baseline's pins), checked against the baseline run once it is read. */}
        <ResultContextPanel context={c.context} pins={baseline.kind === 'ready' ? baseline.value : null} source="comparison" />
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
