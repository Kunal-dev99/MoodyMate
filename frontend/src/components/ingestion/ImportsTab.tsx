'use client'

import Link from 'next/link'
import { useCallback, useEffect, useRef, useState } from 'react'
import { ChevronLeft, ChevronRight, FileUp } from 'lucide-react'
import { api, call } from '@/api/client'
import { Forbidden } from '@/components/auth/Forbidden'
import { RequirePermission } from '@/components/auth/RequirePermission'
import { EmptyState } from '@/components/common/EmptyState'
import { LoadFailed } from '@/components/common/LoadFailed'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { PageSection } from '@/components/common/PageSection'
import { startLoad, type LoadFailure } from '@/components/common/load'
import { ImportStatusPill } from '@/components/ingestion/ImportStatusPill'
import { IMPORT_STATUSES, IMPORT_STATUS_STYLE, formatBytes, type ImportPage, type ImportStatus } from '@/components/ingestion/imports'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'

const PAGE_SIZE = 50
const selectClass = 'h-8 rounded-md border border-input bg-background px-2 text-sm'

type State = { kind: 'loading' } | { kind: 'ready'; page: ImportPage } | LoadFailure

/** The Phase 7 Imports tab, kept unchanged for NEXT_PUBLIC_IMPORTS_UI=off (D-02). */
export function ImportsPlanned() {
  return (
    <EmptyState
      icon={FileUp}
      title="No imports yet"
      description={
        <>
          File imports are <strong>planned</strong> for Feature 02 Phase 8 (WS2). Nothing can be uploaded yet.
        </>
      }
      action={<Badge variant="secondary">Planned</Badge>}
    />
  )
}

function ImportsList() {
  const [offset, setOffset] = useState(0)
  const [status, setStatus] = useState<ImportStatus | ''>('')
  const [state, setState] = useState<State>({ kind: 'loading' })
  const request = useRef<AbortController | null>(null)

  const load = useCallback(() => {
    // Another page or filter cancels the previous request; a late answer never lands.
    request.current = startLoad(
      (signal) =>
        call(() =>
          api.GET('/api/v1/imports', {
            params: { query: { limit: PAGE_SIZE, offset, ...(status ? { status } : {}) } },
            signal,
          }),
        ),
      { onReady: (page) => setState({ kind: 'ready', page }), onFailure: setState, failureTitle: 'Could not load imports' },
      request.current,
    )
  }, [offset, status])

  useEffect(() => {
    setState({ kind: 'loading' })
    load()
    return () => request.current?.abort()
  }, [load])

  const filter = (
    <label className="flex items-center gap-2 text-xs text-muted-foreground">
      Status
      <select
        aria-label="Filter by status"
        className={selectClass}
        value={status}
        onChange={(e) => {
          setOffset(0)
          setStatus(e.target.value as ImportStatus | '')
        }}
      >
        <option value="">All</option>
        {IMPORT_STATUSES.map((s) => (
          <option key={s} value={s}>
            {IMPORT_STATUS_STYLE[s].label}
          </option>
        ))}
      </select>
    </label>
  )

  if (state.kind === 'forbidden') return <Forbidden permission="import:read" correlationId={state.cid} />
  if (state.kind === 'loading') return <LoadingBlock rows={4} />
  if (state.kind === 'failed')
    return (
      <LoadFailed
        icon={FileUp}
        title="Imports could not be loaded"
        cid={state.cid}
        onRetry={() => {
          setState({ kind: 'loading' })
          load()
        }}
      />
    )
  if (state.kind === 'not_found')
    return (
      <EmptyState
        icon={FileUp}
        title="Imports could not be listed"
        description={`The request was refused.${state.cid ? ` Correlation ID: ${state.cid}` : ''}`}
      />
    )

  const { page } = state
  if (page.total === 0 && !status)
    return (
      <EmptyState
        icon={FileUp}
        title="No imports yet"
        description="A data steward uploads a file for a registered source; each upload becomes an import with its raw file kept unchanged."
      />
    )

  return (
    <PageSection
      icon={FileUp}
      title={`${page.total} import${page.total === 1 ? '' : 's'}`}
      description="Newest first. Each import pins the source contract and schema version in force at upload."
      headerRight={filter}
    >
      {page.items.length === 0 ? (
        <p className="text-sm text-muted-foreground">No imports with this status.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Import</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Source</TableHead>
              <TableHead>File</TableHead>
              <TableHead className="text-right">Rows</TableHead>
              <TableHead>Schema</TableHead>
              <TableHead>Uploaded</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {page.items.map((i) => (
              <TableRow key={i.id} data-testid="import-row" data-id={i.id} data-status={i.status}>
                <TableCell>
                  <Link href={`/ingestion/imports/${i.id}`} className="font-mono text-sm text-primary hover:underline">
                    {i.id.slice(0, 8)}…
                  </Link>
                  <div className="font-mono text-xs text-muted-foreground" title={i.artifact.sha256}>
                    sha256 {i.artifact.sha256.slice(0, 12)}…
                  </div>
                </TableCell>
                <TableCell>
                  <ImportStatusPill status={i.status} />
                </TableCell>
                <TableCell className="font-mono text-sm">{i.source_code}</TableCell>
                <TableCell className="text-sm">
                  {i.artifact.original_filename ?? '—'}
                  <div className="text-xs text-muted-foreground">
                    {i.format} · {formatBytes(i.artifact.size_bytes)}
                  </div>
                </TableCell>
                <TableCell className="text-right font-mono text-sm">{i.row_count}</TableCell>
                <TableCell className="font-mono text-sm">
                  {i.schema_code ? `${i.schema_code} v${i.schema_version}` : '—'}
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  <span className="font-mono">{i.created_by}</span>
                  <div>{new Date(i.created_at).toLocaleString()}</div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      {page.total > PAGE_SIZE && (
        <div className="mt-3 flex items-center justify-between text-xs text-muted-foreground">
          <span>
            {page.offset + 1}–{page.offset + page.items.length} of {page.total}
          </span>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
              <ChevronLeft className="h-3.5 w-3.5" />
              Previous
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={offset + PAGE_SIZE >= page.total}
              onClick={() => setOffset(offset + PAGE_SIZE)}
            >
              Next
              <ChevronRight className="h-3.5 w-3.5" />
            </Button>
          </div>
        </div>
      )}
    </PageSection>
  )
}

/** The Imports tab of the Ingestion Workbench (FE-8.2). Cosmetic gate; the API enforces import:read (D-04). */
export function ImportsTab() {
  return (
    <RequirePermission permission="import:read">
      <ImportsList />
    </RequirePermission>
  )
}
