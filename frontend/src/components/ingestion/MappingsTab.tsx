'use client'

import Link from 'next/link'
import { useCallback, useEffect, useRef, useState } from 'react'
import { ChevronLeft, ChevronRight, GitMerge, Plus } from 'lucide-react'
import { api, call } from '@/api/client'
import { Forbidden } from '@/components/auth/Forbidden'
import { RequirePermission } from '@/components/auth/RequirePermission'
import { EmptyState } from '@/components/common/EmptyState'
import { LoadFailed } from '@/components/common/LoadFailed'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { PageSection } from '@/components/common/PageSection'
import { startLoad, type LoadFailure } from '@/components/common/load'
import { ListPager, cursorRefusedBody, useCursorPaging } from '@/components/common/ListPager'
import { LIST_PAGING_UI_ENABLED } from '@/config/features'
import { cursorQuery } from '@/lib/paging'
import type { Schemas } from '@/api/client'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { formatTimestamp } from '@/lib/format'
import { ActorLabel } from '@/components/common/ActorLabel'

const PAGE_SIZE = 50

type State = { kind: 'loading' } | { kind: 'ready'; page: Schemas['MappingPage'] } | LoadFailure

/**
 * Mappings (FE-10.1), all or one source's: GET /mappings, needs mapping:read. Each links
 * to its workbench; "New mapping" (mapping:propose) opens the create page.
 */
export function MappingsTab({ sourceId }: { sourceId?: string }) {
  const [offset, setOffset] = useState(0)
  const [state, setState] = useState<State>({ kind: 'loading' })
  const request = useRef<AbortController | null>(null)
  // FE-33.1: the server's next_cursor, bound to the source filter (NEXT_PUBLIC_LIST_PAGING_UI=off: offsets).
  const cursorPaging = useCursorPaging(sourceId ?? '')
  const cursor = LIST_PAGING_UI_ENABLED ? cursorPaging.cursor : null

  const load = useCallback(() => {
    request.current = startLoad(
      (signal) =>
        call(() =>
          api.GET('/api/v1/mappings', {
            params: { query: { limit: PAGE_SIZE, ...(LIST_PAGING_UI_ENABLED ? cursorQuery(cursor) : { offset }), ...(sourceId ? { source_id: sourceId } : {}) } },
            signal,
          }),
        ),
      { onReady: (page) => setState({ kind: 'ready', page }), onFailure: setState, failureTitle: 'Could not load mappings' },
      request.current,
    )
  }, [offset, sourceId, cursor])

  useEffect(() => {
    setState({ kind: 'loading' })
    load()
    return () => request.current?.abort()
  }, [load])

  const newHref = `/ingestion/mappings/new${sourceId ? `?source=${sourceId}` : ''}`
  const action = (
    <RequirePermission permission="mapping:propose" fallback={null}>
      <Button asChild size="sm" data-testid="new-mapping">
        <Link href={newHref}>
          <Plus className="mr-1 h-3.5 w-3.5" />
          New mapping
        </Link>
      </Button>
    </RequirePermission>
  )

  if (state.kind === 'loading') return <LoadingBlock rows={3} />
  if (state.kind === 'forbidden') return <Forbidden permission="mapping:read" correlationId={state.cid} />
  if (state.kind !== 'ready')
    return cursorRefusedBody(state, cursorPaging, 'mapping-pager') ?? (
      <LoadFailed
        icon={GitMerge}
        title="Mappings could not be loaded"
        cid={state.cid}
        onRetry={() => {
          setState({ kind: 'loading' })
          load()
        }}
      />
    )

  const { page } = state
  return (
    <PageSection
      icon={GitMerge}
      title={`${page.total} mapping${page.total === 1 ? '' : 's'}`}
      description="Each maps one source contract to its target schema. Versions are never edited; a change is a new version."
      actions={action}
    >
      {page.total === 0 ? (
        <p className="text-sm text-muted-foreground" data-testid="mappings-empty">
          No mapping yet. Imports of an unmapped source stop at “Mapping required” until a mapping version is approved.
        </p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Mapping</TableHead>
              <TableHead>Source</TableHead>
              <TableHead>Target schema</TableHead>
              <TableHead>Active version</TableHead>
              <TableHead className="text-right">Versions</TableHead>
              <TableHead>Created</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {page.items.map((m) => (
              <TableRow key={m.id} data-testid="mapping-row" data-code={m.code}>
                <TableCell>
                  <Link href={`/ingestion/mappings/${m.id}`} className="font-medium text-primary hover:underline">
                    {m.name}
                  </Link>
                  <div className="font-mono text-xs text-muted-foreground">{m.code}</div>
                </TableCell>
                <TableCell className="font-mono text-sm">
                  {m.source_code} {m.contract_version != null && <span className="text-muted-foreground">contract v{m.contract_version}</span>}
                </TableCell>
                <TableCell className="font-mono text-sm">{m.schema_code}</TableCell>
                <TableCell>
                  {m.active_version != null ? (
                    <Badge variant="success">v{m.active_version} active</Badge>
                  ) : (
                    <Badge variant="secondary">No active version</Badge>
                  )}
                </TableCell>
                <TableCell className="text-right font-mono text-sm">{m.version_count}</TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  <ActorLabel actor={m.created_by} />
                  <div>{formatTimestamp(m.created_at)}</div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      {LIST_PAGING_UI_ENABLED && page.items.length > 0 && <ListPager page={page} paging={cursorPaging} testId="mapping-pager" />}
      {!LIST_PAGING_UI_ENABLED && page.total > PAGE_SIZE && (
        <div className="mt-3 flex items-center justify-between text-xs text-muted-foreground">
          <span>
            {page.offset + 1}–{page.offset + page.items.length} of {page.total}
          </span>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
              <ChevronLeft className="h-3.5 w-3.5" />
              Previous
            </Button>
            <Button variant="outline" size="sm" disabled={offset + PAGE_SIZE >= page.total} onClick={() => setOffset(offset + PAGE_SIZE)}>
              Next
              <ChevronRight className="h-3.5 w-3.5" />
            </Button>
          </div>
        </div>
      )}
    </PageSection>
  )
}
