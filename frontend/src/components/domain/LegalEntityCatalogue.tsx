'use client'

import Link from 'next/link'
import { useEffect, useMemo, useState } from 'react'
import { Building, ChevronLeft, ChevronRight } from 'lucide-react'
import { api, call } from '@/api/client'
import { Forbidden } from '@/components/auth/Forbidden'
import { EmptyState } from '@/components/common/EmptyState'
import { LoadFailed } from '@/components/common/LoadFailed'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { PageSection } from '@/components/common/PageSection'
import { createLoader, type Loaded } from '@/components/common/load'
import { Button } from '@/components/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { formatTimestamp } from '@/lib/format'
import { ShownValue } from './ShownValue'
import { ProvisionalIdentityBadge } from './LegalEntityView'
import { legalEntityHref, type LegalEntityPage } from './identity'

const PAGE_SIZE = 50

const fetchPage = (offset: number) => (signal: AbortSignal) =>
  call(() => api.GET('/api/v1/legal-entities', { params: { query: { limit: PAGE_SIZE, offset } }, signal }))

/**
 * FE-31.1 (D-58 §2): the tenant's legal entity identities (GET /legal-entities), by code then
 * source, as the server orders them. Codes are masked by the classification of the position
 * field they came from. Read-only: identities are append-only and the UI writes none.
 */
export function LegalEntityCatalogue() {
  const [offset, setOffset] = useState(0)
  const [state, setState] = useState<Loaded<LegalEntityPage>>({ kind: 'loading' })
  const loader = useMemo(() => createLoader(fetchPage(offset), setState, 'Could not load the legal entities'), [offset])

  useEffect(() => {
    loader.load()
    return () => loader.abort()
  }, [loader])

  if (state.kind === 'loading') return <LoadingBlock rows={4} />
  if (state.kind === 'forbidden') return <Forbidden permission="domain:read" correlationId={state.cid} />
  if (state.kind === 'failed') return <LoadFailed icon={Building} title="The legal entities could not be loaded" cid={state.cid} onRetry={loader.load} />
  if (state.kind === 'not_found')
    return (
      <EmptyState
        icon={Building}
        title="The legal entities could not be listed"
        description={`The request was refused.${state.cid ? ` Correlation ID: ${state.cid}` : ''}`}
      />
    )

  const page = state.value
  return (
    <PageSection
      icon={Building}
      title={`Legal entities (${page.total})`}
      description="One identity per source system and code, created when a position names it. Position versions link to it by exact key; several identities with one key give no link (LINK_AMBIGUOUS)."
      headerRight={<ProvisionalIdentityBadge />}
    >
      {page.items.length === 0 ? (
        <EmptyState
          icon={Building}
          title="No legal entities yet"
          description="They appear when a position import naming a legal entity is projected, or when identity links are reconciled."
        />
      ) : (
        <>
          <Table data-testid="legal-entity-table" data-total={page.total}>
            <TableHeader>
              <TableRow>
                <TableHead>Code</TableHead>
                <TableHead>Source</TableHead>
                <TableHead>Legal entity ID</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>First seen</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {page.items.map((e) => (
                <TableRow key={e.legal_entity_id} data-testid="legal-entity-row" data-id={e.legal_entity_id}>
                  <TableCell className="text-sm">
                    <ShownValue value={e.code} testId="legal-entity-code" />
                  </TableCell>
                  <TableCell>
                    <Link href={`/ingestion/sources/${e.source_id}`} className="font-mono text-xs text-primary hover:underline">
                      {e.source_code}
                    </Link>
                  </TableCell>
                  <TableCell>
                    <Link href={legalEntityHref(e.legal_entity_id)} className="font-mono text-xs text-primary hover:underline" data-testid="legal-entity-link">
                      {e.legal_entity_id}
                    </Link>
                  </TableCell>
                  <TableCell>{e.provisional && <ProvisionalIdentityBadge />}</TableCell>
                  <TableCell className="font-mono text-xs">{formatTimestamp(e.created_at)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {page.total > PAGE_SIZE && (
            <div className="mt-3 flex items-center justify-between text-xs text-muted-foreground">
              <span data-testid="legal-entity-range">
                {offset + 1}–{offset + page.items.length} of {page.total}
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
        </>
      )}
    </PageSection>
  )
}
