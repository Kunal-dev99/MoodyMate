'use client'

import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { AlertTriangle, Calculator, CheckCircle2, ClipboardList, XCircle } from 'lucide-react'
import { api, call } from '@/api/client'
import { Forbidden } from '@/components/auth/Forbidden'
import { EmptyState } from '@/components/common/EmptyState'
import { LoadFailed } from '@/components/common/LoadFailed'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { PageHeader } from '@/components/common/PageHeader'
import { PageSection } from '@/components/common/PageSection'
import { createLoader, type Loaded } from '@/components/common/load'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { cn } from '@/lib/utils'
import { IllustrativeBanner } from './IllustrativeBanner'
import { LiquidityView } from './LiquidityView'
import { ManifestPanel } from './ManifestPanel'
import { MaturityLadderView } from './MaturityLadderView'
import { RunIdentity } from './RunIdentity'
import { RunStatusPill } from './RunStatusPill'
import { StressedRunBadge } from './StressedRunBadge'
import { POST_CHECK_TEXT, RUN_STATUS_STYLE, SEVERITY_VARIANT, WARNING_TEXT, hasResults, showsLiquidity, type RunDetail, type RunWarning } from './calculations'
import { formatTimestamp } from '@/lib/format'
import { useActorText } from '@/components/common/ActorLabel'

const CRUMBS = [{ label: 'Calculations', href: '/calculations' }]

/** GET /calculation-runs/{run_id} (calc:read). */
export const fetchRun = (runId: string) => (signal: AbortSignal) =>
  call(() => api.GET('/api/v1/calculation-runs/{run_id}', { params: { path: { run_id: runId } }, signal }))

const Mono = ({ children }: { children: ReactNode }) => <span className="break-all font-mono text-xs">{children}</span>

function Field({ label, children, testId }: { label: string; children: ReactNode; testId?: string }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0" data-testid={testId}>
        {children}
      </dd>
    </>
  )
}

/** What the server's status means for the figures below; never a success the server did not state. */
function StatusNotice({ run }: { run: RunDetail }) {
  if (hasResults(run.status)) return null
  const variant = run.status === 'RUNNING' || run.status === 'QUEUED' ? 'info' : 'destructive'
  return (
    <Alert variant={variant} data-testid="run-status-notice" data-status={run.status}>
      <AlertTriangle className="h-4 w-4" />
      <AlertTitle>{RUN_STATUS_STYLE[run.status].label}: no result</AlertTitle>
      <AlertDescription className="text-foreground">{RUN_STATUS_STYLE[run.status].description}</AlertDescription>
    </Alert>
  )
}

/** The outcome counts' labels: the ladder's (FE-15.2), or the LCR's inputs (FE-17.1). */
const OUTCOME_LABELS = {
  ladder: { in_scope: 'Cash flows in scope', bucketed: 'Bucketed', excluded: 'Excluded with a warning' },
  lcr: { in_scope: 'Positions and cash flows in scope', bucketed: 'Used (assets, outflows, inflows)', excluded: 'Excluded with a warning' },
} as const

function Outcome({ run }: { run: RunDetail }) {
  const o = run.outcome
  const lcr = showsLiquidity(run)
  const labels = OUTCOME_LABELS[lcr ? 'lcr' : 'ladder']
  return (
    <PageSection icon={ClipboardList} title="Outcome" description="Value-free counts and the engine's post-checks, as the server recorded them when the run ended.">
      {!o ? (
        <p className="text-sm text-muted-foreground" data-testid="outcome-none">
          Set when the run ends.
        </p>
      ) : (
        <div className="grid gap-6 lg:grid-cols-2" data-testid="run-outcome">
          <dl className="grid grid-cols-[200px_1fr] gap-y-1 text-sm">
            <Field label={labels.in_scope} testId="outcome-in-scope">
              <span className="font-mono">{o.in_scope}</span>
            </Field>
            <Field label={labels.bucketed} testId="outcome-bucketed">
              <span className="font-mono">{o.bucketed}</span>
            </Field>
            <Field label={labels.excluded} testId="outcome-excluded">
              <span className="font-mono">{o.excluded}</span>
            </Field>
            {lcr && (
              <Field label="Cash flows not inflows by rule" testId="outcome-out-of-scope">
                <span className="font-mono">{o.out_of_scope ?? '—'}</span>
              </Field>
            )}
            <Field label="Rows read">
              <span className="font-mono text-xs">
                {Object.entries(o.inputs)
                  .sort(([a], [b]) => a.localeCompare(b))
                  .map(([code, n]) => `${code}: ${n}`)
                  .join(' · ') || '—'}
              </span>
            </Field>
            <Field label="Currencies">
              <span className="font-mono text-xs">{o.currencies.join(', ') || '—'}</span>
            </Field>
            <Field label="Warnings by code">
              <span className="font-mono text-xs">
                {Object.entries(o.warnings_by_code)
                  .sort(([a], [b]) => a.localeCompare(b))
                  .map(([code, n]) => `${code}: ${n}`)
                  .join(' · ') || 'none'}
              </span>
            </Field>
          </dl>
          <ul className="space-y-1 text-sm" data-testid="post-checks">
            {o.post_checks.map((c) => (
              <li key={c.check} className="flex items-start gap-2" data-testid="post-check" data-check={c.check} data-passed={c.passed}>
                {c.passed ? (
                  <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-[hsl(var(--success))]" />
                ) : (
                  <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
                )}
                <span>
                  <span className="font-mono text-xs">{c.check}</span> <span className={cn(!c.passed && 'text-destructive')}>{c.passed ? 'passed' : 'failed'}</span>
                  <span className="block text-xs text-muted-foreground">{POST_CHECK_TEXT[c.check]}</span>
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </PageSection>
  )
}

function Warnings({ warnings }: { warnings: RunWarning[] }) {
  return (
    <PageSection
      icon={AlertTriangle}
      title={`Warnings (${warnings.length})`}
      description="Every exclusion, block and failure, with a code; nothing is dropped silently. Messages and details are value-free."
    >
      {warnings.length === 0 ? (
        <p className="text-sm text-muted-foreground" data-testid="warnings-none">
          No warnings.
        </p>
      ) : (
        <Table data-testid="run-warnings">
          <TableHeader>
            <TableRow>
              <TableHead className="w-12">#</TableHead>
              <TableHead>Severity</TableHead>
              <TableHead>Code</TableHead>
              <TableHead>Message</TableHead>
              <TableHead>Entity</TableHead>
              <TableHead>Details</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {warnings.map((w) => (
              <TableRow key={w.sequence} data-testid="run-warning" data-code={w.code} data-severity={w.severity}>
                <TableCell className="font-mono text-xs">{w.sequence}</TableCell>
                <TableCell>
                  <Badge variant={SEVERITY_VARIANT[w.severity]}>{w.severity}</Badge>
                </TableCell>
                <TableCell className="text-xs">
                  <span className="font-mono">{w.code}</span>
                  <div className="text-muted-foreground">{WARNING_TEXT[w.code]}</div>
                </TableCell>
                <TableCell className="text-sm">{w.message}</TableCell>
                <TableCell className="text-xs">
                  {w.entity_type ? <span className="font-mono">{w.entity_type}</span> : <span className="text-muted-foreground">—</span>}
                  {w.entity_id && <div className="font-mono text-muted-foreground">{w.entity_id}</div>}
                </TableCell>
                <TableCell>
                  {Object.keys(w.details).length === 0 ? (
                    <span className="text-xs text-muted-foreground">—</span>
                  ) : (
                    <pre className="max-w-[320px] overflow-auto whitespace-pre-wrap font-mono text-[11px]">{JSON.stringify(w.details)}</pre>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </PageSection>
  )
}

/**
 * FE-15.2: one run. Its identity and status (the server's), the illustrative label, the
 * outcome and post-checks, the manifest with every pin, the warnings, and the maturity ladder
 * with the contributions drill-down to the instrument, the import and the source row.
 * FE-17.1: an `lcr_hqla` run shows the liquidity view in the ladder's place (unless
 * NEXT_PUBLIC_LIQUIDITY_UI=off, which restores the Phase 15 layout exactly).
 */
export function RunView({ runId }: { runId: string }) {
  const actorText = useActorText()
  const [state, setState] = useState<Loaded<RunDetail>>({ kind: 'loading' })
  const loader = useMemo(() => createLoader(fetchRun(runId), setState, 'Could not load the run'), [runId])

  useEffect(() => {
    loader.load()
    return () => loader.abort()
  }, [loader])

  const wrap = (body: ReactNode) => (
    <>
      <PageHeader title="Calculation run" breadcrumbs={[...CRUMBS, { label: runId.slice(0, 8) }]} />
      <div className="px-6 pb-6">{body}</div>
    </>
  )

  if (state.kind === 'loading') return wrap(<LoadingBlock rows={6} />)
  if (state.kind === 'forbidden') return wrap(<Forbidden permission="calc:read" correlationId={state.cid} />)
  if (state.kind === 'failed') return wrap(<LoadFailed icon={Calculator} title="The run could not be loaded" cid={state.cid} onRetry={loader.load} />)
  if (state.kind === 'not_found')
    return wrap(
      <EmptyState
        icon={Calculator}
        title="Run not found"
        description={`It does not exist or is not visible to you.${state.cid ? ` Correlation ID: ${state.cid}` : ''}`}
      />,
    )

  const run = state.value
  return (
    <>
      <PageHeader
        title={`Run ${run.run_id.slice(0, 8)} · ${run.module.name}`}
        description={`${run.module.code} v${run.module.version} for reporting date ${run.reporting_date}. Started ${formatTimestamp(run.started_at)} by ${actorText(run.created_by)}${
          run.ended_at ? `, ended ${formatTimestamp(run.ended_at)}` : ''
        }.`}
        breadcrumbs={[...CRUMBS, { label: run.run_id.slice(0, 8) }]}
        actions={<RunStatusPill status={run.status} testId="run-detail-status" />}
      />
      <div className="space-y-4 px-6 pb-6" data-testid="run-detail" data-run-id={run.run_id} data-status={run.status}>
        <IllustrativeBanner statement={run.statement} />
        <RunIdentity run={run} />
        <StatusNotice run={run} />
        <PageSection icon={Calculator} title="Run" description="Immutable: the run moved once, from RUNNING to the status the server set.">
          <dl className="grid grid-cols-[200px_1fr] gap-y-1 text-sm" data-testid="run-fields">
            <Field label="Run ID" testId="run-id">
              <Mono>{run.run_id}</Mono>
            </Field>
            <Field label="Status">
              <RunStatusPill status={run.status} testId="run-field-status" />
              <span className="ml-2 text-xs text-muted-foreground">{RUN_STATUS_STYLE[run.status].description}</span>
            </Field>
            <Field label="Reporting date" testId="run-reporting-date">
              <Mono>{run.reporting_date}</Mono>
            </Field>
            <Field label="Module version">
              <Mono>
                {run.module.code} v{run.module.version} · {run.module.module_version_id}
              </Mono>
            </Field>
            <Field label="Warnings" testId="run-warning-count">
              <Mono>{run.warning_count}</Mono>
            </Field>
            <Field label="Correlation ID">
              <Mono>{run.correlation_id ?? '—'}</Mono>
            </Field>
            {run.rerun_of_run_id && (
              <Field label="Re-run of">
                <Mono>{run.rerun_of_run_id}</Mono>
              </Field>
            )}
            {/* FE-25.1: stressed runs only (Phase 25); a Feature 03 run shows no such rows. */}
            {run.scenario && (
              <>
                <Field label="Stress scenario" testId="run-scenario">
                  <StressedRunBadge scenario={run.scenario} />
                </Field>
                <Field label="Baseline run" testId="run-baseline">
                  <Mono>{run.scenario.baseline_run_id}</Mono>
                </Field>
                <Field label="Scenario run" testId="run-scenario-run">
                  <Mono>
                    {run.scenario.scenario_run_id} · version {run.scenario.version} ({run.scenario.scenario_version_id})
                  </Mono>
                </Field>
              </>
            )}
          </dl>
        </PageSection>
        <Outcome run={run} />
        {showsLiquidity(run) ? <LiquidityView run={run} /> : <MaturityLadderView run={run} />}
        <Warnings warnings={run.warnings} />
        <ManifestPanel run={run} />
      </div>
    </>
  )
}
