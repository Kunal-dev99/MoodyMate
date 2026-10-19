'use client'

import Link from 'next/link'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { useEffect, useMemo, useState } from 'react'
import { ChevronLeft, ChevronRight, ClipboardCheck, ShieldOff } from 'lucide-react'
import { Forbidden } from '@/components/auth/Forbidden'
import { useCurrentSession } from '@/components/auth/SessionProvider'
import { EmptyState } from '@/components/common/EmptyState'
import { LoadFailed } from '@/components/common/LoadFailed'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { MaskedValue, isMasked } from '@/components/common/MaskedValue'
import { PageSection } from '@/components/common/PageSection'
import { createLoader, type Loaded } from '@/components/common/load'
import { counterpartyHref, keyText } from '@/components/credit/credit'
import { RatingStatusPill } from '@/components/credit/ratings/RatingStatusPill'
import { RATING_STATUS_STYLE, isProposer, type ObligorRating } from '@/components/credit/ratings/ratings'
import { Button } from '@/components/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { RatingDecisionDialog } from './RatingDecisionDialog'
import {
  DEFAULT_STATUS,
  PAGE_SIZE,
  STATUS_FILTERS,
  decisionGate,
  fetchQueue,
  queueQuery,
  statusFilterOf,
  type RatingPage,
  type StatusFilter,
} from './review'
import { formatTimestamp } from '@/lib/format'
import { ActorLabel } from '@/components/common/ActorLabel'

const selectClass = 'h-9 rounded-md border border-input bg-background px-2 text-sm'
const filterLabel = (s: StatusFilter): string => (s === 'ALL' ? 'All statuses' : RATING_STATUS_STYLE[s].label)

/** A masked or plain text value from a rating version. */
function Text({ value, testId, className }: { value: ObligorRating['rationale'] | ObligorRating['decision_comment']; testId: string; className?: string }) {
  if (value === null) return <span className="text-muted-foreground" data-testid={testId}>—</span>
  if (isMasked(value))
    return (
      <span data-testid={testId}>
        <MaskedValue value={value} />
      </span>
    )
  return (
    <span className={className} data-testid={testId}>
      {value}
    </span>
  )
}

function Row({ r, onDecided }: { r: ObligorRating; onDecided: (r: ObligorRating) => void }) {
  const { me, has } = useCurrentSession()
  const gate = decisionGate(r, me, has)
  const counterparty = keyText(r.counterparty_key, r.counterparty_id)
  const grade = isMasked(r.grade) ? 'the masked grade' : r.grade
  return (
    <TableRow data-testid="review-row" data-id={r.rating_id} data-status={r.status} data-gate={gate} data-counterparty-id={r.counterparty_id}>
      <TableCell>
        <Link href={counterpartyHref(r.counterparty_id)} className="font-mono text-xs text-primary hover:underline" data-testid="review-counterparty">
          {counterparty}
        </Link>
      </TableCell>
      <TableCell data-testid="review-grade">
        {isMasked(r.grade) ? <MaskedValue value={r.grade} /> : <span className="font-mono text-sm">{r.grade}</span>}
        {r.grade_rank !== null && <div className="text-xs text-muted-foreground">rank {r.grade_rank}</div>}
      </TableCell>
      <TableCell className="font-mono text-sm" data-testid="review-effective-from">
        {r.effective_from}
      </TableCell>
      <TableCell>
        <RatingStatusPill status={r.status} testId="review-status" />
      </TableCell>
      <TableCell data-testid="review-proposer">
        <ActorLabel actor={r.proposed_by} you={isProposer(r, me)} className="text-xs" />
        <div className="font-mono text-xs text-muted-foreground">{formatTimestamp(r.proposed_at)}</div>
      </TableCell>
      <TableCell className="max-w-sm text-sm">
        <Text value={r.rationale} testId="review-rationale" className="whitespace-pre-wrap break-words" />
      </TableCell>
      <TableCell data-testid="review-decider">
        {r.decided_by ? (
          <>
            <ActorLabel actor={r.decided_by} className="text-xs" />
            {r.decided_at && <div className="font-mono text-xs text-muted-foreground">{formatTimestamp(r.decided_at)}</div>}
            <div className="mt-1 text-xs">
              <Text value={r.decision_comment} testId="review-decision-comment" className="whitespace-pre-wrap break-words" />
            </div>
          </>
        ) : (
          <span className="text-muted-foreground">—</span>
        )}
      </TableCell>
      <TableCell className="text-right">
        {gate === 'allowed' && (
          <span className="inline-flex gap-2">
            <RatingDecisionDialog rating={r} gradeLabel={grade} counterpartyLabel={counterparty} decision="APPROVE" onDecided={onDecided} />
            <RatingDecisionDialog rating={r} gradeLabel={grade} counterpartyLabel={counterparty} decision="REJECT" onDecided={onDecided} />
          </span>
        )}
        {gate === 'self_approval' && (
          <span className="inline-flex items-center gap-1 text-xs text-[hsl(var(--warning))]" data-testid="review-self-approval">
            <ShieldOff className="h-3.5 w-3.5" />
            You proposed this rating: another approver decides.
          </span>
        )}
        {gate === 'no_permission' && (
          <span className="text-xs text-muted-foreground" data-testid="review-awaiting-approver">
            Waiting for an approver (rating:approve).
          </span>
        )}
      </TableCell>
    </TableRow>
  )
}

/**
 * FE-20.2: the obligor ratings review queue (D-39 §3). Lists GET /obligor-ratings, IN_REVIEW by
 * default; the other statuses are a filter (superseded and rejected versions are hidden by
 * status, never deleted). Approve and Reject show only to a holder of `rating:approve` who did
 * not propose the version, and are cosmetic: the server checks on every POST (D-04).
 */
export function RatingsReviewQueue() {
  const router = useRouter()
  const pathname = usePathname()
  const params = useSearchParams()
  const status = statusFilterOf(params.get('status'))
  const [paging, setPaging] = useState<{ status: StatusFilter; offset: number }>({ status, offset: 0 })
  const offset = paging.status === status ? paging.offset : 0
  const [state, setState] = useState<Loaded<RatingPage>>({ kind: 'loading' })
  const [outcome, setOutcome] = useState<ObligorRating | null>(null)
  const loader = useMemo(() => createLoader(fetchQueue(queueQuery(status, offset)), setState, 'Could not load the ratings queue'), [status, offset])
  useEffect(() => {
    loader.load()
    return loader.abort
  }, [loader])

  const setStatus = (next: StatusFilter) => {
    const search = new URLSearchParams(params.toString())
    if (next === DEFAULT_STATUS) search.delete('status')
    else search.set('status', next)
    const qs = search.toString()
    router.replace(`${pathname}${qs ? `?${qs}` : ''}`, { scroll: false })
  }

  const decided = (r: ObligorRating) => {
    setOutcome(r)
    loader.load()
  }

  let body
  if (state.kind === 'loading') body = <LoadingBlock rows={4} />
  else if (state.kind === 'forbidden') body = <Forbidden permission="domain:read" correlationId={state.cid} />
  else if (state.kind === 'failed') body = <LoadFailed icon={ClipboardCheck} title="The ratings queue could not be loaded" cid={state.cid} onRetry={loader.load} />
  else if (state.kind === 'not_found')
    body = <EmptyState icon={ClipboardCheck} title="The ratings queue could not be listed" description={`The request was refused.${state.cid ? ` Correlation ID: ${state.cid}` : ''}`} />
  else {
    const page = state.value
    body =
      page.items.length === 0 ? (
        <EmptyState
          icon={ClipboardCheck}
          title={status === 'IN_REVIEW' ? 'Nothing is waiting for a decision' : 'No ratings in this status'}
          description={
            status === 'IN_REVIEW' ? 'A rating proposed on a counterparty’s detail page appears here until an approver decides it.' : 'Change the status filter to see other versions.'
          }
          className="py-8"
        />
      ) : (
        <>
          <Table data-testid="review-queue" data-total={page.total} data-status-filter={status}>
            <TableHeader>
              <TableRow>
                <TableHead>Counterparty</TableHead>
                <TableHead>Grade</TableHead>
                <TableHead>Effective from</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Proposed by</TableHead>
                <TableHead>Rationale</TableHead>
                <TableHead>Decided by</TableHead>
                <TableHead className="text-right">Decision</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {page.items.map((r) => (
                <Row key={r.rating_id} r={r} onDecided={decided} />
              ))}
            </TableBody>
          </Table>
          {page.total > PAGE_SIZE && (
            <div className="mt-3 flex items-center justify-between text-xs text-muted-foreground">
              <span data-testid="review-pager">
                {offset + 1}–{offset + page.items.length} of {page.total}
              </span>
              <div className="flex gap-2">
                <Button variant="outline" size="sm" disabled={offset === 0} onClick={() => setPaging({ status, offset: offset > PAGE_SIZE ? offset - PAGE_SIZE : 0 })}>
                  <ChevronLeft className="h-3.5 w-3.5" />
                  Previous
                </Button>
                <Button variant="outline" size="sm" disabled={offset + PAGE_SIZE >= page.total} onClick={() => setPaging({ status, offset: offset + PAGE_SIZE })}>
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
      icon={ClipboardCheck}
      title="Review queue"
      description="Maker-checker: a risk analyst proposes, an approver who is not the proposer approves or rejects with a comment. Every step and refusal is audited. Approving one version supersedes any ACTIVE version for the same counterparty and date."
      headerRight={
        <label className="flex items-center gap-2 text-xs text-muted-foreground">
          Status
          <select aria-label="Status" className={selectClass} value={status} onChange={(e) => setStatus(statusFilterOf(e.target.value))} data-testid="review-status-filter">
            {STATUS_FILTERS.map((s) => (
              <option key={s} value={s}>
                {filterLabel(s)}
              </option>
            ))}
          </select>
        </label>
      }
    >
      <div className="space-y-3">
        {outcome && (
          <p className="text-sm" data-testid="review-outcome" data-id={outcome.rating_id} data-status={outcome.status}>
            The server recorded the decision on the rating effective <span className="font-mono">{outcome.effective_from}</span> for{' '}
            <Link href={counterpartyHref(outcome.counterparty_id)} className="font-mono text-primary hover:underline" data-testid="review-outcome-counterparty">
              {keyText(outcome.counterparty_key, outcome.counterparty_id)}
            </Link>
            : it is now <RatingStatusPill status={outcome.status} testId="review-outcome-status" />.
          </p>
        )}
        {body}
      </div>
    </PageSection>
  )
}
