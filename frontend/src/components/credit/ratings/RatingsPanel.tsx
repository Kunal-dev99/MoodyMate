'use client'

import { useEffect, useRef, useState } from 'react'
import { Award } from 'lucide-react'
import { api, call } from '@/api/client'
import { useCurrentSession } from '@/components/auth/SessionProvider'
import { toastApiError } from '@/components/common/api-error-toast'
import { MaskedValue, isMasked } from '@/components/common/MaskedValue'
import { PageSection } from '@/components/common/PageSection'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { ProposeRatingDialog } from './ProposeRatingDialog'
import { RatingStatusPill } from './RatingStatusPill'
import { canProposeRating, isProposer, type ObligorRating } from './ratings'
import { formatTimestamp } from '@/lib/format'
import { ActorLabel } from '@/components/common/ActorLabel'

function Who({ actor, at, you, testId }: { actor: string | null; at: string | null; you: boolean; testId: string }) {
  if (!actor) return <span className="text-muted-foreground" data-testid={testId}>—</span>
  return (
    <div data-testid={testId}>
      <ActorLabel actor={actor} you={you} className="text-xs" />
      {at && <div className="font-mono text-xs text-muted-foreground">{formatTimestamp(at)}</div>}
    </div>
  )
}

/** Stateless: the rating history as the server lists it (effective_from descending, then proposed_at descending). */
export function RatingHistory({ ratings, myUserId }: { ratings: readonly ObligorRating[]; myUserId: string | null }) {
  if (ratings.length === 0)
    return (
      <p className="text-sm text-muted-foreground" data-testid="no-ratings">
        No rating has been proposed for this counterparty. A calculation excludes its exposures until one is approved (NO_APPROVED_RATING); the source
        origination grade is never used instead.
      </p>
    )
  return (
    <Table data-testid="rating-history" data-count={ratings.length}>
      <TableHeader>
        <TableRow>
          <TableHead>Grade</TableHead>
          <TableHead>Effective from</TableHead>
          <TableHead>Status</TableHead>
          <TableHead>Proposed by</TableHead>
          <TableHead>Decided by</TableHead>
          <TableHead>Rationale</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {ratings.map((r) => (
          <TableRow key={r.rating_id} data-testid="rating-row" data-id={r.rating_id} data-status={r.status}>
            <TableCell data-testid="rating-grade-cell">
              {isMasked(r.grade) ? (
                <MaskedValue value={r.grade} />
              ) : (
                <>
                  <span className="font-mono text-sm">{r.grade}</span>
                  {r.grade_rank !== null && <span className="ml-2 font-mono text-xs text-muted-foreground">rank {r.grade_rank}</span>}
                </>
              )}
            </TableCell>
            <TableCell className="font-mono text-sm" data-testid="rating-effective-from-cell">
              {r.effective_from}
            </TableCell>
            <TableCell>
              <RatingStatusPill status={r.status} />
              {r.status === 'IN_REVIEW' && <div className="mt-1 text-xs text-muted-foreground">Waiting for an approver who is not the proposer.</div>}
              {r.status === 'SUPERSEDED' && r.superseded_at && (
                <div className="mt-1 font-mono text-xs text-muted-foreground">{formatTimestamp(r.superseded_at)}</div>
              )}
            </TableCell>
            <TableCell>
              <Who actor={r.proposed_by} at={r.proposed_at} you={myUserId !== null && isProposer(r, { user_id: myUserId })} testId="rating-proposer" />
            </TableCell>
            <TableCell>
              <Who actor={r.decided_by} at={r.decided_at} you={false} testId="rating-decider" />
            </TableCell>
            <TableCell className="max-w-sm text-sm" data-testid="rating-rationale-cell">
              {isMasked(r.rationale) ? <MaskedValue value={r.rationale} /> : <span className="line-clamp-3 whitespace-pre-line">{r.rationale}</span>}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

/**
 * FE-20.1: the Ratings panel on the counterparty detail (behind NEXT_PUBLIC_RATINGS_UI; the
 * caller mounts it). The history (grade, effective date, status, proposer, decider) comes from
 * the counterparty detail; after a proposal the panel re-reads it from the server, so a status is
 * only ever the server's. "Propose rating" shows with rating:propose (cosmetic; the server checks
 * it, D-04). Approving and rejecting are the review queue's (FE-20.2), never this panel's.
 */
export function RatingsPanel({ counterpartyId, counterpartyLabel, ratings }: { counterpartyId: string; counterpartyLabel: string; ratings: ObligorRating[] }) {
  const { me, has } = useCurrentSession()
  const [history, setHistory] = useState<ObligorRating[]>(ratings)
  const [proposed, setProposed] = useState<ObligorRating | null>(null)
  const reload = useRef<AbortController | null>(null)

  useEffect(() => setHistory(ratings), [ratings])
  useEffect(() => () => reload.current?.abort(), [])

  const onProposed = async (r: ObligorRating) => {
    setProposed(r)
    reload.current?.abort()
    const controller = new AbortController()
    reload.current = controller
    try {
      const detail = await call(() => api.GET('/api/v1/counterparties/{counterparty_id}', { params: { path: { counterparty_id: counterpartyId } }, signal: controller.signal }))
      if (!controller.signal.aborted) setHistory(detail.ratings)
    } catch (err) {
      if (!controller.signal.aborted) toastApiError(err, 'Could not reload the rating history')
    }
  }

  return (
    <PageSection
      icon={Award}
      title={`Ratings (${history.length})`}
      description="Obligor rating versions: proposed by a risk analyst, approved or rejected by a different approver. Versions are immutable; superseded and rejected ones are kept, never deleted. Grades are human judgements: nothing here suggests one."
      headerRight={canProposeRating(has) ? <ProposeRatingDialog counterpartyId={counterpartyId} counterpartyLabel={counterpartyLabel} onProposed={onProposed} /> : undefined}
    >
      <div className="space-y-3" data-testid="ratings-panel" data-counterparty-id={counterpartyId}>
        {proposed && (
          <p className="text-sm" data-testid="rating-proposed" data-id={proposed.rating_id} data-status={proposed.status}>
            The server recorded your proposal of {isMasked(proposed.grade) ? 'a grade' : <span className="font-mono">{proposed.grade}</span>} from{' '}
            <span className="font-mono">{proposed.effective_from}</span>: it is now <RatingStatusPill status={proposed.status} testId="rating-proposed-status" />.
          </p>
        )}
        <RatingHistory ratings={history} myUserId={me.user_id} />
      </div>
    </PageSection>
  )
}
