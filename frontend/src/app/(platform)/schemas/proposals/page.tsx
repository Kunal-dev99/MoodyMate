'use client'

import Link from 'next/link'
import { useEffect, useState } from 'react'
import { GitPullRequest } from 'lucide-react'
import { api, call, ApiError, type Schemas } from '@/api/client'
import { Forbidden } from '@/components/auth/Forbidden'
import { useCurrentSession } from '@/components/auth/SessionProvider'
import { EmptyState } from '@/components/common/EmptyState'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { PageHeader } from '@/components/common/PageHeader'
import { PageSection } from '@/components/common/PageSection'
import { toastApiError } from '@/components/common/api-error-toast'
import { CursorRefused, ListPager, useCursorPaging } from '@/components/common/ListPager'
import { LIST_PAGING_UI_ENABLED } from '@/config/features'
import { cursorQuery, isInvalidCursor } from '@/lib/paging'
import { CompatibilityBadge } from '@/components/schemas/CompatibilityBadge'
import { ProposalStatusPill } from '@/components/schemas/ProposalStatusPill'
import { PROPOSAL_STATUS_STYLE, type Proposal, type ProposalStatus } from '@/components/schemas/proposals'
import { Button } from '@/components/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { formatTimestamp } from '@/lib/format'

type State =
  | { kind: 'loading' }
  | { kind: 'ready'; items: Proposal[]; list: Schemas['ProposalList'] }
  | { kind: 'forbidden'; cid: string | null }
  /** Q19: the server refused the page's cursor (400 invalid_cursor). */
  | { kind: 'cursor_refused'; cid: string | null }

/** Schema change proposals (FE-5.1): the approver's queue and every proposer's history. */
export default function ProposalsPage() {
  const { has } = useCurrentSession()
  const [status, setStatus] = useState<ProposalStatus | ''>(has('schema:approve') ? 'SUBMITTED' : '')
  const [state, setState] = useState<State>({ kind: 'loading' })
  // FE-33.1: the server's next_cursor, bound to the status filter (NEXT_PUBLIC_LIST_PAGING_UI=off: the first 200 only).
  const cursorPaging = useCursorPaging(status)
  const cursor = LIST_PAGING_UI_ENABLED ? cursorPaging.cursor : null

  useEffect(() => {
    let current = true
    setState({ kind: 'loading' })
    call(() => api.GET('/api/v1/schema-change-proposals', { params: { query: { status: status || undefined, limit: 200, ...cursorQuery(cursor) } } }))
      .then((list) => current && setState({ kind: 'ready', items: list.items, list }))
      .catch((err: unknown) => {
        if (!current) return
        if (err instanceof ApiError && err.status === 403) setState({ kind: 'forbidden', cid: err.correlationId })
        else if (LIST_PAGING_UI_ENABLED && isInvalidCursor(err)) setState({ kind: 'cursor_refused', cid: err instanceof ApiError ? err.correlationId : null })
        else toastApiError(err, 'Could not load proposals')
      })
    return () => {
      current = false
    }
  }, [status, cursor])

  return (
    <>
      <PageHeader
        title="Schema change proposals"
        description="Every change to a canonical schema is proposed, impact-analysed, and approved by someone other than the proposer."
        breadcrumbs={[{ label: 'Source & Schemas', href: '/schemas' }, { label: 'Proposals' }]}
        actions={
          has('schema:propose') ? (
            <Button asChild size="sm">
              <Link href="/schemas/proposals/new">New proposal</Link>
            </Button>
          ) : undefined
        }
      />
      <div className="px-6 pb-6">
        {state.kind === 'forbidden' ? (
          <Forbidden permission="schema:read" correlationId={state.cid} />
        ) : (
          <PageSection
            icon={GitPullRequest}
            title="Proposals"
            headerRight={
              <select
                className="h-9 rounded-md border border-input bg-background px-2 text-sm"
                aria-label="Filter by status"
                value={status}
                onChange={(e) => setStatus(e.target.value as ProposalStatus | '')}
              >
                <option value="">All statuses</option>
                {Object.entries(PROPOSAL_STATUS_STYLE).map(([value, s]) => (
                  <option key={value} value={value}>
                    {s.label}
                  </option>
                ))}
              </select>
            }
          >
            {state.kind === 'loading' ? (
              <LoadingBlock rows={4} />
            ) : state.kind === 'cursor_refused' ? (
              <CursorRefused paging={cursorPaging} cid={state.cid} testId="proposal-pager" />
            ) : state.items.length === 0 ? (
              <EmptyState icon={GitPullRequest} title="No proposals" description="Nothing matches this filter." />
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Proposal</TableHead>
                    <TableHead>Schema</TableHead>
                    <TableHead>Change</TableHead>
                    <TableHead>Impact</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Proposed</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {state.items.map((p) => (
                    <TableRow key={p.id} data-testid="proposal-row">
                      <TableCell>
                        <Link href={`/schemas/proposals/${p.id}`} className="font-medium text-primary hover:underline">
                          {p.title}
                        </Link>
                      </TableCell>
                      <TableCell className="font-mono text-sm">{p.schema_code}</TableCell>
                      <TableCell className="font-mono text-sm">
                        v{p.base_version.version} → v{p.proposed_version.version}
                      </TableCell>
                      <TableCell>
                        {p.impact?.overall ? (
                          <CompatibilityBadge value={p.impact.overall} />
                        ) : (
                          <span className="text-xs text-muted-foreground">not analysed</span>
                        )}
                      </TableCell>
                      <TableCell>
                        <ProposalStatusPill status={p.status} />
                      </TableCell>
                      <TableCell className="text-sm text-muted-foreground">
                        {formatTimestamp(p.created_at)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
            {LIST_PAGING_UI_ENABLED && state.kind === 'ready' && state.items.length > 0 && (
              <ListPager page={state.list} paging={cursorPaging} testId="proposal-pager" />
            )}
          </PageSection>
        )}
      </div>
    </>
  )
}
