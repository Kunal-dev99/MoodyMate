'use client'

import { useState } from 'react'
import { FilePlus2 } from 'lucide-react'
import { toastApiError } from '@/components/common/api-error-toast'
import { startLoad, type LoadFailure } from '@/components/common/load'
import type { Refusal } from '@/components/ingestion/mappings'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'
import { RatingRefusalNotice } from './RatingRefusalNotice'
import {
  EMPTY_PROPOSAL,
  RATIONALE_MAX,
  fetchCreditVocabularies,
  gradeLabel,
  gradeOptions,
  proposalBody,
  proposalProblems,
  proposeRating,
  rationaleCounter,
  ratingRefusal,
  type ObligorRating,
  type ProposalField,
  type ProposalForm,
  type VocabularyEntry,
} from './ratings'

const selectClass = 'h-9 w-full rounded-md border border-input bg-background px-2 text-sm'

type Grades = { kind: 'loading' } | { kind: 'ready'; items: VocabularyEntry[] } | LoadFailure

/**
 * The form's fields, stateless (FE-20.1): a grade picker from GET /credit-vocabularies, an
 * effective date and a required rationale with a character counter. A field's problem shows once
 * it was touched (or `showAll`, after a submit attempt). Rendered by the dialog and by vitest.
 */
export function ProposalFields({
  form,
  grades,
  touched,
  showAll = false,
  disabled = false,
  onChange,
  onTouch,
}: {
  form: ProposalForm
  grades: Grades
  touched: ReadonlySet<ProposalField>
  showAll?: boolean
  disabled?: boolean
  onChange: (next: ProposalForm) => void
  onTouch: (field: ProposalField) => void
}) {
  const options = grades.kind === 'ready' ? gradeOptions(grades.items) : []
  const problems = proposalProblems(
    form,
    options.map((o) => o.code),
  )
  const shown = (f: ProposalField) => (showAll || touched.has(f) ? problems[f] : undefined)
  const counter = rationaleCounter(form.rationale)
  const anyProvisional = options.some((o) => o.provisional)

  return (
    <div className="space-y-4" data-testid="rating-proposal-fields">
      <div className="space-y-1">
        <Label htmlFor="rating-grade">Grade</Label>
        {grades.kind === 'loading' && (
          <p className="text-sm text-muted-foreground" data-testid="rating-grades-loading">
            Loading the rating grades…
          </p>
        )}
        {(grades.kind === 'failed' || grades.kind === 'not_found' || grades.kind === 'forbidden') && (
          <p className="text-sm text-destructive" data-testid="rating-grades-failed">
            The rating grades could not be loaded{grades.cid ? ` (Correlation ID: ${grades.cid})` : ''}. Close and try again.
          </p>
        )}
        {grades.kind === 'ready' && options.length === 0 && (
          <p className="text-sm text-muted-foreground" data-testid="rating-grades-empty">
            No rating grade is seeded for your tenant, so no rating can be proposed.
          </p>
        )}
        {grades.kind === 'ready' && options.length > 0 && (
          <select
            id="rating-grade"
            className={selectClass}
            value={form.grade}
            disabled={disabled}
            onChange={(e) => onChange({ ...form, grade: e.target.value })}
            onBlur={() => onTouch('grade')}
            aria-invalid={!!shown('grade')}
            data-testid="rating-grade"
          >
            <option value="">Choose a grade</option>
            {options.map((o) => (
              <option key={o.code} value={o.code}>
                {gradeLabel(o)}
              </option>
            ))}
          </select>
        )}
        {anyProvisional && (
          <p className="text-xs text-muted-foreground" data-testid="rating-grades-provisional">
            The grade scale is provisional / illustrative (seeded until the client defines its own). Codes are neutral; no rating-agency scale is implied.
          </p>
        )}
        <p className="text-xs text-destructive" data-testid="rating-grade-problem">
          {shown('grade')}
        </p>
      </div>

      <div className="space-y-1">
        <Label htmlFor="rating-effective-from">Effective from</Label>
        <Input
          id="rating-effective-from"
          type="date"
          className="w-48 font-mono"
          value={form.effective_from}
          disabled={disabled}
          onChange={(e) => onChange({ ...form, effective_from: e.target.value })}
          onBlur={() => onTouch('effective_from')}
          aria-invalid={!!shown('effective_from')}
          data-testid="rating-effective-from"
        />
        <p className="text-xs text-muted-foreground">The first date the grade governs, once approved. It governs until a later approved effective date.</p>
        <p className="text-xs text-destructive" data-testid="rating-effective-from-problem">
          {shown('effective_from')}
        </p>
      </div>

      <div className="space-y-1">
        <Label htmlFor="rating-rationale">Rationale (required)</Label>
        <Textarea
          id="rating-rationale"
          value={form.rationale}
          disabled={disabled}
          rows={5}
          onChange={(e) => onChange({ ...form, rationale: e.target.value })}
          onBlur={() => onTouch('rationale')}
          aria-invalid={!!shown('rationale')}
          placeholder="Why this grade: the evidence you weighed"
          data-testid="rating-rationale"
        />
        <div className="flex justify-between gap-4 text-xs">
          <span className="text-destructive" data-testid="rating-rationale-problem">
            {shown('rationale')}
          </span>
          <span className={cn('font-mono', counter.over ? 'text-destructive' : 'text-muted-foreground')} data-testid="rating-rationale-counter" data-over={counter.over}>
            {counter.text}
          </span>
        </div>
      </div>
    </div>
  )
}

/**
 * FE-20.1: "Propose rating" (POST /counterparties/{id}/ratings, rating:propose). The proposal is
 * written IN_REVIEW by the server; the dialog closes only on its 201 and hands the server's
 * version to `onProposed`, so the panel reloads the history from the server. A refusal (403,
 * 404, 409 `rating_change_disabled`, 422 `invalid_grade`) is shown as it came (D-04). The caller
 * shows this only with rating:propose; that is cosmetic, the server checks it.
 */
export function ProposeRatingDialog({ counterpartyId, counterpartyLabel, onProposed }: { counterpartyId: string; counterpartyLabel: string; onProposed: (r: ObligorRating) => void }) {
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState<ProposalForm>(EMPTY_PROPOSAL)
  const [touched, setTouched] = useState<Set<ProposalField>>(new Set())
  const [attempted, setAttempted] = useState(false)
  const [grades, setGrades] = useState<Grades>({ kind: 'loading' })
  const [busy, setBusy] = useState(false)
  const [refusal, setRefusal] = useState<Refusal | null>(null)

  const codes = grades.kind === 'ready' ? gradeOptions(grades.items).map((o) => o.code) : []
  const body = grades.kind === 'ready' ? proposalBody(form, codes) : null

  const change = (next: boolean) => {
    if (busy) return
    setOpen(next)
    if (next) {
      setForm(EMPTY_PROPOSAL)
      setTouched(new Set())
      setAttempted(false)
      setRefusal(null)
      setGrades({ kind: 'loading' })
      startLoad(fetchCreditVocabularies, {
        onReady: (list) => setGrades({ kind: 'ready', items: list.items }),
        onFailure: setGrades,
        failureTitle: 'Could not load the rating grades',
      })
    }
  }

  const submit = async () => {
    setAttempted(true)
    if (!body || busy) return
    setBusy(true)
    setRefusal(null)
    try {
      const created = await proposeRating(counterpartyId, body)
      setOpen(false)
      onProposed(created)
    } catch (err) {
      const r = ratingRefusal(err, 'Could not propose the rating')
      toastApiError(err, r.title)
      setRefusal(r)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={change}>
      <DialogTrigger asChild>
        <Button size="sm" variant="outline" data-testid="propose-rating-open">
          <FilePlus2 className="mr-2 h-4 w-4" />
          Propose rating
        </Button>
      </DialogTrigger>
      <DialogContent data-testid="propose-rating-dialog" className="max-w-xl">
        <DialogHeader>
          <DialogTitle>Propose a rating for {counterpartyLabel}</DialogTitle>
          <DialogDescription>
            Written IN_REVIEW with your name and the time, and audited. It governs nothing until an approver who is not you approves it. A proposal cannot be
            edited: a correction is a new proposal. The rationale is confidential (at most {RATIONALE_MAX.toLocaleString('en')} characters).
          </DialogDescription>
        </DialogHeader>
        <ProposalFields
          form={form}
          grades={grades}
          touched={touched}
          showAll={attempted}
          disabled={busy}
          onChange={setForm}
          onTouch={(f) => setTouched((t) => new Set(t).add(f))}
        />
        {refusal && <RatingRefusalNotice refusal={refusal} />}
        <DialogFooter>
          <Button variant="outline" onClick={() => change(false)} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={busy || grades.kind !== 'ready' || (attempted && !body)} data-testid="propose-rating-submit">
            {busy ? 'Proposing…' : 'Propose for review'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
