'use client'

import Link from 'next/link'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { ChevronLeft, ChevronRight, ListTree, X } from 'lucide-react'
import { Forbidden } from '@/components/auth/Forbidden'
import { EmptyState } from '@/components/common/EmptyState'
import { LoadFailed } from '@/components/common/LoadFailed'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { MaskedValue, isMasked } from '@/components/common/MaskedValue'
import { PageSection } from '@/components/common/PageSection'
import { createLoader, type Loaded } from '@/components/common/load'
import { importHref } from '@/components/domain/domain'
import { ShownValue } from '@/components/domain/ShownValue'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import {
  CHANGE_TEXT,
  CHANGE_VARIANT,
  PARTS_PAGE,
  changeCounts,
  columnLabel,
  fetchParts,
  partMove,
  type DrillTarget,
  type PartChange,
  type ScenarioContribution,
  type ScenarioContributionPage,
} from './comparison'

function Lineage({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="whitespace-nowrap">
      <span className="text-muted-foreground">{label} </span>
      {children}
    </div>
  )
}

/** One changed part: what the shock did, where it was due before and after, the amount (masked per D-33) and its source row. */
export function PartRow({ part }: { part: ScenarioContribution }) {
  const l = part.lineage
  return (
    <TableRow
      data-testid="comparison-part"
      data-id={part.scenario_contribution_id}
      data-part-id={part.part_id}
      data-change={part.change}
      data-direction={part.direction}
      data-masked={isMasked(part.amount)}
    >
      <TableCell>
        <Badge variant={CHANGE_VARIANT[part.change]} data-testid="part-change">
          {CHANGE_TEXT[part.change]}
        </Badge>
        <div className="mt-1 font-mono text-[11px] text-muted-foreground" title="Part path">
          {part.part_path}
        </div>
      </TableCell>
      <TableCell className="text-xs">
        {part.direction}
        <div className="text-muted-foreground">{part.origin}</div>
      </TableCell>
      <TableCell className="text-xs" data-testid="part-move">
        {partMove(part)}
      </TableCell>
      <TableCell className="text-xs">
        {part.shock_sequences.map((n, i) => (
          <div key={`${n}-${i}`} className="font-mono">
            #{n} {part.templates[i]}
          </div>
        ))}
      </TableCell>
      <TableCell className="text-right text-sm">
        <ShownValue value={part.amount} money testId="part-amount" />
        {!isMasked(part.amount) && <span className="text-xs text-muted-foreground"> {part.currency}</span>}
      </TableCell>
      <TableCell className="text-xs">
        {isMasked(part.instrument_business_key) ? (
          <MaskedValue value={part.instrument_business_key} />
        ) : (
          <span className="font-mono">{part.instrument_business_key ?? '—'}</span>
        )}
        <div className="font-mono text-muted-foreground" title="Cash-flow event">
          {part.cashflow_event_id}
        </div>
      </TableCell>
      <TableCell className="text-xs">
        <Link href={importHref(l.import_id)} className="font-mono text-primary hover:underline" data-testid="part-source-link" data-import-id={l.import_id} data-source-ordinal={l.source_ordinal}>
          Import {l.import_id.slice(0, 8)} · row {l.source_ordinal}
        </Link>
        <Lineage label="canonical">
          <span className="font-mono">{l.canonical_record_id}</span>
        </Lineage>
        <Lineage label="source row">
          <span className="font-mono" data-testid="part-source-record">
            {l.source_record_id}
          </span>
        </Lineage>
      </TableCell>
    </TableRow>
  )
}

type ChangeFilter = 'ALL' | PartChange
const FILTERS: readonly ChangeFilter[] = ['ALL', 'MOVED', 'STAYED', 'ADDED']

/**
 * FE-25.2 drill-down (D-46 §4): one member run's changed parts in one currency and bucket (moved,
 * stayed and added), each traced to its cash-flow event, canonical record, import and source
 * row (D-31 §6). Amounts are confidential and arrive masked for a role not cleared for them;
 * days, buckets and templates are internal and clear. The change filter narrows the page shown.
 */
export function ComparisonDrilldown({ scenarioId, scenarioRunId, target, onClose }: { scenarioId: string; scenarioRunId: string; target: DrillTarget; onClose: () => void }) {
  const [offset, setOffset] = useState(0)
  const [filter, setFilter] = useState<ChangeFilter>('ALL')
  const [state, setState] = useState<Loaded<ScenarioContributionPage>>({ kind: 'loading' })
  const loader = useMemo(() => createLoader(fetchParts(scenarioId, scenarioRunId, target, offset), setState, 'Could not load the changed parts'), [scenarioId, scenarioRunId, target, offset])
  useEffect(() => {
    loader.load()
    return loader.abort
  }, [loader])

  const title = `Changed parts · ${target.currency} · ${target.bucketLabel} · ${columnLabel({ combined: target.combined, shock_subset: target.shockSubset })}`
  const close = (
    <Button size="sm" variant="ghost" onClick={onClose} data-testid="comparison-drill-close" aria-label="Close the drill-down">
      <X className="h-4 w-4" />
    </Button>
  )
  let body: ReactNode
  if (state.kind === 'loading') body = <LoadingBlock rows={3} />
  else if (state.kind === 'failed') body = <LoadFailed icon={ListTree} title="Could not load the changed parts" cid={state.cid} onRetry={loader.load} />
  else if (state.kind === 'forbidden') body = <Forbidden permission="calc:read" correlationId={state.cid} />
  else if (state.kind === 'not_found') body = <EmptyState icon={ListTree} title="Member run not found" description={`The server has no such member run for this scenario run.${state.cid ? ` Correlation ID: ${state.cid}` : ''}`} />
  else {
    const page = state.value
    const counts = changeCounts(page.items)
    const shown = filter === 'ALL' ? page.items : page.items.filter((p) => p.change === filter)
    body =
      page.items.length === 0 ? (
        <EmptyState icon={ListTree} title="No changed part in this bucket" description="The shocks moved or added nothing here for this run." />
      ) : (
        <>
          <div className="mb-2 flex flex-wrap items-center gap-2" role="group" aria-label="Change">
            {FILTERS.map((f) => (
              <Button key={f} size="sm" variant={filter === f ? 'default' : 'outline'} className="h-7" onClick={() => setFilter(f)} aria-pressed={filter === f} data-testid="part-filter" data-change={f}>
                {f === 'ALL' ? `All (${page.items.length})` : `${CHANGE_TEXT[f]} (${counts[f]})`}
              </Button>
            ))}
            <span className="text-xs text-muted-foreground">Filters this page of {page.total}.</span>
          </div>
          <Table data-testid="comparison-parts" data-total={page.total}>
            <TableHeader>
              <TableRow>
                <TableHead>Change</TableHead>
                <TableHead>Direction</TableHead>
                <TableHead>Due</TableHead>
                <TableHead>Shocks</TableHead>
                <TableHead className="text-right">Amount</TableHead>
                <TableHead>Instrument · cash flow</TableHead>
                <TableHead>Source row</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {shown.map((p) => (
                <PartRow key={p.scenario_contribution_id} part={p} />
              ))}
            </TableBody>
          </Table>
          {page.total > PARTS_PAGE && (
            <div className="mt-3 flex items-center justify-between text-xs text-muted-foreground">
              <span data-testid="parts-range">
                {page.offset + 1}–{page.offset + page.items.length} of {page.total}
              </span>
              <div className="flex gap-2">
                <Button variant="outline" size="sm" disabled={offset === 0} onClick={() => setOffset(offset > PARTS_PAGE ? offset - PARTS_PAGE : 0)}>
                  <ChevronLeft className="h-3.5 w-3.5" />
                  Previous
                </Button>
                <Button variant="outline" size="sm" disabled={offset + PARTS_PAGE >= page.total} onClick={() => setOffset(offset + PARTS_PAGE)}>
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
    <div data-testid="comparison-drilldown" data-currency={target.currency} data-bucket={target.bucketCode}>
      <PageSection icon={ListTree} title={title} description="Each part of a changed cash flow, with where it was due before and after, and its source row." actions={close}>
        {body}
      </PageSection>
    </div>
  )
}
