'use client'

import Link from 'next/link'
import { useState } from 'react'
import { Check, CopyPlus, Pencil, Send, ShieldCheck, ShieldOff, X } from 'lucide-react'
import { useCurrentSession } from '@/components/auth/SessionProvider'
import { toastApiError } from '@/components/common/api-error-toast'
import type { Refusal } from '@/components/ingestion/mappings'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { ScenarioRefusalNotice } from './ScenarioRefusalNotice'
import {
  COMMENT_MAX,
  acknowledged,
  acknowledgement,
  canAdapt,
  canConfirm,
  canEdit,
  canSubmit,
  commentProblem,
  confirmVersion,
  decideVersion,
  decisionBody,
  decisionGate,
  editVersionHref,
  needsAiReview,
  newVersionHref,
  proposedDefaults,
  scenarioRefusal,
  submitVersion,
  type Decision,
  type ScenarioVersion,
} from './scenarios'

export type StepVerb = 'confirmed' | 'submitted' | 'decided'

/** The AI-origin acknowledgements an AI_PROPOSED version needs before confirm or submit (D-44 §6). */
function AiAcknowledgement({
  version,
  aiOrigin,
  defaults,
  onAiOrigin,
  onDefaults,
}: {
  version: ScenarioVersion
  aiOrigin: boolean
  defaults: string[]
  onAiOrigin: (v: boolean) => void
  onDefaults: (v: string[]) => void
}) {
  if (!needsAiReview(version)) return null
  const id = version.scenario_version_id
  return (
    <div className="space-y-2 rounded-md border border-border p-3" data-testid="ai-acknowledgement">
      <div className="flex items-start gap-2 text-sm">
        <Checkbox id={`ack-ai-${id}`} checked={aiOrigin} onCheckedChange={(v) => onAiOrigin(v === true)} data-testid="ack-ai-origin" />
        <Label htmlFor={`ack-ai-${id}`}>I reviewed this AI-proposed version and its evidence; I take responsibility for it.</Label>
      </div>
      {proposedDefaults(version).map((path) => (
        <div key={path} className="flex items-start gap-2 text-sm">
          <Checkbox
            id={`ack-${id}-${path}`}
            checked={defaults.includes(path)}
            onCheckedChange={(v) => onDefaults(v === true ? [...defaults.filter((p) => p !== path), path] : defaults.filter((p) => p !== path))}
            data-testid="ack-default"
            data-path={path}
          />
          <Label htmlFor={`ack-${id}-${path}`}>
            I accept the proposed default <span className="font-mono">{path}</span>
          </Label>
        </div>
      ))}
    </div>
  )
}

/** "Confirm for exploration" and "Submit for approval": the author's own steps, each in a dialog. */
function AuthorStep({ scenarioId, version, step, onDone }: { scenarioId: string; version: ScenarioVersion; step: 'confirm' | 'submit'; onDone: (verb: StepVerb, v: ScenarioVersion) => void }) {
  const [open, setOpen] = useState(false)
  const [comment, setComment] = useState('')
  const [aiOrigin, setAiOrigin] = useState(false)
  const [defaults, setDefaults] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [refusal, setRefusal] = useState<Refusal | null>(null)
  const confirm = step === 'confirm'
  const problem = confirm ? null : commentProblem(comment, false)
  const ready = !problem && acknowledged(version, aiOrigin, defaults)

  const change = (next: boolean) => {
    if (busy) return
    setOpen(next)
    if (!next) {
      setComment('')
      setAiOrigin(false)
      setDefaults([])
      setRefusal(null)
    }
  }

  async function send() {
    if (!ready || busy) return
    setBusy(true)
    setRefusal(null)
    const ack = acknowledgement(version, aiOrigin, defaults)
    try {
      const done = confirm
        ? await confirmVersion(scenarioId, version.version, ack)
        : await submitVersion(scenarioId, version.version, { ...ack, comment: comment.trim() || null })
      setOpen(false)
      onDone(confirm ? 'confirmed' : 'submitted', done)
    } catch (err) {
      const r = scenarioRefusal(err, confirm ? 'Could not confirm the version' : 'Could not submit the version')
      toastApiError(err, r.title)
      setRefusal(r)
    } finally {
      setBusy(false)
    }
  }

  const testId = confirm ? 'version-confirm' : 'version-submit'
  return (
    <Dialog open={open} onOpenChange={change}>
      <DialogTrigger asChild>
        <Button size="sm" variant={confirm ? 'default' : 'outline'} data-testid={testId}>
          {confirm ? <ShieldCheck className="mr-2 h-4 w-4" /> : <Send className="mr-2 h-4 w-4" />}
          {confirm ? 'Confirm for exploration' : 'Submit for approval'}
        </Button>
      </DialogTrigger>
      <DialogContent className="max-w-xl" data-testid={`${testId}-dialog`}>
        <DialogHeader>
          <DialogTitle>
            {confirm ? 'Confirm' : 'Submit'} v{version.version} {confirm ? 'for exploration' : 'for approval'}
          </DialogTitle>
          <DialogDescription>
            {confirm
              ? 'You confirm your own version for exploration runs only. They are labelled "Exploration, not approved", are never authoritative and never appear in the shared library. The shocks freeze from now on.'
              : 'You submit this version for promotion. An approver who is not you approves or rejects it with a comment; only then can it join the shared library. The shocks freeze from now on.'}{' '}
            The step is recorded with your name and audited.
          </DialogDescription>
        </DialogHeader>
        {!confirm && (
          <div className="space-y-1">
            <Label htmlFor={`submit-comment-${version.scenario_version_id}`}>Comment (optional)</Label>
            <Textarea
              id={`submit-comment-${version.scenario_version_id}`}
              value={comment}
              maxLength={COMMENT_MAX}
              disabled={busy}
              onChange={(e) => setComment(e.target.value)}
              placeholder="What the approver should check"
              data-testid="submit-comment"
            />
            {problem && <p className="text-xs text-destructive">{problem}</p>}
          </div>
        )}
        <AiAcknowledgement version={version} aiOrigin={aiOrigin} defaults={defaults} onAiOrigin={setAiOrigin} onDefaults={setDefaults} />
        {refusal && <ScenarioRefusalNotice refusal={refusal} />}
        <DialogFooter>
          <Button variant="outline" onClick={() => change(false)} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={send} disabled={!ready || busy} data-testid={`${testId}-send`}>
            {busy ? (confirm ? 'Confirming…' : 'Submitting…') : confirm ? 'Confirm for exploration' : 'Submit for approval'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

const DECISION_TEXT: Record<Decision, { title: string; action: string; busy: string; description: string }> = {
  APPROVE: {
    title: 'Approve',
    action: 'Approve for test',
    busy: 'Approving…',
    description: 'Approving promotes this version to APPROVED_FOR_TEST: a shared, approved scenario (illustrative) listed in the shared library.',
  },
  REJECT: { title: 'Reject', action: 'Reject', busy: 'Rejecting…', description: 'Rejecting ends this version REJECTED: not runnable, kept, never deleted. Its author may adapt it into a new version.' },
}

/** Maker-checker promotion with its required comment (D-44 §6). The server decides and audits. */
function DecisionDialog({ scenarioId, version, decision, onDone }: { scenarioId: string; version: ScenarioVersion; decision: Decision; onDone: (verb: StepVerb, v: ScenarioVersion) => void }) {
  const [open, setOpen] = useState(false)
  const [comment, setComment] = useState('')
  const [touched, setTouched] = useState(false)
  const [busy, setBusy] = useState(false)
  const [refusal, setRefusal] = useState<Refusal | null>(null)
  const text = DECISION_TEXT[decision]
  const problem = commentProblem(comment)

  const change = (next: boolean) => {
    if (busy) return
    setOpen(next)
    if (!next) {
      setComment('')
      setTouched(false)
      setRefusal(null)
    }
  }

  async function send() {
    const body = decisionBody(decision, comment)
    if (!body || busy) return
    setBusy(true)
    setRefusal(null)
    try {
      const decided = await decideVersion(scenarioId, version.version, body)
      setOpen(false)
      onDone('decided', decided)
    } catch (err) {
      const r = scenarioRefusal(err, `Could not ${decision === 'APPROVE' ? 'approve' : 'reject'} the version`)
      toastApiError(err, r.title)
      setRefusal(r)
    } finally {
      setBusy(false)
    }
  }

  const testId = decision === 'APPROVE' ? 'version-approve' : 'version-reject'
  return (
    <Dialog open={open} onOpenChange={change}>
      <DialogTrigger asChild>
        <Button size="sm" variant={decision === 'APPROVE' ? 'default' : 'outline'} data-testid={testId}>
          {decision === 'APPROVE' ? <Check className="mr-2 h-4 w-4" /> : <X className="mr-2 h-4 w-4" />}
          {text.title}
        </Button>
      </DialogTrigger>
      <DialogContent className="max-w-xl" data-testid="decision-dialog" data-decision={decision}>
        <DialogHeader>
          <DialogTitle>
            {text.title} v{version.version}
          </DialogTitle>
          <DialogDescription>{text.description} The decision is recorded with your name and audited.</DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
          <Label htmlFor={`decision-comment-${version.scenario_version_id}`}>Comment (required)</Label>
          <Textarea
            id={`decision-comment-${version.scenario_version_id}`}
            value={comment}
            maxLength={COMMENT_MAX}
            disabled={busy}
            onChange={(e) => setComment(e.target.value)}
            onBlur={() => setTouched(true)}
            placeholder="What you checked and why"
            data-testid="decision-comment"
          />
          <div className="flex justify-between text-xs">
            <span className="text-destructive" data-testid="decision-comment-problem">
              {touched && problem}
            </span>
            <span className="font-mono text-muted-foreground" data-testid="decision-comment-count">
              {comment.trim().length}/{COMMENT_MAX}
            </span>
          </div>
        </div>
        {refusal && <ScenarioRefusalNotice refusal={refusal} />}
        <DialogFooter>
          <Button variant="outline" onClick={() => change(false)} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={send} disabled={!!problem || busy} variant={decision === 'REJECT' ? 'destructive' : 'default'} data-testid="decision-confirm">
            {busy ? text.busy : text.action}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/**
 * FE-24.2: the lifecycle actions of one version. Edit, "Confirm for exploration" and "Submit for
 * approval" show to the author only; Approve and Reject to an approver who is not the author.
 * Run actions stay hidden until Phase 25. Every gate is cosmetic: the server checks each step
 * (403, 409 `not_author` / `self_approval` / `invalid_status` / `scenarios_disabled`, D-04).
 */
export function VersionActions({
  scenarioId,
  version,
  enabled,
  onDone,
}: {
  scenarioId: string
  version: ScenarioVersion
  enabled: boolean
  onDone: (verb: StepVerb, v: ScenarioVersion) => void
}) {
  const { me, has } = useCurrentSession()
  const gate = decisionGate(version, me, has)
  return (
    <div className="space-y-2" data-testid="version-actions" data-gate={gate}>
      {enabled && (
        <div className="flex flex-wrap gap-2">
          {canEdit(version, me, has) && (
            <Button size="sm" variant="outline" asChild data-testid="version-edit">
              <Link href={editVersionHref(scenarioId, version.version)}>
                <Pencil className="mr-2 h-4 w-4" />
                Edit draft
              </Link>
            </Button>
          )}
          {canConfirm(version, me, has) && <AuthorStep scenarioId={scenarioId} version={version} step="confirm" onDone={onDone} />}
          {canSubmit(version, me, has) && <AuthorStep scenarioId={scenarioId} version={version} step="submit" onDone={onDone} />}
          {gate === 'allowed' && (
            <>
              <DecisionDialog scenarioId={scenarioId} version={version} decision="APPROVE" onDone={onDone} />
              <DecisionDialog scenarioId={scenarioId} version={version} decision="REJECT" onDone={onDone} />
            </>
          )}
          {canAdapt(has) && (
            <Button size="sm" variant="ghost" asChild data-testid="version-adapt">
              <Link href={newVersionHref(scenarioId, version.version)}>
                <CopyPlus className="mr-2 h-4 w-4" />
                New version from v{version.version}
              </Link>
            </Button>
          )}
        </div>
      )}
      {gate === 'self_approval' && (
        <p className="flex items-center gap-1 text-xs text-[hsl(var(--warning))]" data-testid="self-approval-blocked">
          <ShieldOff className="h-3.5 w-3.5" />
          You authored this version, so you cannot approve or reject it: self-approval is not allowed. Another user with scenario:approve decides.
        </p>
      )}
      {gate === 'no_permission' && (
        <p className="text-xs text-muted-foreground" data-testid="awaiting-approver">
          Waiting for an approver (scenario:approve) who is not the author.
        </p>
      )}
    </div>
  )
}
