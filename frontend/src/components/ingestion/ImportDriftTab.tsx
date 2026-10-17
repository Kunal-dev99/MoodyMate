'use client'

import Link from 'next/link'
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { FilePlus2, GitCompareArrows } from 'lucide-react'
import { api, call } from '@/api/client'
import { Forbidden } from '@/components/auth/Forbidden'
import { RequirePermission } from '@/components/auth/RequirePermission'
import { EmptyState } from '@/components/common/EmptyState'
import { LoadFailed } from '@/components/common/LoadFailed'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { MaskedValue, isMasked } from '@/components/common/MaskedValue'
import { PageSection } from '@/components/common/PageSection'
import { startLoad, type LoadFailure } from '@/components/common/load'
import { DriftStatusPill, DriftTypePill } from '@/components/ingestion/DriftPills'
import { DriftReviewDialog } from '@/components/ingestion/DriftReviewDialog'
import type { ImportDetail } from '@/components/ingestion/imports'
import {
  DRIFT_STATUSES,
  DRIFT_TYPE_STYLE,
  driftProposalHref,
  thresholdFields,
  type DriftEvent,
  type DriftPage,
  type DriftSummary,
} from '@/components/ingestion/drift'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'

/*
 * FE-12.2: an import's drift alerts (DI-06) against its source's baseline. Reading is GET
 * only; the one write is the review dialog (quality:review, the server decides). Nothing
 * here changes a schema, contract or mapping: "Propose schema change" is a link to the
 * Phase 5 proposal page with the schema prefilled.
 */

type State = { kind: 'loading' } | { kind: 'ready'; page: DriftPage } | LoadFailure

const Mono = ({ children }: { children: ReactNode }) => <span className="break-all font-mono text-xs">{children}</span>

/** One statistic as the server sent it: text, "masked" by policy, or "—". */
function Stat({ value }: { value: DriftSummary['min'] | number | undefined }) {
  if (isMasked(value)) return <MaskedValue value={value} />
  if (value === null || value === undefined || value === '') return <span className="text-muted-foreground">—</span>
  return <span className="font-mono text-xs">{value}</span>
}

const STATS: { label: string; render: (s: DriftSummary) => ReactNode }[] = [
  { label: 'Position', render: (s) => <Stat value={s.position} /> },
  { label: 'Values', render: (s) => <Stat value={s.value_count} /> },
  { label: 'Nulls', render: (s) => <Stat value={s.null_count} /> },
  { label: 'Null rate', render: (s) => <Stat value={s.null_rate} /> },
  { label: 'Distinct', render: (s) => <Stat value={s.distinct_count} /> },
  {
    label: 'Type',
    render: (s) => <span className="text-xs">{s.suggested_type ? `Suggested: ${s.suggested_type}` : 'No type suggested'}</span>,
  },
  { label: 'Candidate types', render: (s) => <Mono>{s.candidate_types.length ? s.candidate_types.join(', ') : '—'}</Mono> },
  { label: 'Min', render: (s) => <Stat value={s.min} /> },
  { label: 'Max', render: (s) => <Stat value={s.max} /> },
]

function Comparison({ event }: { event: DriftEvent }) {
  const side = (s: DriftSummary | null, missing: string, render: (s: DriftSummary) => ReactNode) =>
    s ? render(s) : <span className="text-xs text-muted-foreground">{missing}</span>
  return (
    <Table data-testid="drift-comparison">
      <TableHeader>
        <TableRow>
          <TableHead className="w-40">Statistic</TableHead>
          <TableHead>Baseline</TableHead>
          <TableHead>This import</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {STATS.map((stat) => (
          <TableRow key={stat.label}>
            <TableCell className="text-xs text-muted-foreground">{stat.label}</TableCell>
            <TableCell data-testid="drift-baseline-stat">{side(event.baseline, 'not in the baseline', stat.render)}</TableCell>
            <TableCell data-testid="drift-observed-stat">{side(event.observed, 'not in this import', stat.render)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

function Review({ event }: { event: DriftEvent }) {
  if (!event.reviewed_by)
    return (
      <p className="text-sm text-muted-foreground" data-testid="drift-awaiting-review">
        Not reviewed yet. A person with <code className="font-mono">quality:review</code> records whether it is expected (Acknowledged) or needs a schema
        change (Action required).
      </p>
    )
  return (
    <div className="rounded-md border border-border bg-surface-2 px-3 py-2 text-sm" data-testid="drift-review">
      <div className="flex flex-wrap items-center gap-2">
        <DriftStatusPill status={event.status} />
        <span className="text-muted-foreground">by</span>
        <span className="font-mono text-xs" data-testid="drift-reviewed-by">
          {event.reviewed_by}
        </span>
        {event.reviewed_at && <span className="font-mono text-xs text-muted-foreground">· {new Date(event.reviewed_at).toLocaleString()}</span>}
      </div>
      <p className="mt-1 whitespace-pre-wrap break-words" data-testid="drift-review-comment">
        {event.review_comment}
      </p>
    </div>
  )
}

function ProposeLink({ event }: { event: DriftEvent }) {
  const link = event.proposal_link
  if (event.status !== 'ACTION_REQUIRED' || !link) return null
  return (
    <RequirePermission
      permission="schema:propose"
      fallback={
        <span className="text-xs text-muted-foreground" data-testid="drift-propose-unavailable">
          Proposing a schema change needs <code className="font-mono">schema:propose</code>.
        </span>
      }
    >
      <Button asChild size="sm" variant="outline">
        <Link href={driftProposalHref(link)} data-testid="drift-propose" data-schema-id={link.schema_id}>
          <FilePlus2 className="mr-2 h-4 w-4" />
          Propose schema change
        </Link>
      </Button>
    </RequirePermission>
  )
}

function DriftAlert({ event, onReviewed }: { event: DriftEvent; onReviewed: () => void }) {
  const t = thresholdFields(event.thresholds)
  return (
    <div
      className="rounded-md border border-border"
      data-testid="drift-event"
      data-id={event.id}
      data-drift-type={event.drift_type}
      data-column={event.column}
      data-status={event.status}
    >
      <div className="flex flex-wrap items-center gap-2 border-b border-border/40 px-4 py-3">
        <span className="font-mono text-xs text-muted-foreground">#{event.sequence}</span>
        <DriftTypePill type={event.drift_type} />
        <span className="font-mono text-sm" data-testid="drift-column">
          {event.column}
        </span>
        <span className="text-xs text-muted-foreground">{event.classification}</span>
        <DriftStatusPill status={event.status} />
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <RequirePermission permission="quality:review" fallback={null}>
            <DriftReviewDialog event={event} onReviewed={onReviewed} />
          </RequirePermission>
          <ProposeLink event={event} />
        </div>
      </div>
      <div className="space-y-3 px-4 py-3">
        <p className="text-sm text-muted-foreground">{DRIFT_TYPE_STYLE[event.drift_type].meaning}</p>
        <Comparison event={event} />
        <dl className="grid grid-cols-[200px_1fr] gap-y-1 text-sm">
          <dt className="text-muted-foreground">Baseline import</dt>
          <dd>
            <Link href={`/ingestion/imports/${event.baseline_import_id}`} className="font-mono text-xs text-primary hover:underline" data-testid="drift-baseline">
              {event.baseline_import_id}
            </Link>
          </dd>
          <dt className="text-muted-foreground">Detected</dt>
          <dd>
            <Mono>
              {new Date(event.created_at).toLocaleString()} · detector v{event.detector_version}
            </Mono>
          </dd>
          <dt className="text-muted-foreground">Thresholds</dt>
          <dd>
            <span className="font-mono text-xs">
              null rate {t.nullRate ?? '—'} · range {t.range ?? '—'}
            </span>
            {t.provisional && (
              <Badge variant="warning" className="ml-2">
                Provisional
              </Badge>
            )}
          </dd>
          <dt className="text-muted-foreground">Event ID</dt>
          <dd>
            <Mono>{event.id}</Mono>
          </dd>
        </dl>
        <Review event={event} />
      </div>
    </div>
  )
}

/** The Drift tab: the import's alerts against its baseline, each reviewed once by a person. */
export function ImportDriftTab({ job }: { job: ImportDetail }) {
  const [state, setState] = useState<State>({ kind: 'loading' })
  const request = useRef<AbortController | null>(null)
  const importId = job.id

  const load = useCallback(() => {
    request.current = startLoad(
      (signal) => call(() => api.GET('/api/v1/imports/{import_id}/drift', { params: { path: { import_id: importId } }, signal })),
      { onReady: (page) => setState({ kind: 'ready', page }), onFailure: setState, failureTitle: 'Could not load the drift alerts' },
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
        icon={GitCompareArrows}
        title="The drift alerts could not be loaded"
        cid={state.cid}
        onRetry={() => {
          setState({ kind: 'loading' })
          load()
        }}
      />
    )
  else if (state.kind === 'not_found')
    body = <EmptyState icon={GitCompareArrows} title="Drift not found" description={`The import does not exist or is not visible to you.${state.cid ? ` Correlation ID: ${state.cid}` : ''}`} />
  else {
    const { page } = state
    const counts = DRIFT_STATUSES.filter((s) => page.status_counts[s])
    body =
      page.items.length === 0 ? (
        <div data-testid="drift-none">
          <EmptyState
            icon={GitCompareArrows}
            title="No drift alerts"
            description="The profile matches its baseline, or there was nothing to compare with (no COMPLETED import of this source before it), or drift detection was switched off when it was profiled."
          />
        </div>
      ) : (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <span className="text-muted-foreground">Baseline</span>
            {page.baseline_import_id && (
              <Link href={`/ingestion/imports/${page.baseline_import_id}`} className="font-mono text-xs text-primary hover:underline">
                {page.baseline_import_id}
              </Link>
            )}
            <span className="text-muted-foreground">·</span>
            <span className="font-mono text-xs" data-testid="drift-total">
              {page.total}
            </span>
            <span className="text-muted-foreground">{page.total === 1 ? 'alert' : 'alerts'}</span>
            <div className="flex flex-wrap gap-2 text-xs" data-testid="drift-status-counts">
              {counts.map((s) => (
                <span key={s} className="flex items-center gap-1" data-status={s} data-count={page.status_counts[s]}>
                  <DriftStatusPill status={s} /> <span className="font-mono">{page.status_counts[s]}</span>
                </span>
              ))}
            </div>
          </div>
          {page.items.map((event) => (
            <DriftAlert key={event.id} event={event} onReviewed={load} />
          ))}
        </div>
      )
  }

  return (
    <div className="space-y-4" data-testid="drift-tab">
      <PageSection
        icon={GitCompareArrows}
        title="Drift alerts"
        description="How this import’s profile differs from its source’s baseline: the latest COMPLETED or COMPLETED_WITH_WARNINGS import of the same source (DI-06). Nothing changes a schema, contract or mapping automatically."
      >
        {body}
      </PageSection>
    </div>
  )
}
