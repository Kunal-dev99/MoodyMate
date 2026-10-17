'use client'

import Link from 'next/link'
import { useEffect, useMemo, useState } from 'react'
import { FilePlus2, SlidersHorizontal } from 'lucide-react'
import { useCurrentSession } from '@/components/auth/SessionProvider'
import { Forbidden } from '@/components/auth/Forbidden'
import { toastApiError } from '@/components/common/api-error-toast'
import { EmptyState } from '@/components/common/EmptyState'
import { LoadFailed } from '@/components/common/LoadFailed'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { PageHeader } from '@/components/common/PageHeader'
import { PageSection } from '@/components/common/PageSection'
import { createLoader, type Loaded } from '@/components/common/load'
import type { Refusal } from '@/components/ingestion/mappings'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { ParameterEditor } from './ParameterEditor'
import { RuleRefusalNotice, RuleVersionActions } from './RuleVersionActions'
import { RuleVersionStatusPill } from './RuleVersionStatusPill'
import {
  COMMENT_MAX,
  activeVersionOf,
  canPropose,
  editorFor,
  fetchRule,
  proposalBody,
  proposalProblems,
  proposeVersion,
  ruleHref,
  ruleRefusal,
  type Editor,
  type RuleDetail,
  type RuleVersion,
} from './rules'

const crumbs = (rule?: RuleDetail) => [
  { label: 'Financial Domain', href: '/domain' },
  { label: 'Rules', href: '/domain/rules' },
  ...(rule ? [{ label: rule.code, href: ruleHref(rule.rule_id) }] : []),
  { label: 'Propose' },
]

function ProposeForm({ rule, active, onProposed }: { rule: RuleDetail; active: RuleVersion; onProposed: () => void }) {
  const [editor, setEditor] = useState<Editor>(() => editorFor(rule, active.parameters))
  const [summary, setSummary] = useState('')
  const [busy, setBusy] = useState(false)
  const [proposed, setProposed] = useState<RuleVersion | null>(null)
  const [refusal, setRefusal] = useState<Refusal | null>(null)
  const problems = proposalProblems(editor, active.parameters, summary)
  const body = proposalBody(editor, active.parameters, summary)

  const edit = (e: Editor) => {
    setEditor(e)
    setProposed(null)
  }

  async function propose() {
    if (!body || busy || proposed) return
    setBusy(true)
    setRefusal(null)
    try {
      setProposed(await proposeVersion(rule.rule_id, body))
      onProposed()
    } catch (err) {
      const r = ruleRefusal(err, 'Could not propose the version')
      toastApiError(err, r.title)
      setRefusal(r)
    } finally {
      setBusy(false)
    }
  }

  return (
    <PageSection
      icon={SlidersHorizontal}
      title={`New version of ${rule.code}`}
      description={`A DRAFT copied from ACTIVE v${active.version}, with your changes. Only the changed parameters are sent (a merge patch); the server checks them against the rule's kind and records you as the author.`}
      headerRight={
        <span className="flex gap-2">
          <Badge variant="outline" className="font-mono" data-testid="parameter-kind">
            {editor.kind}
          </Badge>
          {active.illustrative && <Badge variant="warning">Illustrative</Badge>}
          {active.provisional && <Badge variant="warning">Provisional</Badge>}
        </span>
      }
    >
      <div className="space-y-4" data-testid="rule-propose" data-rule-id={rule.rule_id} data-base-version={active.version}>
        <p className="text-xs text-muted-foreground">
          Based on ACTIVE v{active.version} · parameters SHA-256 <span className="break-all font-mono">{active.parameters_sha256}</span>
        </p>
        <ParameterEditor editor={editor} onChange={edit} disabled={busy} />
        <div className="max-w-2xl space-y-2">
          <Label htmlFor="change-summary">Change summary (required)</Label>
          <Textarea
            id="change-summary"
            value={summary}
            maxLength={COMMENT_MAX}
            disabled={busy}
            onChange={(e) => {
              setSummary(e.target.value)
              setProposed(null)
            }}
            placeholder="What changes and why"
            data-testid="change-summary"
          />
        </div>
        {problems.length > 0 && (
          <ul className="list-disc space-y-0.5 pl-5 text-xs text-muted-foreground" data-testid="parameter-problems">
            {problems.map((p) => (
              <li key={p} data-testid="parameter-problem">
                {p}
              </li>
            ))}
          </ul>
        )}
        <p className="text-xs text-muted-foreground">These checks are a convenience only: the server decides (422 invalid_parameters).</p>
        <div className="flex flex-wrap items-center gap-3">
          <Button onClick={propose} disabled={!body || busy || !!proposed} data-testid="propose-submit">
            <FilePlus2 className="mr-2 h-4 w-4" />
            {busy ? 'Proposing…' : 'Propose draft'}
          </Button>
          {proposed && (
            <span className="flex items-center gap-2 text-sm" data-testid="propose-result" data-version={proposed.version} data-status={proposed.status}>
              The server created v{proposed.version} as <RuleVersionStatusPill status={proposed.status} testId="propose-result-status" />. Submit it for
              review below.
            </span>
          )}
        </div>
        {refusal && <RuleRefusalNotice refusal={refusal} />}
      </div>
    </PageSection>
  )
}

/** FE-16.2: propose a new version of one rule, then submit it (the open versions below). */
export function ProposeRuleVersion({ ruleId }: { ruleId: string }) {
  const { has } = useCurrentSession()
  const [state, setState] = useState<Loaded<RuleDetail>>({ kind: 'loading' })
  // Bumped after a proposal so the open versions (and their Submit button) reload.
  const [generation, setGeneration] = useState(0)
  const loader = useMemo(() => createLoader(fetchRule(ruleId), setState, 'Could not load the rule'), [ruleId])
  useEffect(() => {
    loader.load()
    return loader.abort
  }, [loader])

  const rule = state.kind === 'ready' ? state.value : undefined
  const header = <PageHeader title={rule ? `Propose a change: ${rule.name}` : 'Propose a rule change'} breadcrumbs={crumbs(rule)} />

  let body
  if (state.kind === 'loading') body = <LoadingBlock rows={4} />
  else if (state.kind === 'forbidden') body = <Forbidden permission="rule:read" />
  else if (state.kind === 'not_found') body = <EmptyState icon={SlidersHorizontal} title="Rule not found" description="It does not exist in your tenant." />
  else if (state.kind === 'failed') body = <LoadFailed icon={SlidersHorizontal} title="Could not load the rule" cid={state.cid} onRetry={loader.load} />
  else {
    const r = state.value
    const active = activeVersionOf(r)
    body = (
      <div className="space-y-6">
        {!active || !canPropose(r, has) ? (
          <EmptyState
            icon={SlidersHorizontal}
            title="This rule cannot be changed here now"
            description={
              !r.change_enabled
                ? 'Rule changes are switched off: the rule stays at its ACTIVE version.'
                : !active
                  ? 'The rule has no ACTIVE version to copy.'
                  : 'Proposing a version needs rule:propose.'
            }
            action={
              <Link href={ruleHref(r.rule_id)} className="text-sm text-primary hover:underline">
                Back to the rule
              </Link>
            }
          />
        ) : (
          <ProposeForm rule={r} active={active} onProposed={() => setGeneration((g) => g + 1)} />
        )}
        <RuleVersionActions key={generation} ruleId={r.rule_id} />
      </div>
    )
  }
  return (
    <>
      {header}
      <div className="px-6 pb-6">{body}</div>
    </>
  )
}
