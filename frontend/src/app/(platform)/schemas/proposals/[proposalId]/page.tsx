'use client'

import Link from 'next/link'
import { useParams, useRouter } from 'next/navigation'
import { useCallback, useEffect, useState } from 'react'
import { Activity, CheckCircle2, FileDiff, Gavel, Undo2 } from 'lucide-react'
import { api, call, ApiError } from '@/api/client'
import { Forbidden } from '@/components/auth/Forbidden'
import { useCurrentSession } from '@/components/auth/SessionProvider'
import { EmptyState } from '@/components/common/EmptyState'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { PageHeader } from '@/components/common/PageHeader'
import { PageSection } from '@/components/common/PageSection'
import { toastApiError } from '@/components/common/api-error-toast'
import { ImpactPanel } from '@/components/schemas/ImpactPanel'
import { ProposalStatusPill } from '@/components/schemas/ProposalStatusPill'
import { StatusPill } from '@/components/schemas/StatusPill'
import { canDecide, canSubmit, type Proposal } from '@/components/schemas/proposals'
import { typeLabel } from '@/components/schemas/registry'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Textarea } from '@/components/ui/textarea'

type State = { kind: 'loading' } | { kind: 'ready'; p: Proposal } | { kind: 'error'; status: number; cid: string | null }

/** One proposal through its workflow (FE-5.1): analyse → submit → decide → (revert). */
export default function ProposalDetailPage() {
  const { proposalId } = useParams<{ proposalId: string }>()
  const router = useRouter()
  const { me, has } = useCurrentSession()
  const [state, setState] = useState<State>({ kind: 'loading' })
  const [busy, setBusy] = useState(false)
  const [evidence, setEvidence] = useState('')
  const [comment, setComment] = useState('')

  const load = useCallback(() => {
    call(() => api.GET('/api/v1/schema-change-proposals/{proposal_id}', { params: { path: { proposal_id: proposalId } } }))
      .then((p) => setState({ kind: 'ready', p }))
      .catch((err: unknown) => {
        if (err instanceof ApiError) setState({ kind: 'error', status: err.status, cid: err.correlationId })
        else toastApiError(err, 'Could not load the proposal')
      })
  }, [proposalId])

  useEffect(load, [load])

  /** Runs one workflow action; the server's answer (including 403/409) is authoritative. */
  const act = async (label: string, request: () => Promise<Proposal>, after?: (p: Proposal) => void) => {
    setBusy(true)
    try {
      const p = await request()
      if (after) after(p)
      else setState({ kind: 'ready', p })
    } catch (err) {
      toastApiError(err, label)
      load()
    } finally {
      setBusy(false)
    }
  }

  if (state.kind === 'error') {
    if (state.status === 403) return <Forbidden permission="schema:read" correlationId={state.cid} />
    return (
      <div className="p-6">
        <EmptyState icon={FileDiff} title="Proposal not found" description="It does not exist or is not visible to you." />
      </div>
    )
  }
  if (state.kind === 'loading') return <LoadingBlock rows={6} />

  const { p } = state
  const path = { proposal_id: p.id }
  const mine = p.proposer_user_id === me.user_id
  const submitAllowed = canSubmit(p, me.user_id)
  const decisionAllowed = canDecide(p, me.user_id, has('schema:approve'))

  return (
    <>
      <PageHeader
        title={p.title}
        description={p.rationale ?? undefined}
        breadcrumbs={[
          { label: 'Source & Schemas', href: '/schemas' },
          { label: 'Proposals', href: '/schemas/proposals' },
          { label: `${p.schema_code} v${p.proposed_version.version}` },
        ]}
        actions={<ProposalStatusPill status={p.status} />}
      />
      <div className="space-y-4 px-6 pb-6">
        <PageSection icon={FileDiff} title="Change">
          <dl className="grid grid-cols-[180px_1fr] gap-y-1 text-sm">
            <dt className="text-muted-foreground">Schema</dt>
            <dd>
              <Link className="text-primary hover:underline" href={`/schemas/${p.schema_id}`}>
                {p.schema_name}
              </Link>{' '}
              <span className="font-mono text-xs text-muted-foreground">{p.schema_code}</span>
            </dd>
            <dt className="text-muted-foreground">Versions</dt>
            <dd className="flex items-center gap-2">
              v{p.base_version.version} <StatusPill status={p.base_version.status} /> → v{p.proposed_version.version}{' '}
              <StatusPill status={p.proposed_version.status} />
            </dd>
            <dt className="text-muted-foreground">Proposed by</dt>
            <dd className="font-mono text-xs">
              {p.proposer} {mine && <span className="font-sans text-muted-foreground">(you)</span>}
            </dd>
            {p.reverts_proposal_id && (
              <>
                <dt className="text-muted-foreground">Reverts</dt>
                <dd>
                  <Link className="text-primary hover:underline" href={`/schemas/proposals/${p.reverts_proposal_id}`}>
                    the applied change it undoes
                  </Link>
                </dd>
              </>
            )}
            {p.regression_evidence_ref && (
              <>
                <dt className="text-muted-foreground">Regression evidence</dt>
                <dd>{p.regression_evidence_ref}</dd>
              </>
            )}
            {p.decided_by && (
              <>
                <dt className="text-muted-foreground">Decided by</dt>
                <dd className="font-mono text-xs">
                  {p.decided_by} · {p.decided_at && new Date(p.decided_at).toLocaleString()}
                  {p.decision_comment && <span className="ml-2 font-sans text-sm">“{p.decision_comment}”</span>}
                </dd>
              </>
            )}
          </dl>
          <details className="mt-3">
            <summary className="cursor-pointer text-sm text-muted-foreground">Proposed contract ({p.proposed_fields.length} fields)</summary>
            <Table className="mt-2">
              <TableHeader>
                <TableRow>
                  <TableHead>Field</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Required</TableHead>
                  <TableHead>Classification</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {p.proposed_fields.map((f) => (
                  <TableRow key={f.name}>
                    <TableCell className="font-mono text-sm">{f.name}</TableCell>
                    <TableCell className="font-mono text-sm">{typeLabel(f)}</TableCell>
                    <TableCell className="text-sm">{f.required ? 'Yes' : 'No'}</TableCell>
                    <TableCell className="text-sm">{f.classification}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </details>
        </PageSection>

        <PageSection
          icon={Activity}
          title="Impact analysis"
          accent={p.impact?.overall === 'BREAKING' ? 'danger' : 'primary'}
          headerRight={
            p.status === 'DRAFT' && has('schema:propose') ? (
              <Button
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={() =>
                  act('Impact analysis failed', () =>
                    call(() => api.POST('/api/v1/schema-change-proposals/{proposal_id}/impact-analysis', { params: { path } })),
                  )
                }
              >
                {p.impact ? 'Re-run analysis' : 'Run impact analysis'}
              </Button>
            ) : undefined
          }
        >
          {p.impact ? (
            <ImpactPanel impact={p.impact} />
          ) : (
            <p className="text-sm text-muted-foreground" data-testid="impact-missing">
              Not analysed yet. The proposal cannot be submitted until it is.
            </p>
          )}
        </PageSection>

        {p.status === 'DRAFT' && mine && (
          <PageSection icon={CheckCircle2} title="Submit for approval">
            <div className="flex flex-wrap items-center gap-2">
              <Input
                className="max-w-md"
                placeholder="Regression evidence (link or reference, optional)"
                value={evidence}
                onChange={(e) => setEvidence(e.target.value)}
                maxLength={500}
              />
              <Button
                data-testid="submit-proposal"
                disabled={!submitAllowed || busy}
                title={submitAllowed ? undefined : 'Run the impact analysis first'}
                onClick={() =>
                  act('Could not submit', () =>
                    call(() =>
                      api.POST('/api/v1/schema-change-proposals/{proposal_id}/submit', {
                        params: { path },
                        body: { regression_evidence_ref: evidence.trim() || null },
                      }),
                    ),
                  )
                }
              >
                Submit for approval
              </Button>
            </div>
            {!p.impact && <p className="mt-2 text-xs text-muted-foreground">Submit unlocks once the impact analysis is recorded.</p>}
          </PageSection>
        )}

        {p.status === 'SUBMITTED' && (
          <PageSection icon={Gavel} title="Approver decision" accent="warning">
            {decisionAllowed ? (
              <div className="space-y-2">
                <Textarea
                  placeholder="Comment (required to reject)"
                  value={comment}
                  onChange={(e) => setComment(e.target.value)}
                  maxLength={2000}
                  aria-label="Decision comment"
                />
                <div className="flex gap-2">
                  <Button
                    data-testid="approve-proposal"
                    disabled={busy}
                    onClick={() =>
                      act('Approval failed', () =>
                        call(() =>
                          api.POST('/api/v1/schema-change-proposals/{proposal_id}/approval', {
                            params: { path },
                            body: { decision: 'APPROVE', comment: comment.trim() || null },
                          }),
                        ),
                      )
                    }
                  >
                    Approve and activate v{p.proposed_version.version}
                  </Button>
                  <Button
                    variant="destructive"
                    data-testid="reject-proposal"
                    disabled={busy || !comment.trim()}
                    onClick={() =>
                      act('Rejection failed', () =>
                        call(() =>
                          api.POST('/api/v1/schema-change-proposals/{proposal_id}/approval', {
                            params: { path },
                            body: { decision: 'REJECT', comment: comment.trim() },
                          }),
                        ),
                      )
                    }
                  >
                    Reject
                  </Button>
                </div>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground" data-testid="awaiting-approver">
                {mine
                  ? 'Waiting for an approver. You cannot approve your own proposal.'
                  : 'Waiting for a user with schema approval rights.'}
              </p>
            )}
          </PageSection>
        )}

        {p.status === 'APPLIED' && has('schema:propose') && (
          <PageSection icon={Undo2} title="Revert">
            <p className="mb-2 text-sm text-muted-foreground">
              Creates a new proposal that reinstates the v{p.base_version.version} contract as a new version. History is never edited.
            </p>
            <Button
              variant="outline"
              disabled={busy}
              onClick={() =>
                act(
                  'Could not create the revert',
                  () => call(() => api.POST('/api/v1/schema-change-proposals/{proposal_id}/revert', { params: { path } })),
                  (revert) => router.push(`/schemas/proposals/${revert.id}`),
                )
              }
            >
              Propose revert
            </Button>
          </PageSection>
        )}
      </div>
    </>
  )
}
