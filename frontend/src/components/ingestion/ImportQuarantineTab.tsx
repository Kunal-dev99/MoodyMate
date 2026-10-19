'use client'

import Link from 'next/link'
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { FileUp, ShieldAlert } from 'lucide-react'
import { api, call } from '@/api/client'
import { Forbidden } from '@/components/auth/Forbidden'
import { RequirePermission } from '@/components/auth/RequirePermission'
import { EmptyState } from '@/components/common/EmptyState'
import { LoadFailed } from '@/components/common/LoadFailed'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { MaskedValue, isMasked } from '@/components/common/MaskedValue'
import { PageSection } from '@/components/common/PageSection'
import { toastApiError } from '@/components/common/api-error-toast'
import { startLoad, type LoadFailure } from '@/components/common/load'
import { QuarantineStatusPill } from '@/components/ingestion/QuarantineStatusPill'
import { RefusalNotice } from '@/components/ingestion/RefusalNotice'
import type { ImportDetail } from '@/components/ingestion/imports'
import type { Refusal } from '@/components/ingestion/mappings'
import {
  MOVE_LABEL,
  QUARANTINE_STATUSES,
  canRetry,
  legalMoves,
  retryHref,
  validationRefusal,
  type QuarantineBatch,
  type QuarantineItem,
  type QuarantineStatus,
} from '@/components/ingestion/validation'
import { Button } from '@/components/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'

/*
 * FE-11.2: an import's held rows (D-20), per batch. Values come masked by the server's
 * classification policy; a disposition only moves an item along the D-20 table (the server
 * decides) and nothing is ever edited or deleted. Corrections are a new import.
 */

const PAGE = 50
const selectClass = 'h-9 rounded-md border border-input bg-background px-2 text-sm'

type State = { kind: 'loading' } | { kind: 'ready'; batch: QuarantineBatch } | LoadFailure

function Cell({ value }: { value: QuarantineItem['values'][string] | undefined }) {
  if (value === undefined) return <span className="text-muted-foreground">—</span>
  if (isMasked(value)) return <MaskedValue value={value} />
  if (value === '') return <span className="text-muted-foreground">(empty)</span>
  return <span className="font-mono text-xs">{value}</span>
}

function Actions({ item, batchId, onMoved }: { item: QuarantineItem; batchId: string; onMoved: (r: Refusal | null) => void }) {
  const [busy, setBusy] = useState(false)
  const moves = legalMoves(item)
  if (moves.length === 0) return <span className="text-xs text-muted-foreground">—</span>
  const move = async (to: QuarantineStatus) => {
    setBusy(true)
    try {
      await call(() =>
        api.POST('/api/v1/quarantine/{batch_id}/items/{quarantine_id}/disposition', {
          params: { path: { batch_id: batchId, quarantine_id: item.id } },
          body: { status: to },
        }),
      )
      onMoved(null)
    } catch (err) {
      const r = validationRefusal(err, 'Could not move the item')
      toastApiError(err, r.title)
      onMoved(r)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="flex flex-wrap gap-1">
      {moves.map((to) => (
        <Button key={to} size="sm" variant={to === 'REJECTED' ? 'outline' : 'secondary'} disabled={busy} onClick={() => move(to)} data-testid="quarantine-move" data-to={to}>
          {MOVE_LABEL[to]}
        </Button>
      ))}
    </div>
  )
}

function BatchItems({ batchId, batchNumber }: { batchId: string; batchNumber: number }) {
  const [status, setStatus] = useState<QuarantineStatus | ''>('')
  const [offset, setOffset] = useState(0)
  const [state, setState] = useState<State>({ kind: 'loading' })
  const [refusal, setRefusal] = useState<Refusal | null>(null)
  const request = useRef<AbortController | null>(null)

  const load = useCallback(() => {
    const query = { limit: PAGE, offset, ...(status ? { status } : {}) }
    request.current = startLoad(
      (signal) => call(() => api.GET('/api/v1/quarantine/{batch_id}', { params: { path: { batch_id: batchId }, query }, signal })),
      { onReady: (batch) => setState({ kind: 'ready', batch }), onFailure: setState, failureTitle: 'Could not load the quarantine' },
      request.current,
    )
  }, [batchId, status, offset])

  useEffect(() => {
    setState({ kind: 'loading' })
    load()
    return () => request.current?.abort()
  }, [load])

  const title = `Batch ${batchNumber}`
  if (state.kind === 'loading') return <LoadingBlock rows={4} />
  if (state.kind === 'forbidden') return <Forbidden permission="quarantine:read" correlationId={state.cid} />
  if (state.kind === 'failed')
    return (
      <LoadFailed
        icon={ShieldAlert}
        title="The quarantine could not be loaded"
        cid={state.cid}
        onRetry={() => {
          setState({ kind: 'loading' })
          load()
        }}
      />
    )
  if (state.kind === 'not_found')
    return <EmptyState icon={ShieldAlert} title="Batch not found" description={`It does not exist or is not visible to you.${state.cid ? ` Correlation ID: ${state.cid}` : ''}`} />

  const { batch } = state
  const last = Math.min(batch.offset + batch.items.length, batch.total)
  const counts = QUARANTINE_STATUSES.filter((s) => batch.status_counts[s])

  let table: ReactNode
  if (batch.items.length === 0)
    table = <EmptyState icon={ShieldAlert} title="No held rows" description={status ? 'No item has this status.' : 'Every row of this batch was accepted.'} />
  else
    table = (
      <>
        <div className="overflow-x-auto">
          <Table data-testid="quarantine-table">
            <TableHeader>
              <TableRow>
                <TableHead>Row</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Actions</TableHead>
                <TableHead>Reasons</TableHead>
                {batch.columns.map((c) => (
                  <TableHead key={c} className="font-mono text-xs">
                    {c}
                  </TableHead>
                ))}
                <TableHead>Owner</TableHead>
                <TableHead>Resolved by</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {batch.items.map((item) => (
                <TableRow key={item.id} data-testid="quarantine-item" data-id={item.id} data-row={item.row} data-status={item.status}>
                  <TableCell className="font-mono text-sm">{item.row}</TableCell>
                  <TableCell>
                    <QuarantineStatusPill status={item.status} />
                  </TableCell>
                  <TableCell>
                    <RequirePermission permission="quality:review" fallback={<span className="text-xs text-muted-foreground">—</span>}>
                      <Actions
                        item={item}
                        batchId={batch.batch_id}
                        onMoved={(r) => {
                          setRefusal(r)
                          load()
                        }}
                      />
                    </RequirePermission>
                  </TableCell>
                  <TableCell className="font-mono text-xs" data-testid="quarantine-reasons">
                    {item.reason_codes.join(', ')}
                  </TableCell>
                  {batch.columns.map((c) => (
                    <TableCell key={c} className="whitespace-nowrap">
                      <Cell value={item.values[c]} />
                    </TableCell>
                  ))}
                  <TableCell className="font-mono text-xs">{item.owner}</TableCell>
                  <TableCell className="font-mono text-xs" data-testid="quarantine-resolved-by">
                    {item.resolved_by_import_id ? (
                      <Link href={`/ingestion/imports/${item.resolved_by_import_id}`} className="text-primary hover:underline">
                        {item.resolved_by_import_id.slice(0, 8)}
                      </Link>
                    ) : (
                      '—'
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
        <div className="mt-3 flex items-center justify-between text-sm">
          <span className="text-muted-foreground">
            {batch.offset + 1}–{last} of {batch.total}
          </span>
          <div className="flex gap-2">
            <Button size="sm" variant="outline" disabled={batch.offset === 0} onClick={() => setOffset(Math.max(0, batch.offset - PAGE))}>
              Previous
            </Button>
            <Button size="sm" variant="outline" disabled={last >= batch.total} onClick={() => setOffset(batch.offset + PAGE)}>
              Next
            </Button>
          </div>
        </div>
      </>
    )

  return (
    <PageSection icon={ShieldAlert} title={title} description="Rows as read, never edited; values masked by each column's classification.">
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <select
          aria-label="Quarantine status"
          className={selectClass}
          value={status}
          onChange={(e) => {
            setOffset(0)
            setStatus(e.target.value as QuarantineStatus | '')
          }}
          data-testid="quarantine-status-filter"
        >
          <option value="">All statuses</option>
          {QUARANTINE_STATUSES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <div className="flex flex-wrap gap-2 text-xs" data-testid="quarantine-status-counts">
          {counts.map((s) => (
            <span key={s} className="flex items-center gap-1" data-status={s} data-count={batch.status_counts[s]}>
              <QuarantineStatusPill status={s} /> <span className="font-mono">{batch.status_counts[s]}</span>
            </span>
          ))}
        </div>
      </div>
      {refusal && (
        <div className="mb-3">
          <RefusalNotice refusal={refusal} />
        </div>
      )}
      {table}
    </PageSection>
  )
}

/** The Quarantine tab (shown only with quarantine:read; the server enforces it too). */
export function ImportQuarantineTab({ job }: { job: ImportDetail }) {
  return (
    <div className="space-y-4" data-testid="quarantine-tab">
      <PageSection
        icon={FileUp}
        title="Quarantine"
        description="Review held rows: start a review, then mark an item retry ready or reject it. Corrected and Resolved come only from a retry import."
        headerRight={
          canRetry(job.status) ? (
            <RequirePermission permission="import:write" fallback={null}>
              <Button asChild size="sm" data-testid="quarantine-retry">
                <Link href={retryHref(job)}>
                  <FileUp className="mr-2 h-4 w-4" />
                  Retry with corrected file
                </Link>
              </Button>
            </RequirePermission>
          ) : undefined
        }
      >
        <p className="text-sm text-muted-foreground">
          A retry is a new import of the same source with <code className="font-mono">retry_of_import_id</code> = this import. When it is
          validated, items marked Retry ready whose business key it accepts move to Corrected, then Resolved.
        </p>
      </PageSection>
      {!job.manifest ? (
        <EmptyState icon={ShieldAlert} title="Not validated yet" description="Rows are held only once the import is validated." />
      ) : (
        job.batches.map((b) => <BatchItems key={b.id} batchId={b.id} batchNumber={b.batch_number} />)
      )}
    </div>
  )
}
