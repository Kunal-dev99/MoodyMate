'use client'

import Link from 'next/link'
import { useEffect, useState } from 'react'
import { GitPullRequest } from 'lucide-react'
import { api, call, ApiError } from '@/api/client'
import { Forbidden } from '@/components/auth/Forbidden'
import { useCurrentSession } from '@/components/auth/SessionProvider'
import { EmptyState } from '@/components/common/EmptyState'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { PageHeader } from '@/components/common/PageHeader'
import { PageSection } from '@/components/common/PageSection'
import { toastApiError } from '@/components/common/api-error-toast'
import { CompatibilityBadge } from '@/components/schemas/CompatibilityBadge'
import { ProposalStatusPill } from '@/components/schemas/ProposalStatusPill'
import { PROPOSAL_STATUS_STYLE, type Proposal, type ProposalStatus } from '@/components/schemas/proposals'
import { Button } from '@/components/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'

type State = { kind: 'loading' } | { kind: 'ready'; items: Proposal[] } | { kind: 'forbidden'; cid: string | null }

/** Schema change proposals (FE-5.1): the approver's queue and every proposer's history. */
export default function ProposalsPage() {
  const { has } = useCurrentSession()
  const [status, setStatus] = useState<ProposalStatus | ''>(has('schema:approve') ? 'SUBMITTED' : '')
  const [state, setState] = useState<State>({ kind: 'loading' })

  useEffect(() => {
    let current = true
    setState({ kind: 'loading' })
    call(() => api.GET('/api/v1/schema-change-proposals', { params: { query: { status: status || undefined, limit: 200 } } }))
      .then((list) => current && setState({ kind: 'ready', items: list.items }))
      .catch((err: unknown) => {
        if (!current) return
        if (err instanceof ApiError && err.status === 403) setState({ kind: 'forbidden', cid: err.correlationId })
        else toastApiError(err, 'Could not load proposals')
      })
    return () => {
      current = false
    }
  }, [status])

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
                        {new Date(p.created_at).toLocaleString()}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </PageSection>
        )}
      </div>
    </>
  )
}
