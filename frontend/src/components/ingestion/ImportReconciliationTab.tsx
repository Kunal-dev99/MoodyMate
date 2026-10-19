'use client'

import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { ListChecks, Scale } from 'lucide-react'
import { api, call } from '@/api/client'
import { Forbidden } from '@/components/auth/Forbidden'
import { EmptyState } from '@/components/common/EmptyState'
import { LoadFailed } from '@/components/common/LoadFailed'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { MaskedValue } from '@/components/common/MaskedValue'
import { PageSection } from '@/components/common/PageSection'
import { startLoad, type LoadFailure } from '@/components/common/load'
import { ImportStatusPill } from '@/components/ingestion/ImportStatusPill'
import { ReconciliationItemStatusPill, ReconciliationOutcomePill } from '@/components/ingestion/ReconciliationPills'
import type { ImportDetail } from '@/components/ingestion/imports'
import {
  CHECK_LABEL,
  OUTCOME_STYLE,
  detailCounts,
  figureDisplay,
  isNotReconciled,
  policyFields,
  type ReconciliationItem,
  type ReconciliationRun,
} from '@/components/ingestion/reconciliation'
import { Badge } from '@/components/ui/badge'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { formatTimestamp } from '@/lib/format'
import { ActorLabel } from '@/components/common/ActorLabel'

/*
 * FE-12.1: an import's reconciliation (DI-05), read-only. Validation writes it once; this
 * tab only reads it (GET). Every figure is the server's exact decimal text and is rendered
 * as that text: nothing here parses, rounds or recomputes a figure, and the outcome is the
 * server's, never derived from the rows.
 */

type State = { kind: 'loading' } | { kind: 'ready'; run: ReconciliationRun | null } | LoadFailure

function Field({ label, children, testId }: { label: string; children: ReactNode; testId?: string }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd data-testid={testId}>{children}</dd>
    </>
  )
}

const Mono = ({ children }: { children: ReactNode }) => <span className="break-all font-mono text-xs">{children}</span>

/** One figure: the server's text as is, "masked" by policy, or "—" when there is none. */
function Figure({ value }: { value: ReconciliationItem['held'] }) {
  const d = figureDisplay(value)
  if (d.kind === 'masked') return <MaskedValue value={d.value} />
  if (d.kind === 'none') return <span className="text-muted-foreground">—</span>
  return <span className="whitespace-nowrap font-mono text-sm">{d.text}</span>
}

function Summary({ run, job }: { run: ReconciliationRun; job: ImportDetail }) {
  const policy = policyFields(run.tolerance_policy)
  const m = job.manifest
  return (
    <dl className="grid grid-cols-[220px_1fr] gap-y-1 text-sm" data-testid="recon-summary">
      <Field label="Reconciliation ID" testId="recon-id">
        <Mono>{run.reconciliation_id}</Mono>
      </Field>
      <Field label="Import ID">
        <Mono>{run.import_id}</Mono>
      </Field>
      <Field label="Import status">
        <ImportStatusPill status={run.import_status} />
      </Field>
      <Field label="Outcome">
        <span className="flex flex-wrap items-center gap-2">
          <ReconciliationOutcomePill outcome={run.outcome} />
          <span className="text-muted-foreground" data-testid="recon-outcome-meaning">
            {OUTCOME_STYLE[run.outcome].meaning}
          </span>
        </span>
      </Field>
      <Field label="Reconciled">
        <Mono>
          <ActorLabel actor={run.reconciled_by} /> · {formatTimestamp(run.reconciled_at)}
        </Mono>
      </Field>
      <Field label="Reconciler version">
        <Mono>v{run.reconciler_version}</Mono>
      </Field>
      <Field label="Tolerance policy" testId="recon-policy">
        <span className="font-mono text-xs">
          {policy.kind ?? '—'} · tolerance {policy.tolerance ?? '—'} · counts {policy.countTolerance ?? '—'}
          {policy.setting && <span className="text-muted-foreground"> · setting {policy.setting}</span>}
        </span>
        {policy.provisional && (
          <Badge variant="warning" className="ml-2">
            Provisional
          </Badge>
        )}
      </Field>
      {m && (
        <>
          <Field label="Manifest ID">
            <Mono>{m.manifest_id}</Mono>
          </Field>
          <Field label="Schema version">
            <span className="font-mono">
              {m.schema_code} v{m.schema_version}
            </span>{' '}
            <span className="font-mono text-xs text-muted-foreground">{m.schema_version_id}</span>
          </Field>
          <Field label="Mapping version">
            <span className="font-mono">
              {m.mapping_code} v{m.mapping_version}
            </span>{' '}
            <span className="font-mono text-xs text-muted-foreground">{m.mapping_version_id}</span>
          </Field>
          <Field label="Source contract">
            <span className="font-mono">
              {m.source_code} v{m.source_contract_version}
            </span>
          </Field>
        </>
      )}
    </dl>
  )
}

/** The checks table (exported for a render test of the exact figures). */
export function ReconciliationChecks({ items }: { items: ReconciliationItem[] }) {
  if (items.length === 0) return <EmptyState icon={ListChecks} title="No checks" description="The server recorded no reconciliation checks for this import." />
  return (
    <>
      <div className="overflow-x-auto">
        <Table data-testid="recon-table">
          <TableHeader>
            <TableRow>
              <TableHead>#</TableHead>
              <TableHead>Check</TableHead>
              <TableHead>Column</TableHead>
              <TableHead>Unit</TableHead>
              <TableHead className="text-right">Source</TableHead>
              <TableHead className="text-right">Target</TableHead>
              <TableHead className="text-right">Held</TableHead>
              <TableHead className="text-right">
                Variance
                <div className="text-xs font-normal">source − target</div>
              </TableHead>
              <TableHead className="text-right">Tolerance</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Values</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((i) => {
              const counts = detailCounts(i.details)
              return (
                <TableRow
                  key={i.sequence}
                  data-testid="recon-item"
                  data-sequence={i.sequence}
                  data-check={i.check}
                  data-column={i.column ?? ''}
                  data-status={i.status}
                >
                  <TableCell className="font-mono text-xs text-muted-foreground">{i.sequence}</TableCell>
                  <TableCell>
                    <div className="text-sm" title={CHECK_LABEL[i.check].compares}>
                      {CHECK_LABEL[i.check].label}
                    </div>
                    <div className="font-mono text-xs text-muted-foreground">{i.check}</div>
                  </TableCell>
                  <TableCell className="font-mono text-xs">{i.column ?? '—'}</TableCell>
                  <TableCell className="text-xs">
                    {i.unit ?? '—'}
                    <div className="text-muted-foreground">{i.classification}</div>
                  </TableCell>
                  <TableCell className="text-right" data-testid="recon-source">
                    <Figure value={i.source} />
                  </TableCell>
                  <TableCell className="text-right" data-testid="recon-target">
                    <Figure value={i.target} />
                  </TableCell>
                  <TableCell className="text-right" data-testid="recon-held">
                    <Figure value={i.held} />
                  </TableCell>
                  <TableCell className="text-right" data-testid="recon-variance">
                    <Figure value={i.variance} />
                  </TableCell>
                  <TableCell className="text-right" data-testid="recon-tolerance">
                    <Figure value={i.tolerance} />
                  </TableCell>
                  <TableCell>
                    <ReconciliationItemStatusPill status={i.status} />
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-xs text-muted-foreground" data-testid="recon-values">
                    {counts ? (
                      <div className="space-y-0.5">
                        <div>
                          <span className="font-mono">{counts.parseable}</span> parseable
                        </div>
                        <div>
                          <span className="font-mono">{counts.unparseable}</span> unparseable
                        </div>
                        <div>
                          <span className="font-mono">{counts.empty}</span> empty
                        </div>
                      </div>
                    ) : (
                      '—'
                    )}
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      </div>
      <p className="mt-3 text-xs text-muted-foreground">
        Figures are the server’s exact decimal text, never rounded or recomputed here. Held is the part of the gap that quarantined or rejected rows
        explain. Row counts always use zero tolerance; a role not cleared for a column’s classification sees its totals masked.
      </p>
    </>
  )
}

/** The Reconciliation tab: the run's outcome, policy and pinned versions, then one row per check. */
export function ImportReconciliationTab({ job }: { job: ImportDetail }) {
  const [state, setState] = useState<State>({ kind: 'loading' })
  const request = useRef<AbortController | null>(null)
  const importId = job.id

  const load = useCallback(() => {
    request.current = startLoad(
      (signal) =>
        call(() => api.GET('/api/v1/imports/{import_id}/reconciliation', { params: { path: { import_id: importId } }, signal })).catch((err: unknown) => {
          // Not validated yet: no reconciliation exists. An empty state, not an error.
          if (isNotReconciled(err)) return null
          throw err
        }),
      { onReady: (run) => setState({ kind: 'ready', run }), onFailure: setState, failureTitle: 'Could not load the reconciliation' },
      request.current,
    )
  }, [importId])

  useEffect(() => {
    setState({ kind: 'loading' })
    load()
    return () => request.current?.abort()
  }, [load])

  let body: ReactNode
  if (state.kind === 'loading') body = <LoadingBlock rows={4} />
  else if (state.kind === 'forbidden') body = <Forbidden permission="import:read" correlationId={state.cid} />
  else if (state.kind === 'failed')
    body = (
      <LoadFailed
        icon={Scale}
        title="The reconciliation could not be loaded"
        cid={state.cid}
        onRetry={() => {
          setState({ kind: 'loading' })
          load()
        }}
      />
    )
  else if (state.kind === 'not_found')
    body = <EmptyState icon={Scale} title="Reconciliation not found" description={`The import does not exist or is not visible to you.${state.cid ? ` Correlation ID: ${state.cid}` : ''}`} />
  else if (state.run === null)
    body = (
      <div data-testid="recon-not-run">
        <EmptyState
          icon={Scale}
          title="Not reconciled yet"
          description="Reconciliation runs when the import is validated (Validation tab), in the same transaction, and is never changed afterwards."
        />
      </div>
    )
  else body = <Summary run={state.run} job={job} />

  const run = state.kind === 'ready' ? state.run : null
  return (
    <div className="space-y-4" data-testid="reconciliation-tab">
      <PageSection
        icon={Scale}
        title="Reconciliation"
        description="Row-count conservation, the declared row count and each control total, as reconciled by the server at validation."
        headerRight={run ? <ReconciliationOutcomePill outcome={run.outcome} /> : undefined}
      >
        {body}
      </PageSection>
      {run && (
        <PageSection icon={ListChecks} title="Checks" description="One row per check, in the server’s order.">
          <ReconciliationChecks items={run.items} />
        </PageSection>
      )}
    </div>
  )
}
