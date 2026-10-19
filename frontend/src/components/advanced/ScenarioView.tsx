'use client'

import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { Bot, FileWarning, GitBranch, History } from 'lucide-react'
import { Forbidden } from '@/components/auth/Forbidden'
import { EmptyState } from '@/components/common/EmptyState'
import { LoadFailed } from '@/components/common/LoadFailed'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { PageHeader } from '@/components/common/PageHeader'
import { PageSection } from '@/components/common/PageSection'
import { createLoader, type Loaded } from '@/components/common/load'
import { Badge } from '@/components/ui/badge'
import { DisabledNotice } from './ScenarioEditor'
import { ScenarioStatusPill } from './ScenarioStatusPill'
import { VersionActions, type StepVerb } from './VersionActions'
import { MaskedValue, isMasked } from '@/components/common/MaskedValue'
import { describeSelector, describeShock, explorerHref, fetchScenario, type ScenarioDetail, type ScenarioVersion } from './scenarios'

const CRUMBS = [
  { label: 'Advanced' },
  { label: 'Agentic Stress-Test Explorer', href: explorerHref },
]

const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : null)

const STEP_TEXT: Record<StepVerb, string> = {
  confirmed: 'accepted the confirmation of',
  submitted: 'accepted the submission of',
  decided: 'recorded the decision on',
}

/**
 * FE-24.2: one scenario and every version: status, approval label, origin, author, decider,
 * parent version, shocks and their sha256, and the lifecycle actions. CLOSED and REJECTED
 * versions are listed too (hidden by status elsewhere, never deleted). What the page shows is
 * what the server returned; after a step it reloads instead of moving a status itself.
 */
export function ScenarioView({ scenarioId }: { scenarioId: string }) {
  const [state, setState] = useState<Loaded<ScenarioDetail>>({ kind: 'loading' })
  const [outcome, setOutcome] = useState<{ verb: StepVerb; version: ScenarioVersion } | null>(null)
  const loader = useMemo(() => createLoader(fetchScenario(scenarioId), setState, 'Could not load the scenario'), [scenarioId])
  useEffect(() => {
    loader.load()
    return loader.abort
  }, [loader])

  if (state.kind !== 'ready') {
    return (
      <>
        <PageHeader title="Scenario" breadcrumbs={CRUMBS} />
        <div className="space-y-4 px-6 pb-6">
          {state.kind === 'loading' && <LoadingBlock rows={4} />}
          {state.kind === 'failed' && <LoadFailed icon={History} title="Could not load the scenario" cid={state.cid} onRetry={loader.load} />}
          {state.kind === 'forbidden' && <Forbidden permission="calc:read" correlationId={state.cid} />}
          {state.kind === 'not_found' && (
            <EmptyState icon={FileWarning} title="Scenario not found" description={`It does not exist or is not visible to you.${state.cid ? ` Correlation ID: ${state.cid}` : ''}`} />
          )}
        </div>
      </>
    )
  }

  const s = state.value
  const versions = [...s.versions].sort((a, b) => b.version - a.version)
  const done = (verb: StepVerb, version: ScenarioVersion) => {
    setOutcome({ verb, version })
    loader.load()
  }
  return (
    <>
      <PageHeader title={s.name} description={s.description ?? undefined} breadcrumbs={[...CRUMBS, { label: s.name }]} />
      <div className="space-y-4 px-6 pb-6" data-testid="scenario-view" data-id={s.scenario_id}>
        <p className="text-sm text-muted-foreground">
          Code <span className="font-mono text-foreground" data-testid="scenario-code">{s.code}</span> · created by <span className="font-mono">{s.created_by}</span> · {when(s.created_at)}
        </p>
        {!s.scenarios_enabled && <DisabledNotice />}
        {outcome && (
          <p className="text-sm" data-testid="scenario-step-result" data-version={outcome.version.version} data-status={outcome.version.status}>
            The server {STEP_TEXT[outcome.verb]} v{outcome.version.version}: it is now <ScenarioStatusPill status={outcome.version.status} testId="scenario-step-status" />.
          </p>
        )}
        <PageSection icon={History} title="Versions" description="Newest first. A version's shocks freeze when it leaves DRAFT. Runs arrive in a later phase.">
          <ul className="space-y-3">
            {versions.map((v) => (
              <VersionCard key={v.scenario_version_id} scenarioId={s.scenario_id} version={v} enabled={s.scenarios_enabled} onDone={done} />
            ))}
          </ul>
        </PageSection>
      </div>
    </>
  )
}

function VersionCard({ scenarioId, version: v, enabled, onDone }: { scenarioId: string; version: ScenarioVersion; enabled: boolean; onDone: (verb: StepVerb, v: ScenarioVersion) => void }) {
  return (
    <li className="space-y-3 rounded-md border border-border px-4 py-3" data-testid="scenario-version" data-version={v.version} data-status={v.status}>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="font-mono font-medium">v{v.version}</span>
        <ScenarioStatusPill status={v.status} testId="version-status" />
        {v.approval_label && (
          <Badge variant={v.approval_label === 'Exploration, not approved' ? 'warning' : 'success'} data-testid="approval-label">
            {v.approval_label}
          </Badge>
        )}
        {v.origin === 'AI_PROPOSED' ? (
          <Badge variant="info" data-testid="version-origin" data-origin={v.origin}>
            <Bot className="mr-1 h-3 w-3" />
            AI-proposed · {v.ai_acknowledged ? 'reviewed by the author' : 'awaiting the author’s review'}
          </Badge>
        ) : (
          <Badge variant="outline" data-testid="version-origin" data-origin={v.origin}>
            Manual
          </Badge>
        )}
        {v.parent_version !== null && (
          <span className="flex items-center gap-1 text-xs text-muted-foreground" data-testid="version-parent">
            <GitBranch className="h-3.5 w-3.5" />
            adapted from v{v.parent_version}
          </span>
        )}
      </div>

      <dl className="grid gap-x-6 gap-y-1 text-xs md:grid-cols-2">
        <Meta label="Author" testId="version-author">
          <span className="font-mono">{v.authored_by}</span> · {when(v.created_at)}
        </Meta>
        {v.confirmed_at && <Meta label="Confirmed for exploration">{when(v.confirmed_at)}</Meta>}
        {v.submitted_by && (
          <Meta label="Submitted" testId="version-submitter">
            <span className="font-mono">{v.submitted_by}</span> · {when(v.submitted_at)}
            {v.submit_comment && <> · “{v.submit_comment}”</>}
          </Meta>
        )}
        {v.decided_by && (
          <Meta label="Decided" testId="version-decider">
            <span className="font-mono">{v.decided_by}</span> · {when(v.decided_at)}
            {v.decision_comment && <> · “{v.decision_comment}”</>}
          </Meta>
        )}
        {v.closed_by && (
          <Meta label="Closed">
            <span className="font-mono">{v.closed_by}</span> · {when(v.closed_at)}
            {v.close_comment && <> · “{v.close_comment}”</>}
          </Meta>
        )}
        {v.change_summary && <Meta label="Change summary">{v.change_summary}</Meta>}
        <Meta label="Shocks sha256">
          <span className="font-mono" title={v.shocks_sha256}>
            {v.shocks_sha256.slice(0, 16)}…
          </span>
        </Meta>
      </dl>

      <ol className="space-y-1 text-sm" data-testid="version-shocks">
        {v.shocks.map((shock, i) => {
          const selector = describeSelector(shock)
          return (
            <li key={i} className="rounded-sm bg-surface-2 px-2 py-1" data-testid="version-shock" data-template={shock.template}>
              <span className="mr-2 font-mono text-xs text-muted-foreground">#{i + 1}</span>
              {describeShock(shock)}
              {selector && <span className="text-xs text-muted-foreground"> · {selector}</span>}
              {shock.template === 'FUNDING_UNAVAILABLE' && shock.selector.instrument_keys && shock.selector.instrument_keys.length > 0 && (
                <span className="text-xs text-muted-foreground" data-testid="version-shock-instruments">
                  {' · instruments '}
                  {shock.selector.instrument_keys.map((k, j) => (
                    <span key={j} className="mr-1 font-mono">
                      {isMasked(k) ? <MaskedValue value={k} /> : k}
                    </span>
                  ))}
                </span>
              )}
            </li>
          )
        })}
      </ol>

      {v.parameter_sources && Object.keys(v.parameter_sources).length > 0 && (
        <div className="text-xs" data-testid="parameter-sources">
          <span className="font-medium">AI parameter sources: </span>
          {Object.entries(v.parameter_sources)
            .sort(([a], [b]) => a.localeCompare(b))
            .map(([path, source]) => (
              <span key={path} className="mr-2 font-mono">
                {path}={source}
                {source === 'PROPOSED_DEFAULT' && v.acknowledged_defaults.includes(path) && ' (acknowledged)'}
              </span>
            ))}
        </div>
      )}

      <VersionActions scenarioId={scenarioId} version={v} enabled={enabled} onDone={onDone} />
    </li>
  )
}

function Meta({ label, children, testId }: { label: string; children: ReactNode; testId?: string }) {
  return (
    <div className="flex gap-2" data-testid={testId}>
      <dt className="text-muted-foreground">{label}</dt>
      <dd>{children}</dd>
    </div>
  )
}
