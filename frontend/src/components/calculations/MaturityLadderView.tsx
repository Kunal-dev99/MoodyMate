'use client'

import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { ChevronLeft, ChevronRight, Layers, ListTree, X } from 'lucide-react'
import { ApiError, api, call } from '@/api/client'
import { Forbidden } from '@/components/auth/Forbidden'
import { EmptyState } from '@/components/common/EmptyState'
import { LoadFailed } from '@/components/common/LoadFailed'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { MaskedValue, isMasked } from '@/components/common/MaskedValue'
import { PageSection } from '@/components/common/PageSection'
import { createLoader, type Loaded } from '@/components/common/load'
import { ListPager, cursorRefusedBody, useCursorPaging } from '@/components/common/ListPager'
import { LIST_PAGING_UI_ENABLED } from '@/config/features'
import { ShownValue } from '@/components/domain/ShownValue'
import { importHref, instrumentHref } from '@/components/domain/domain'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { cn } from '@/lib/utils'
import { IllustrativeBanner } from './IllustrativeBanner'
import { ResultContextPanel } from './ResultContextPanel'
import { RunIdentity } from './RunIdentity'
import { followsRun } from './polling'
import { ResultsPending } from './RunPolling'
import { LadderFigureValue } from './LadderFigure'
import {
  bucketRange,
  drillFrom,
  drillHref,
  hasResults,
  ladderMaskHint,
  runHref,
  type Bucket,
  type LadderFigure,
  type LadderRow,
  type MaturityLadder,
  type ResultPage,
  type RunDetail,
} from './calculations'

const RESULTS_PAGE = 200

/** GET …/maturity-ladder; null for a run of another module (404 `maturity_ladder_not_found`). */
export const fetchLadder = (runId: string) => (signal: AbortSignal) =>
  call(() => api.GET('/api/v1/calculation-runs/{run_id}/maturity-ladder', { params: { path: { run_id: runId } }, signal })).catch((err: unknown) => {
    if (err instanceof ApiError && err.status === 404 && err.envelope.code === 'maturity_ladder_not_found') return null
    throw err
  })

/**
 * GET …/results for one ladder cell (currency and bucket), one page. `ResultPage` carries no
 * `context` block yet; once the contract adds one, show it here with `ResultContextPanel` and the
 * same non-blocking mismatch check against the run's pins (FE-30.1 ruling).
 */
export const fetchContributions = (runId: string, currency: string, bucket: string, offset: number, cursor: string | null = null) => (signal: AbortSignal) =>
  call(() =>
    api.GET('/api/v1/calculation-runs/{run_id}/results', {
      // FE-33.1: past the first page the server's next_cursor replaces the offset.
      params: { path: { run_id: runId }, query: { currency, bucket_code: bucket, limit: RESULTS_PAGE, ...(cursor ? { cursor } : { offset }) } },
      signal,
    }),
  )

/** An inflow / outflow pair in one cell (contractual or behavioural), with one masking hint for the pair. */
function SplitFigures({
  row,
  inflow,
  outflow,
  minAggregateCount,
  part,
}: {
  row: LadderRow
  inflow: LadderFigure
  outflow: LadderFigure
  minAggregateCount: number
  /** `contractual` or `behavioural`: names the test ids. */
  part: string
}) {
  const hint = ladderMaskHint(row, inflow, minAggregateCount) ?? ladderMaskHint(row, outflow, minAggregateCount)
  return (
    <span className="inline-flex flex-col items-end">
      <span>
        <ShownValue value={row[inflow]} testId={`ladder-inflows-${part}`} money /> /{' '}
        <ShownValue value={row[outflow]} testId={`ladder-outflows-${part}`} money />
      </span>
      {hint && (
        <span className="mt-0.5 text-[11px] leading-tight text-muted-foreground" data-testid={`ladder-${part}-hint`}>
          {hint}
        </span>
      )}
    </span>
  )
}

function LadderTable({
  run,
  currency,
  rows,
  buckets,
  selected,
  minAggregateCount,
}: {
  run: RunDetail
  currency: string
  rows: LadderRow[]
  buckets: Bucket[]
  selected: { currency: string; bucket: string } | null
  minAggregateCount: number
}) {
  const n = minAggregateCount
  const range = (code: string) => {
    const b = buckets.find((x) => x.code === code)
    return b ? bucketRange(b) : null
  }
  return (
    <div className="space-y-2" data-testid="ladder-currency" data-currency={currency}>
      <h3 className="font-mono text-sm font-medium">{currency}</h3>
      <Table data-testid="ladder-table" data-currency={currency}>
        <TableHeader>
          <TableRow>
            <TableHead>Bucket</TableHead>
            <TableHead className="text-right">Inflows</TableHead>
            <TableHead className="text-right">Outflows</TableHead>
            <TableHead className="text-right">Net gap</TableHead>
            <TableHead className="text-right">Cumulative gap</TableHead>
            <TableHead className="text-right">Contractual in / out</TableHead>
            <TableHead className="text-right">Behavioural in / out</TableHead>
            <TableHead className="text-right">Contributions</TableHead>
            <TableHead />
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((r) => {
            const active = selected?.currency === currency && selected.bucket === r.bucket_code
            return (
              <TableRow
                key={r.bucket_code}
                className={cn(active && 'bg-surface-2')}
                data-testid="ladder-row"
                data-currency={currency}
                data-bucket={r.bucket_code}
                data-count={r.contribution_count}
                data-result-id={r.liquidity_gap_result_id}
              >
                <TableCell className="text-sm">
                  {r.bucket_label}
                  <div className="font-mono text-xs text-muted-foreground">
                    {r.bucket_code}
                    {range(r.bucket_code) && ` · ${range(r.bucket_code)}`}
                  </div>
                </TableCell>
                <TableCell className="text-right text-sm">
                  <LadderFigureValue row={r} figure="inflows" minAggregateCount={n} testId="ladder-inflows" />
                </TableCell>
                <TableCell className="text-right text-sm">
                  <LadderFigureValue row={r} figure="outflows" minAggregateCount={n} testId="ladder-outflows" />
                </TableCell>
                <TableCell className="text-right text-sm">
                  <LadderFigureValue row={r} figure="net_gap" minAggregateCount={n} testId="ladder-net" />
                </TableCell>
                <TableCell className="text-right text-sm">
                  <LadderFigureValue row={r} figure="cumulative_gap" minAggregateCount={n} testId="ladder-cumulative" />
                </TableCell>
                <TableCell className="text-right text-xs">
                  <SplitFigures row={r} inflow="inflows_contractual" outflow="outflows_contractual" minAggregateCount={n} part="contractual" />
                </TableCell>
                <TableCell className="text-right text-xs">
                  <SplitFigures row={r} inflow="inflows_behavioural" outflow="outflows_behavioural" minAggregateCount={n} part="behavioural" />
                </TableCell>
                <TableCell className="text-right font-mono text-sm" data-testid="ladder-count">
                  {r.contribution_count}
                </TableCell>
                <TableCell className="text-right">
                  {r.contribution_count > 0 && (
                    <Button asChild size="sm" variant={active ? 'secondary' : 'outline'} data-testid="ladder-drill">
                      <Link href={drillHref(run.run_id, currency, r.bucket_code)} scroll={false}>
                        Contributions
                      </Link>
                    </Button>
                  )}
                </TableCell>
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
    </div>
  )
}

function Lineage({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="whitespace-nowrap">
      <span className="text-muted-foreground">{label} </span>
      {children}
    </div>
  )
}

/**
 * The contributions drill-down (FE-15.2): one row per bucketed cash flow of a ladder cell
 * (GET …/results?currency=&bucket_code=), each with its lineage: cash-flow event → instrument
 * → import and source row (D-31 §6). Values are masked by the classification of what they read
 * (D-33); the bucket's own totals are the server's aggregates, shown beside them, never
 * re-added here.
 */
function Contributions({
  run,
  row,
  currency,
  bucket,
  minAggregateCount,
}: {
  run: RunDetail
  row: LadderRow | null
  currency: string
  bucket: string
  minAggregateCount: number
}) {
  const router = useRouter()
  const [offset, setOffset] = useState(0)
  // FE-33.1: the server's next_cursor, bound to this cell (NEXT_PUBLIC_LIST_PAGING_UI=off: offsets).
  const cursorPaging = useCursorPaging(`${run.run_id}|${currency}|${bucket}`)
  const cursor = LIST_PAGING_UI_ENABLED ? cursorPaging.cursor : null
  const [state, setState] = useState<Loaded<ResultPage>>({ kind: 'loading' })
  const loader = useMemo(
    () => createLoader(fetchContributions(run.run_id, currency, bucket, offset, cursor), setState, 'Could not load the contributions'),
    [run.run_id, currency, bucket, offset, cursor],
  )

  useEffect(() => {
    loader.load()
    return () => loader.abort()
  }, [loader])

  let body: ReactNode
  if (state.kind === 'loading') body = <LoadingBlock rows={3} />
  else if (state.kind === 'forbidden') body = <Forbidden permission="calc:read" correlationId={state.cid} />
  else if (state.kind === 'failed') body = <LoadFailed icon={ListTree} title="The contributions could not be loaded" cid={state.cid} onRetry={loader.load} />
  else if (state.kind === 'not_found')
    body = cursorRefusedBody(state, cursorPaging, 'contributions-pager') ?? (
      <EmptyState
        icon={ListTree}
        title="The contributions could not be read"
        description={`HTTP ${state.status ?? '—'}.${state.cid ? ` Correlation ID: ${state.cid}` : ''}`}
      />
    )
  else {
    const page = state.value
    body =
      page.items.length === 0 ? (
        <p className="text-sm text-muted-foreground" data-testid="contributions-empty">
          No contribution in this bucket.
        </p>
      ) : (
        <>
          <Table data-testid="contributions-table" data-total={page.total}>
            <TableHeader>
              <TableRow>
                <TableHead className="w-12">#</TableHead>
                <TableHead>Direction</TableHead>
                <TableHead>Origin</TableHead>
                <TableHead className="text-right">Value</TableHead>
                <TableHead>Due date</TableHead>
                <TableHead className="text-right">Days after</TableHead>
                <TableHead>Cash-flow event</TableHead>
                <TableHead>Instrument</TableHead>
                <TableHead>Import and source row</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {page.items.map((c) => {
                const l = c.lineage
                return (
                  <TableRow
                    key={c.intermediate_value_id}
                    data-testid="contribution"
                    data-id={c.intermediate_value_id}
                    data-direction={c.direction}
                    data-origin={c.origin}
                    data-masked={isMasked(c.value)}
                    data-classification={c.classification}
                  >
                    <TableCell className="font-mono text-xs">{c.sequence}</TableCell>
                    <TableCell>
                      <Badge variant={c.direction === 'INFLOW' ? 'success' : 'outline'} data-testid="contribution-direction">
                        {c.direction}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-xs">{c.origin}</TableCell>
                    <TableCell className="text-right text-sm">
                      <ShownValue value={c.value} testId="contribution-value" money />
                      {!isMasked(c.value) && <span className="text-xs text-muted-foreground"> {c.unit}</span>}
                    </TableCell>
                    <TableCell className="text-sm">
                      <ShownValue value={l.due_date} testId="contribution-due-date" />
                    </TableCell>
                    <TableCell className="text-right text-sm">
                      {isMasked(c.days_after_reporting_date) ? (
                        <MaskedValue value={c.days_after_reporting_date} />
                      ) : (
                        <span className="font-mono">{c.days_after_reporting_date}</span>
                      )}
                    </TableCell>
                    <TableCell className="text-xs">
                      <ShownValue value={l.cashflow_business_key} testId="contribution-cashflow-key" />
                      <div className="font-mono text-muted-foreground" title="Cash-flow event">
                        {l.cashflow_event_id}
                      </div>
                    </TableCell>
                    <TableCell className="text-xs">
                      {l.instrument_id ? (
                        <Link href={instrumentHref(l.instrument_id)} className="font-mono text-primary hover:underline" data-testid="contribution-instrument-link">
                          {isMasked(l.instrument_business_key) || l.instrument_business_key === null ? `${l.instrument_id.slice(0, 8)}…` : l.instrument_business_key}
                        </Link>
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                      {isMasked(l.instrument_business_key) && (
                        <span className="ml-1">
                          <MaskedValue value={l.instrument_business_key} />
                        </span>
                      )}
                      {l.instrument_version_id && (
                        <div className="font-mono text-muted-foreground" title="Instrument version">
                          {l.instrument_version_id}
                        </div>
                      )}
                    </TableCell>
                    <TableCell className="text-xs">
                      <Link href={importHref(l.import_id)} className="font-mono text-primary hover:underline" data-testid="contribution-import-link" data-import-id={l.import_id}>
                        Import {l.import_id.slice(0, 8)} · row {l.source_ordinal}
                      </Link>
                      <Lineage label="canonical">
                        <span className="font-mono">{l.canonical_record_id}</span>
                      </Lineage>
                      <Lineage label="source row">
                        <span className="font-mono">{l.source_record_id}</span>
                      </Lineage>
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
          {LIST_PAGING_UI_ENABLED && <ListPager page={page} paging={cursorPaging} testId="contributions-pager" />}
          {!LIST_PAGING_UI_ENABLED && page.total > RESULTS_PAGE && (
            <div className="mt-3 flex items-center justify-between text-xs text-muted-foreground">
              <span data-testid="contributions-range">
                {page.offset + 1}–{page.offset + page.items.length} of {page.total}
              </span>
              <div className="flex gap-2">
                <Button variant="outline" size="sm" disabled={offset === 0} onClick={() => setOffset(offset > RESULTS_PAGE ? offset - RESULTS_PAGE : 0)}>
                  <ChevronLeft className="h-3.5 w-3.5" />
                  Previous
                </Button>
                <Button variant="outline" size="sm" disabled={offset + RESULTS_PAGE >= page.total} onClick={() => setOffset(offset + RESULTS_PAGE)}>
                  Next
                  <ChevronRight className="h-3.5 w-3.5" />
                </Button>
              </div>
            </div>
          )}
        </>
      )
  }

  return (
    <PageSection
      icon={ListTree}
      title={`Contributions · ${currency} · ${row?.bucket_label ?? bucket}`}
      description="Each bucketed cash flow, with its lineage to the instrument, the import and the source row. A value is masked unless your clearance covers the most sensitive field it reads."
      headerRight={
        <Button variant="ghost" size="sm" onClick={() => router.replace(runHref(run.run_id), { scroll: false })} data-testid="contributions-close">
          <X className="mr-1 h-3.5 w-3.5" />
          Close
        </Button>
      }
    >
      <div className="space-y-3" data-testid="contributions" data-currency={currency} data-bucket={bucket}>
        <RunIdentity run={run} />
        <IllustrativeBanner statement={run.statement} compact />
        {row && (
          <dl className="grid grid-cols-2 gap-3 md:grid-cols-4" data-testid="contributions-bucket-totals">
            {(
              [
                ['Inflows (server aggregate)', 'inflows', 'bucket-inflows'],
                ['Outflows (server aggregate)', 'outflows', 'bucket-outflows'],
                ['Net gap', 'net_gap', 'bucket-net'],
              ] as const
            ).map(([label, figure, testId]) => (
              <div key={testId} className="rounded-md border border-border bg-surface-2 px-3 py-2">
                <dt className="text-xs text-muted-foreground">{label}</dt>
                <dd className="text-sm">
                  <LadderFigureValue row={row} figure={figure} minAggregateCount={minAggregateCount} testId={testId} />
                </dd>
              </div>
            ))}
            <div className="rounded-md border border-border bg-surface-2 px-3 py-2">
              <dt className="text-xs text-muted-foreground">Contributions</dt>
              <dd className="font-mono text-sm" data-testid="bucket-count">
                {row.contribution_count}
              </dd>
            </div>
          </dl>
        )}
        {body}
      </div>
    </PageSection>
  )
}

/**
 * FE-15.2: the maturity ladder per currency (inflows, outflows, net and cumulative gap, and
 * the contractual and behavioural parts), with each bucket's contributions one click away.
 * The ladder exists only for a run the server ended SUCCEEDED or SUCCEEDED_WITH_WARNINGS.
 */
export function MaturityLadderView({ run }: { run: RunDetail }) {
  const params = useSearchParams()
  const selected = useMemo(() => drillFrom(new URLSearchParams(params.toString())), [params])
  const [state, setState] = useState<Loaded<MaturityLadder | null>>({ kind: 'loading' })
  const loader = useMemo(() => createLoader(fetchLadder(run.run_id), setState, 'Could not load the maturity ladder'), [run.run_id])
  // FE-33.1: a background run the server has not ended has no ladder yet: nothing is read until
  // it ends (RunView keys this panel on the run's status, so it reads once the server ends it).
  const pending = followsRun(run)

  useEffect(() => {
    if (pending) return
    loader.load()
    return () => loader.abort()
  }, [loader, pending])

  let body: ReactNode
  let ladder: MaturityLadder | null = null
  if (pending) body = <ResultsPending status={run.status} testId="ladder-pending" />
  else if (state.kind === 'loading') body = <LoadingBlock rows={4} />
  else if (state.kind === 'forbidden') body = <Forbidden permission="calc:read" correlationId={state.cid} />
  else if (state.kind === 'failed') body = <LoadFailed icon={Layers} title="The maturity ladder could not be loaded" cid={state.cid} onRetry={loader.load} />
  else if (state.kind === 'not_found')
    body = (
      <EmptyState
        icon={Layers}
        title="The maturity ladder could not be read"
        description={`HTTP ${state.status ?? '—'}.${state.cid ? ` Correlation ID: ${state.cid}` : ''}`}
      />
    )
  else if (state.value === null)
    body = (
      <p className="text-sm text-muted-foreground" data-testid="ladder-not-applicable">
        This run&apos;s module has no maturity ladder.
      </p>
    )
  else {
    const value = state.value
    ladder = value
    body =
      !hasResults(value.status) || value.currencies.length === 0 ? (
        <p className="text-sm text-muted-foreground" data-testid="ladder-none" data-status={value.status}>
          {hasResults(value.status)
            ? 'The run bucketed no cash flow, so the ladder has no currency.'
            : 'No ladder: the server has not ended this run SUCCEEDED or SUCCEEDED_WITH_WARNINGS, so nothing here is a result.'}
        </p>
      ) : (
        <div className="space-y-6">
          {value.currencies.map((c) => (
            <LadderTable
              key={c.currency}
              run={run}
              currency={c.currency}
              rows={c.rows}
              buckets={value.buckets}
              selected={selected}
              minAggregateCount={value.min_aggregate_count}
            />
          ))}
          <p className="text-xs text-muted-foreground">
            Per currency, no FX. Net gap = inflows − outflows; the cumulative gap is the running sum of net gaps in bucket order. Aggregates are{' '}
            <span className="font-mono">internal</span>, except a figure built from fewer than{' '}
            <span className="font-mono" data-testid="ladder-min-aggregate-count">
              {value.min_aggregate_count}
            </span>{' '}
            contributions: it takes their classification, and so does every figure derived from it (the cumulative gap stays masked for the rest
            of the currency); zero figures stay clear. Bucket rule version <span className="font-mono">{value.bucket_rule_version_id}</span>.
          </p>
        </div>
      )
  }

  const row =
    selected && ladder ? ladder.currencies.find((c) => c.currency === selected.currency)?.rows.find((r) => r.bucket_code === selected.bucket) ?? null : null

  return (
    <>
      <PageSection
        icon={Layers}
        title="Maturity ladder"
        description="Cash flows bucketed by calendar days after the reporting date, by direction (from the instrument type) and origin, under the pinned illustrative rules."
      >
        <div className="space-y-4" data-testid="maturity-ladder" data-run-id={run.run_id}>
          <RunIdentity run={run} />
          <IllustrativeBanner statement={ladder?.statement ?? run.statement} compact />
          {/* FE-30.1: the ladder read's own context block (D-56 §5), checked against the run's pins. */}
          {ladder && <ResultContextPanel context={ladder.context} pins={run} source="maturity-ladder" compact />}
          {body}
        </div>
      </PageSection>
      {selected && ladder && hasResults(ladder.status) && (
        <Contributions
          key={`${selected.currency}-${selected.bucket}`}
          run={run}
          row={row}
          currency={selected.currency}
          bucket={selected.bucket}
          minAggregateCount={ladder.min_aggregate_count}
        />
      )}
    </>
  )
}
