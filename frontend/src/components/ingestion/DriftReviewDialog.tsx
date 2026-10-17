'use client'

import { useState } from 'react'
import { ClipboardCheck } from 'lucide-react'
import { api, call } from '@/api/client'
import { toastApiError } from '@/components/common/api-error-toast'
import { DriftTypePill } from '@/components/ingestion/DriftPills'
import { RefusalNotice } from '@/components/ingestion/RefusalNotice'
import { COMMENT_MAX, REVIEW_OUTCOME, commentProblem, driftRefusal, reviewBody, reviewOutcomes, type DriftEvent, type DriftReviewOutcome } from '@/components/ingestion/drift'
import type { Refusal } from '@/components/ingestion/mappings'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'

/*
 * FE-12.2: review one OPEN drift event (POST /drift-events/{id}/review, quality:review).
 * The review is recorded once by the server, with the reviewer and the time, and audited;
 * the dialog only closes on the server's 200 and the list is then reloaded from the server.
 * Nothing else changes: ACTION_REQUIRED only links to the proposal page.
 */

export function DriftReviewDialog({ event, onReviewed }: { event: DriftEvent; onReviewed: () => void }) {
  const [open, setOpen] = useState(false)
  const [outcome, setOutcome] = useState<DriftReviewOutcome | null>(null)
  const [comment, setComment] = useState('')
  const [touched, setTouched] = useState(false)
  const [busy, setBusy] = useState(false)
  const [refusal, setRefusal] = useState<Refusal | null>(null)
  // A refusal may mean the record moved on (e.g. 409 already_reviewed): reload it on close.
  const [stale, setStale] = useState(false)

  const outcomes = reviewOutcomes(event)
  if (outcomes.length === 0) return null
  const problem = commentProblem(comment)
  const body = reviewBody(outcome, comment)

  const change = (next: boolean) => {
    if (busy) return
    setOpen(next)
    if (next) {
      setOutcome(null)
      setComment('')
      setTouched(false)
      setRefusal(null)
    } else if (stale) {
      setStale(false)
      onReviewed()
    }
  }

  const submit = async () => {
    if (!body) return
    setBusy(true)
    setRefusal(null)
    try {
      await call(() => api.POST('/api/v1/drift-events/{drift_event_id}/review', { params: { path: { drift_event_id: event.id } }, body }))
      setOpen(false)
      onReviewed()
    } catch (err) {
      const r = driftRefusal(err, 'Could not record the review')
      toastApiError(err, r.title)
      setRefusal(r)
      setStale(true)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={change}>
      <DialogTrigger asChild>
        <Button size="sm" variant="secondary" data-testid="drift-review-open">
          <ClipboardCheck className="mr-2 h-4 w-4" />
          Review
        </Button>
      </DialogTrigger>
      <DialogContent data-testid="drift-review-dialog" className="max-w-xl">
        <DialogHeader>
          <DialogTitle className="flex flex-wrap items-center gap-2">
            Review drift <DriftTypePill type={event.drift_type} /> <span className="font-mono text-sm">{event.column}</span>
          </DialogTitle>
          <DialogDescription>
            Recorded once, with your name and the time, and audited. No schema, contract or mapping changes. A second review is refused.
          </DialogDescription>
        </DialogHeader>

        <fieldset className="space-y-2" disabled={busy}>
          <legend className="mb-2 text-sm font-medium">Outcome</legend>
          {outcomes.map((o) => (
            <label
              key={o}
              className={cn(
                'flex cursor-pointer items-start gap-3 rounded-md border border-border px-3 py-2',
                outcome === o && 'border-primary bg-surface-2',
              )}
            >
              <input
                type="radio"
                className="mt-1"
                name={`drift-outcome-${event.id}`}
                value={o}
                checked={outcome === o}
                onChange={() => setOutcome(o)}
                data-testid="drift-review-outcome"
                data-outcome={o}
              />
              <span>
                <span className="block text-sm font-medium">{REVIEW_OUTCOME[o].label}</span>
                <span className="block text-xs text-muted-foreground">{REVIEW_OUTCOME[o].hint}</span>
              </span>
            </label>
          ))}
        </fieldset>

        <div className="space-y-2">
          <Label htmlFor={`drift-comment-${event.id}`}>Comment (required)</Label>
          <Textarea
            id={`drift-comment-${event.id}`}
            value={comment}
            maxLength={COMMENT_MAX}
            disabled={busy}
            onChange={(e) => setComment(e.target.value)}
            onBlur={() => setTouched(true)}
            placeholder="What you checked and why this outcome"
            data-testid="drift-review-comment-input"
          />
          <div className="flex justify-between text-xs">
            <span className="text-destructive" data-testid="drift-review-problem">
              {touched && problem}
            </span>
            <span className="font-mono text-muted-foreground">
              {comment.trim().length}/{COMMENT_MAX}
            </span>
          </div>
        </div>

        {refusal && <RefusalNotice refusal={refusal} />}

        <DialogFooter>
          <Button variant="outline" onClick={() => change(false)} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={!body || busy} data-testid="drift-review-submit">
            {busy ? 'Recording…' : 'Record review'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
