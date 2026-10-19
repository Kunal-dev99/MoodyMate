'use client'

import { useState } from 'react'
import { Check, X } from 'lucide-react'
import { toastApiError } from '@/components/common/api-error-toast'
import type { Refusal } from '@/components/ingestion/mappings'
import { RatingRefusalNotice } from '@/components/credit/ratings/RatingRefusalNotice'
import { ratingRefusal, type ObligorRating } from '@/components/credit/ratings/ratings'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { COMMENT_MAX, commentProblem, decideRating, decisionBody, type Decision } from './review'

const DECISION_TEXT: Record<Decision, { title: string; action: string; busy: string; description: string }> = {
  APPROVE: {
    title: 'Approve',
    action: 'Approve and activate',
    busy: 'Approving…',
    description:
      'Approving makes this version ACTIVE from its effective date and any ACTIVE version for the same counterparty and date SUPERSEDED, in one step. Runs submitted afterwards pin it; earlier runs keep their pins.',
  },
  REJECT: { title: 'Reject', action: 'Reject', busy: 'Rejecting…', description: 'Rejecting ends this version REJECTED. It never governs a date and is kept, never deleted.' },
}

/** A decision refusal: the shared rating titles, with "not found" naming the rating, not the counterparty. */
function decisionRefusal(err: unknown, decision: Decision): Refusal {
  const r = ratingRefusal(err, `Could not ${decision === 'APPROVE' ? 'approve' : 'reject'} the rating`)
  return r.code === 'not_found' ? { ...r, title: 'Rating not found' } : r
}

/**
 * FE-20.2: approve or reject one IN_REVIEW rating with a required comment (D-39 §3). Shown only
 * when the gate allows it; the server decides and audits, and its refusal (403, 409
 * `self_approval` / `stale_version` / `invalid_status` / `rating_change_disabled`) is shown as it
 * came. `onDecided` gets the version the server returned; the UI never sets a status itself.
 */
export function RatingDecisionDialog({
  rating,
  gradeLabel,
  counterpartyLabel,
  decision,
  onDecided,
}: {
  rating: Pick<ObligorRating, 'rating_id' | 'effective_from'>
  gradeLabel: string
  counterpartyLabel: string
  decision: Decision
  onDecided: (r: ObligorRating) => void
}) {
  const [open, setOpen] = useState(false)
  const [comment, setComment] = useState('')
  const [touched, setTouched] = useState(false)
  const [busy, setBusy] = useState(false)
  const [refusal, setRefusal] = useState<Refusal | null>(null)
  const text = DECISION_TEXT[decision]
  const problem = commentProblem(comment)
  const fieldId = `rating-decision-comment-${decision}-${rating.rating_id}`

  const change = (next: boolean) => {
    if (busy) return
    setOpen(next)
    if (!next) {
      setComment('')
      setTouched(false)
      setRefusal(null)
    }
  }

  async function confirm() {
    const body = decisionBody(decision, comment)
    if (!body || busy) {
      setTouched(true)
      return
    }
    setBusy(true)
    setRefusal(null)
    try {
      const decided = await decideRating(rating.rating_id, body)
      setOpen(false)
      setComment('')
      setTouched(false)
      onDecided(decided)
    } catch (err) {
      const r = decisionRefusal(err, decision)
      toastApiError(err, r.title)
      setRefusal(r)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={change}>
      <DialogTrigger asChild>
        <Button size="sm" variant={decision === 'APPROVE' ? 'default' : 'outline'} data-testid={decision === 'APPROVE' ? 'rating-approve' : 'rating-reject'}>
          {decision === 'APPROVE' ? <Check className="mr-2 h-4 w-4" /> : <X className="mr-2 h-4 w-4" />}
          {text.title}
        </Button>
      </DialogTrigger>
      <DialogContent data-testid="rating-decision-dialog" data-decision={decision} data-id={rating.rating_id} className="max-w-xl">
        <DialogHeader>
          <DialogTitle>
            {text.title} {gradeLabel} for {counterpartyLabel}, effective {rating.effective_from}
          </DialogTitle>
          <DialogDescription>{text.description} The decision is recorded with your name and audited.</DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
          <Label htmlFor={fieldId}>Comment (required)</Label>
          <Textarea
            id={fieldId}
            value={comment}
            disabled={busy}
            onChange={(e) => setComment(e.target.value)}
            onBlur={() => setTouched(true)}
            placeholder="What you checked and why"
            data-testid="rating-decision-comment"
          />
          <div className="flex justify-between text-xs">
            <span className="text-destructive" data-testid="rating-decision-comment-problem">
              {touched && problem}
            </span>
            <span className={problem && comment.trim() ? 'font-mono text-destructive' : 'font-mono text-muted-foreground'} data-testid="rating-decision-comment-count">
              {comment.trim().length}/{COMMENT_MAX}
            </span>
          </div>
        </div>
        {refusal && <RatingRefusalNotice refusal={refusal} />}
        <DialogFooter>
          <Button variant="outline" onClick={() => change(false)} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={confirm} disabled={!!problem || busy} variant={decision === 'REJECT' ? 'destructive' : 'default'} data-testid="rating-decision-confirm">
            {busy ? text.busy : text.action}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
