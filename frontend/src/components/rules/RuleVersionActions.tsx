'use client'

import Link from 'next/link'
import { useEffect, useMemo, useState } from 'react'
import { Check, FilePlus2, GitPullRequestArrow, Send, ShieldAlert, ShieldOff, X } from 'lucide-react'
import { useCurrentSession } from '@/components/auth/SessionProvider'
import { toastApiError } from '@/components/common/api-error-toast'
import { LoadFailed } from '@/components/common/LoadFailed'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { PageSection } from '@/components/common/PageSection'
import { createLoader, type Loaded } from '@/components/common/load'
import type { Refusal } from '@/components/ingestion/mappings'
import { Alert } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { RuleVersionStatusPill } from './RuleVersionStatusPill'
import {
  COMMENT_MAX,
  canPropose,
  canSubmit,
  commentProblem,
  decideVersion,
  decisionGate,
  fetchRule,
  isAuthor,
  openVersions,
  proposeHref,
  ruleRefusal,
  submitVersion,
  type Decision,
  type RuleDetail,
  type RuleVersion,
} from './rules'

/** A refused rule request, inline: the server's envelope as it came, with its correlation ID. */
export function RuleRefusalNotice({ refusal }: { refusal: Refusal }) {
  return (
    <Alert variant="destructive" data-testid="rule-refusal" data-code={refusal.code}>
      <ShieldAlert className="h-4 w-4" />
      <div className="space-y-1 text-sm">
        <div className="font-medium">{refusal.title}</div>
        <div className="text-foreground">{refusal.message}</div>
        <div className="font-mono text-xs text-muted-foreground">
          {refusal.status !== null && <>HTTP {refusal.status} · </>}
          <span data-testid="rule-refusal-code">{refusal.code}</span>
          {refusal.cid && (
            <>
              {' '}
              · Correlation ID: <span data-testid="rule-refusal-cid">{refusal.cid}</span>
            </>
          )}
        </div>
      </div>
    </Alert>
  )
}

const DECISION_TEXT: Record<Decision, { title: string; action: string; busy: string; description: string }> = {
  APPROVE: {
    title: 'Approve',
    action: 'Approve and activate',
    busy: 'Approving…',
    description:
      'Approving makes this version ACTIVE and the current ACTIVE version SUPERSEDED, in one step. Runs submitted afterwards pin it; earlier runs keep their pins.',
  },
  REJECT: { title: 'Reject', action: 'Reject', busy: 'Rejecting…', description: 'Rejecting ends this version REJECTED. No run ever uses it.' },
}

/** The maker-checker decision with its required comment (FE-16.2). The server decides and audits it. */
function DecisionDialog({ ruleId, version, decision, onDecided }: { ruleId: string; version: RuleVersion; decision: Decision; onDecided: (v: RuleVersion) => void }) {
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

  async function confirm() {
    if (problem || busy) return
    setBusy(true)
    setRefusal(null)
    try {
      const decided = await decideVersion(ruleId, version.version, { decision, comment: comment.trim() })
      setOpen(false)
      setComment('')
      onDecided(decided)
    } catch (err) {
      const r = ruleRefusal(err, `Could not ${decision === 'APPROVE' ? 'approve' : 'reject'} the version`)
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
      <DialogContent data-testid="decision-dialog" data-decision={decision} className="max-w-xl">
        <DialogHeader>
          <DialogTitle>
            {text.title} {version.rule_code} v{version.version}
          </DialogTitle>
          <DialogDescription>{text.description} The decision is recorded with your name and audited.</DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
          <Label htmlFor={`decision-comment-${version.rule_version_id}`}>Comment (required)</Label>
          <Textarea
            id={`decision-comment-${version.rule_version_id}`}
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
            <span className="font-mono text-muted-foreground">
              {comment.trim().length}/{COMMENT_MAX}
            </span>
          </div>
        </div>
        {refusal && <RuleRefusalNotice refusal={refusal} />}
        <DialogFooter>
          <Button variant="outline" onClick={() => change(false)} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={confirm} disabled={!!problem || busy} variant={decision === 'REJECT' ? 'destructive' : 'default'} data-testid="decision-confirm">
            {busy ? text.busy : text.action}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

type Outcome = { verb: 'submitted' | 'decided'; version: RuleVersion }

function OpenVersion({ rule, version, onChanged }: { rule: RuleDetail; version: RuleVersion; onChanged: (o: Outcome) => void }) {
  const { me, has } = useCurrentSession()
  const [busy, setBusy] = useState(false)
  const [refusal, setRefusal] = useState<Refusal | null>(null)
  const gate = decisionGate(rule, version, me, has)
  const mine = isAuthor(version, me)

  async function submit() {
    setBusy(true)
    setRefusal(null)
    try {
      onChanged({ verb: 'submitted', version: await submitVersion(rule.rule_id, version.version) })
    } catch (err) {
      const r = ruleRefusal(err, 'Could not submit the version')
      toastApiError(err, r.title)
      setRefusal(r)
    } finally {
      setBusy(false)
    }
  }

  return (
    <li
      className="space-y-2 rounded-md border border-border px-3 py-2"
      data-testid="open-version"
      data-version={version.version}
      data-status={version.status}
      data-mine={mine ? 'true' : 'false'}
    >
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="font-mono font-medium">v{version.version}</span>
        <RuleVersionStatusPill status={version.status} />
        <span className="text-xs text-muted-foreground">
          proposed by <span className="font-mono">{version.authored_by}</span>
          {mine && ' (you)'} · {new Date(version.created_at).toLocaleString()}
        </span>
        <span className="ml-auto flex flex-wrap gap-2">
          {canSubmit(rule, version, has) && (
            <Button size="sm" onClick={submit} disabled={busy} data-testid="version-submit">
              <Send className="mr-2 h-4 w-4" />
              {busy ? 'Submitting…' : 'Submit for review'}
            </Button>
          )}
          {gate === 'allowed' && (
            <>
              <DecisionDialog ruleId={rule.rule_id} version={version} decision="APPROVE" onDecided={(v) => onChanged({ verb: 'decided', version: v })} />
              <DecisionDialog ruleId={rule.rule_id} version={version} decision="REJECT" onDecided={(v) => onChanged({ verb: 'decided', version: v })} />
            </>
          )}
        </span>
      </div>
      {version.change_summary && (
        <p className="text-sm" data-testid="open-version-summary">
          {version.change_summary}
        </p>
      )}
      {gate === 'self_approval' && (
        <p className="flex items-center gap-1 text-xs text-[hsl(var(--warning))]" data-testid="self-approval-blocked">
          <ShieldOff className="h-3.5 w-3.5" />
          You proposed this version, so you cannot approve or reject it: self-approval is not allowed. Another user with rule:approve decides.
        </p>
      )}
      {gate === 'no_permission' && (
        <p className="text-xs text-muted-foreground" data-testid="awaiting-approver">
          Waiting for an approver (rule:approve) who is not the author.
        </p>
      )}
      {refusal && <RuleRefusalNotice refusal={refusal} />}
    </li>
  )
}

/**
 * FE-16.2: the maker-checker actions on one rule, for its detail page. Proposing a new version
 * (a DRAFT copied from the ACTIVE one, on the propose page), submitting a DRAFT, and approving
 * or rejecting an IN_REVIEW version with a required comment. The Approve button never shows to
 * the version's author (self-approval is not allowed; the server answers 409 `self_approval`)
 * and is cosmetic: the server checks rule:propose and rule:approve on every request (D-04).
 * `onChanged` runs after the server accepted a step, so the page can reload its own view.
 */
export function RuleVersionActions({ ruleId, onChanged }: { ruleId: string; onChanged?: () => void }) {
  const { has } = useCurrentSession()
  const [state, setState] = useState<Loaded<RuleDetail>>({ kind: 'loading' })
  const [outcome, setOutcome] = useState<Outcome | null>(null)
  const loader = useMemo(() => createLoader(fetchRule(ruleId), setState, 'Could not load the rule'), [ruleId])
  useEffect(() => {
    loader.load()
    return loader.abort
  }, [loader])

  const changed = (o: Outcome) => {
    setOutcome(o)
    loader.load()
    onChanged?.()
  }

  if (state.kind === 'loading') return <LoadingBlock rows={2} />
  if (state.kind === 'failed') return <LoadFailed icon={GitPullRequestArrow} title="Could not load the rule's open versions" cid={state.cid} onRetry={loader.load} />
  if (state.kind !== 'ready') return null // 403 / 404: the page itself shows the refusal

  const rule = state.value
  const open = openVersions(rule)
  return (
    <PageSection
      icon={GitPullRequestArrow}
      title="Change this rule"
      description="Maker-checker (D-32 §3): an analyst proposes a version from the ACTIVE one and submits it; an approver who is not the author activates or rejects it. Every step is audited."
      headerRight={
        canPropose(rule, has) ? (
          <Button size="sm" variant="outline" asChild data-testid="propose-link">
            <Link href={proposeHref(rule.rule_id)}>
              <FilePlus2 className="mr-2 h-4 w-4" />
              Propose a new version
            </Link>
          </Button>
        ) : undefined
      }
    >
      <div className="space-y-3" data-testid="rule-version-actions" data-rule-id={rule.rule_id}>
        {!rule.change_enabled && (
          <p className="text-sm text-muted-foreground" data-testid="rule-change-disabled">
            Rule changes are switched off: the rule stays at its ACTIVE version and the server refuses proposals and decisions (409
            rule_change_disabled). Runs keep working.
          </p>
        )}
        {outcome && (
          <p className="text-sm" data-testid="rule-step-result" data-version={outcome.version.version} data-status={outcome.version.status}>
            The server {outcome.verb === 'submitted' ? 'accepted the submission of' : 'recorded the decision on'} v{outcome.version.version}: it is now{' '}
            <RuleVersionStatusPill status={outcome.version.status} testId="rule-step-status" />.
          </p>
        )}
        {open.length === 0 ? (
          <p className="text-sm text-muted-foreground" data-testid="no-open-versions">
            No version is waiting: nothing is DRAFT or IN_REVIEW.
          </p>
        ) : (
          <ul className="space-y-2">
            {open.map((v) => (
              <OpenVersion key={v.rule_version_id} rule={rule} version={v} onChanged={changed} />
            ))}
          </ul>
        )}
      </div>
    </PageSection>
  )
}
