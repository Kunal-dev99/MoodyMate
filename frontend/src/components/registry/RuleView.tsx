'use client'

import Link from 'next/link'
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { GitBranch, GitCompare, History, PlayCircle, SlidersHorizontal } from 'lucide-react'
import { api, call } from '@/api/client'
import { Forbidden } from '@/components/auth/Forbidden'
import { IllustrativeBanner } from '@/components/calculations/IllustrativeBanner'
import { RunStatusPill } from '@/components/calculations/RunStatusPill'
import { runHref } from '@/components/calculations/calculations'
import { EmptyState } from '@/components/common/EmptyState'
import { LoadFailed } from '@/components/common/LoadFailed'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { PageHeader } from '@/components/common/PageHeader'
import { PageSection } from '@/components/common/PageSection'
import { createLoader, startLoad, type Loaded } from '@/components/common/load'
import { Button } from '@/components/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { cn } from '@/lib/utils'
import { RuleVersionActions } from '@/components/rules/RuleVersionActions'
import { ParameterDiffTable, ParameterSchemaView, ParameterValues } from './ParameterView'
import { LabelBadges, VersionStatusPill } from './RegistryBadges'
import {
  CATEGORY_TEXT,
  defaultDiffPair,
  defaultVersion,
  diffParameters,
  modulesHref,
  newestFirst,
  rulesHref,
  usedByText,
  type RuleDetail,
  type RuleVersion,
} from './registry'

const CRUMBS = [
  { label: 'Financial Domain', href: '/domain' },
  { label: 'Rules', href: rulesHref },
]
const selectClass = 'h-9 rounded-md border border-input bg-background px-2 text-sm'

const fetchRule = (ruleId: string) => (signal: AbortSignal) =>
  call(() => api.GET('/api/v1/rules/{rule_id}', { params: { path: { rule_id: ruleId } }, signal }))

function Field({ label, children, testId }: { label: string; children: ReactNode; testId?: string }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd data-testid={testId}>{children}</dd>
    </>
  )
}

const Mono = ({ children }: { children: ReactNode }) => <span className="break-all font-mono text-xs">{children}</span>
const when = (at: string | null) => (at ? new Date(at).toLocaleString() : '—')

/** Every version of the rule, newest first, with who wrote and decided it and how many runs pin it. */
function VersionHistory({ versions, shown, onShow }: { versions: RuleVersion[]; shown: number | null; onShow: (version: number) => void }) {
  return (
    <PageSection
      icon={History}
      title={`Version history (${versions.length})`}
      description="Every version, newest first. A version is immutable once it leaves DRAFT; superseded and rejected versions are kept, never deleted (D-32 §2)."
    >
      <Table data-testid="rule-versions">
        <TableHeader>
          <TableRow>
            <TableHead>Version</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Author</TableHead>
            <TableHead>Approver</TableHead>
            <TableHead>Change</TableHead>
            <TableHead>Parameters SHA-256</TableHead>
            <TableHead>Runs</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {newestFirst(versions).map((v) => (
            <TableRow
              key={v.rule_version_id}
              className={cn(shown === v.version && 'bg-surface-2')}
              data-testid="rule-version-row"
              data-version={v.version}
              data-status={v.status}
              data-rule-version-id={v.rule_version_id}
            >
              <TableCell>
                <Button
                  variant="link"
                  size="sm"
                  className="h-auto p-0 font-mono"
                  onClick={() => onShow(v.version)}
                  aria-pressed={shown === v.version}
                  data-testid="rule-version-show"
                >
                  v{v.version}
                </Button>
                <LabelBadges illustrative={v.illustrative} provisional={v.provisional} seeded={v.seeded} className="mt-1 block" />
              </TableCell>
              <TableCell>
                <VersionStatusPill status={v.status} testId="rule-version-status" />
              </TableCell>
              <TableCell className="text-xs" data-testid="rule-version-author">
                {v.authored_by}
              </TableCell>
              <TableCell className="text-xs" data-testid="rule-version-approver">
                {v.decided_by ?? v.approved_by ?? <span className="text-muted-foreground">—</span>}
                {(v.decided_at ?? v.approved_at) && <div className="text-muted-foreground">{when(v.decided_at ?? v.approved_at)}</div>}
              </TableCell>
              <TableCell className="max-w-xs text-xs">
                {v.change_summary ?? <span className="text-muted-foreground">—</span>}
                {v.decision_comment && <div className="text-muted-foreground">Decision: {v.decision_comment}</div>}
              </TableCell>
              <TableCell className="font-mono text-xs" title={v.parameters_sha256}>
                {v.parameters_sha256.slice(0, 12)}…
              </TableCell>
              <TableCell className="text-xs" data-testid="rule-version-run-count" data-run-count={v.run_count}>
                {usedByText(v.run_count)}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </PageSection>
  )
}

/** One version: its identity, approval trail, parameters and the runs that pin it. */
function VersionPanel({ version: v }: { version: RuleVersion }) {
  return (
    <div data-testid="rule-version-detail" data-version={v.version} data-status={v.status}>
      <PageSection
        icon={GitBranch}
        title={`Version ${v.version}`}
        description="Parameters are typed by the rule's parameter kind and shown as the server's exact text."
        headerRight={
          <span className="flex items-center gap-2">
            <VersionStatusPill status={v.status} testId="rule-version-detail-status" />
            <LabelBadges illustrative={v.illustrative} provisional={v.provisional} seeded={v.seeded} />
          </span>
        }
      >
        <div className="space-y-6">
          <dl className="grid grid-cols-[180px_1fr] gap-y-1 text-sm">
            <Field label="Rule version ID" testId="rule-version-id">
              <Mono>{v.rule_version_id}</Mono>
            </Field>
            <Field label="Parameters SHA-256" testId="rule-version-sha256">
              <Mono>{v.parameters_sha256}</Mono>
            </Field>
            <Field label="Authored by">
              <span className="text-xs">{v.authored_by}</span> <span className="text-xs text-muted-foreground">{when(v.created_at)}</span>
            </Field>
            <Field label="Approved by">
              <span className="text-xs">{v.approved_by ?? '—'}</span> <span className="text-xs text-muted-foreground">{v.approved_at ? when(v.approved_at) : ''}</span>
            </Field>
            {v.decided_by && (
              <Field label="Decided by">
                <span className="text-xs">{v.decided_by}</span> <span className="text-xs text-muted-foreground">{when(v.decided_at)}</span>
              </Field>
            )}
            {v.decision_comment && <Field label="Decision comment">{v.decision_comment}</Field>}
            {v.change_summary && <Field label="Change summary">{v.change_summary}</Field>}
            <Field label="Effective">
              <Mono>
                {v.effective_from ?? '—'} → {v.effective_to ?? 'open'}
              </Mono>
            </Field>
            {v.superseded_at && <Field label="Superseded">{when(v.superseded_at)}</Field>}
          </dl>
          <div>
            <h3 className="mb-2 text-sm font-medium">Parameters</h3>
            <ParameterValues parameters={v.parameters} />
          </div>
          <div>
            <h3 className="mb-2 text-sm font-medium" data-testid="rule-version-used-by">
              {usedByText(v.run_count)}
            </h3>
            {v.runs.length === 0 ? (
              <p className="text-sm text-muted-foreground">No calculation run pins this version.</p>
            ) : (
              <>
                <Table data-testid="rule-version-runs">
                  <TableHeader>
                    <TableRow>
                      <TableHead>Run</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead>Reporting date</TableHead>
                      <TableHead>Started</TableHead>
                      <TableHead>Re-run of</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {v.runs.map((r) => (
                      <TableRow key={r.run_id} data-testid="pinning-run" data-run-id={r.run_id}>
                        <TableCell>
                          <Link href={runHref(r.run_id)} className="font-mono text-xs text-primary hover:underline" data-testid="pinning-run-link">
                            {r.run_id}
                          </Link>
                        </TableCell>
                        <TableCell>
                          <RunStatusPill status={r.status} testId="pinning-run-status" />
                        </TableCell>
                        <TableCell className="font-mono text-sm">{r.reporting_date}</TableCell>
                        <TableCell className="text-xs">{when(r.started_at)}</TableCell>
                        <TableCell>
                          {r.rerun_of_run_id ? (
                            <Link href={runHref(r.rerun_of_run_id)} className="font-mono text-xs text-primary hover:underline">
                              {r.rerun_of_run_id.slice(0, 8)}…
                            </Link>
                          ) : (
                            <span className="text-muted-foreground">—</span>
                          )}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
                {v.run_count > v.runs.length && (
                  <p className="mt-2 text-xs text-muted-foreground" data-testid="pinning-runs-truncated">
                    The newest {v.runs.length} of {v.run_count} runs are listed.
                  </p>
                )}
              </>
            )}
          </div>
        </div>
      </PageSection>
    </div>
  )
}

/** Two versions side by side: the parameter leaves that differ. */
function VersionDiff({ versions }: { versions: RuleVersion[] }) {
  const initial = defaultDiffPair(versions)
  const [pair, setPair] = useState<{ from: number; to: number } | null>(initial)
  const byNumber = (n: number | undefined) => versions.find((v) => v.version === n)
  const from = byNumber(pair?.from)
  const to = byNumber(pair?.to)
  const ordered = newestFirst(versions)

  let body: ReactNode
  if (versions.length < 2)
    body = (
      <p className="text-sm text-muted-foreground" data-testid="parameter-diff-single">
        Only one version exists: there is nothing to compare yet.
      </p>
    )
  else
    body = (
      <div className="space-y-3">
        <div className="flex flex-wrap items-end gap-2">
          {(['from', 'to'] as const).map((side) => (
            <label key={side} className="flex flex-col gap-1 text-xs text-muted-foreground">
              {side === 'from' ? 'From version' : 'To version'}
              <select
                aria-label={side === 'from' ? 'From version' : 'To version'}
                className={selectClass}
                value={pair?.[side] ?? ''}
                onChange={(e) => {
                  const picked = ordered.find((v) => String(v.version) === e.target.value)
                  if (picked) setPair({ from: pair?.from ?? picked.version, to: pair?.to ?? picked.version, [side]: picked.version })
                }}
                data-testid={`diff-${side}`}
              >
                {ordered.map((v) => (
                  <option key={v.rule_version_id} value={v.version}>
                    v{v.version} · {v.status}
                  </option>
                ))}
              </select>
            </label>
          ))}
        </div>
        {from && to ? <ParameterDiffTable changes={diffParameters(from.parameters, to.parameters)} /> : null}
      </div>
    )

  return (
    <PageSection icon={GitCompare} title="Parameter diff" description="The parameter values that differ between two versions, as the server stores them.">
      {body}
    </PageSection>
  )
}

/**
 * FE-16.1: one rule (GET /rules/{rule_id}): its definition and parameter kind, the D-35
 * illustrative label, its version history with authors, approvers and "used by N runs", one
 * version's parameters and pinning runs, and a parameter diff between two versions. Read-only:
 * FE-16.2's propose → submit → approve actions mount in the marked spot.
 */
export function RuleView({ ruleId }: { ruleId: string }) {
  const [state, setState] = useState<Loaded<RuleDetail>>({ kind: 'loading' })
  const [shown, setShown] = useState<number | null>(null)
  const loader = useMemo(() => createLoader(fetchRule(ruleId), setState, 'Could not load the rule'), [ruleId])
  const refreshing = useRef<AbortController | null>(null)

  useEffect(() => {
    loader.load()
    return () => {
      loader.abort()
      refreshing.current?.abort()
    }
  }, [loader])

  /** Reloads the rule in place (no loading state), e.g. after a version is proposed or decided. */
  const refresh = useCallback(() => {
    refreshing.current = startLoad(
      fetchRule(ruleId),
      { onReady: (value) => setState({ kind: 'ready', value }), onFailure: setState, failureTitle: 'Could not reload the rule' },
      refreshing.current,
    )
  }, [ruleId])

  const wrap = (body: ReactNode) => (
    <>
      <PageHeader title="Rule" breadcrumbs={[...CRUMBS, { label: ruleId.slice(0, 8) }]} />
      <div className="px-6 pb-6">{body}</div>
    </>
  )

  if (state.kind === 'loading') return wrap(<LoadingBlock rows={6} />)
  if (state.kind === 'forbidden') return wrap(<Forbidden permission="rule:read" correlationId={state.cid} />)
  if (state.kind === 'failed') return wrap(<LoadFailed icon={SlidersHorizontal} title="The rule could not be loaded" cid={state.cid} onRetry={loader.load} />)
  if (state.kind === 'not_found')
    return wrap(
      <EmptyState
        icon={SlidersHorizontal}
        title="Rule not found"
        description={`It does not exist or is not visible to you.${state.cid ? ` Correlation ID: ${state.cid}` : ''}`}
      />,
    )

  const detail = state.value
  const shownVersion = detail.versions.find((v) => v.version === shown) ?? defaultVersion(detail.versions)
  return (
    <>
      <PageHeader
        title={detail.name}
        description={`${detail.code} · ${CATEGORY_TEXT[detail.category]} · ${detail.version_count} version${detail.version_count === 1 ? '' : 's'}${
          detail.active_version === null ? ' · no ACTIVE version' : ` · v${detail.active_version} ACTIVE`
        }`}
        breadcrumbs={[...CRUMBS, { label: detail.code }]}
        actions={
          <span data-testid="rule-labels">
            <LabelBadges illustrative={detail.illustrative} provisional={detail.provisional} />
          </span>
        }
      />
      <div className="space-y-4 px-6 pb-6" data-testid="rule-detail" data-code={detail.code} data-rule-id={detail.rule_id}>
        {detail.illustrative && <IllustrativeBanner statement={detail.statement} />}
        <PageSection icon={SlidersHorizontal} title="Definition" description="A rule is data: one closed parameter kind, no expressions or code (D-32 §1).">
          <div className="grid gap-6 lg:grid-cols-2">
            <dl className="grid grid-cols-[160px_1fr] gap-y-1 text-sm" data-testid="rule-definition">
              <Field label="Code" testId="rule-code">
                <span className="font-mono">{detail.code}</span>
              </Field>
              <Field label="Category">{CATEGORY_TEXT[detail.category]}</Field>
              <Field label="Owner">{detail.owner}</Field>
              <Field label="Module" testId="rule-module">
                <Link href={modulesHref} className="font-mono text-primary hover:underline">
                  {detail.target_module.code}
                </Link>{' '}
                <span className="text-muted-foreground">{detail.target_module.name}</span>
              </Field>
              <Field label="Active version" testId="rule-active">
                {detail.active_version === null ? (
                  <span className="text-destructive">None: a module reading this rule cannot run</span>
                ) : (
                  <span className="font-mono">v{detail.active_version}</span>
                )}
              </Field>
              <Field label="Rule ID">
                <Mono>{detail.rule_id}</Mono>
              </Field>
              {detail.description && <Field label="Description">{detail.description}</Field>}
            </dl>
            <ParameterSchemaView schema={detail.parameter_schema} vocabulary={detail.vocabulary} />
          </div>
        </PageSection>

        {/* FE-16.2 (src/components/rules/): propose → submit → approve, and the RULE_CHANGE_ENABLED=off notice. `refresh` reloads this rule in place. */}
        <div data-testid="rule-version-actions-slot">
          <RuleVersionActions ruleId={detail.rule_id} onChanged={refresh} />
        </div>

        <VersionHistory versions={detail.versions} shown={shownVersion?.version ?? null} onShow={setShown} />
        {shownVersion ? (
          <VersionPanel key={shownVersion.rule_version_id} version={shownVersion} />
        ) : (
          <EmptyState icon={PlayCircle} title="This rule has no version" />
        )}
        <VersionDiff key={detail.versions.map((v) => `${v.version}:${v.status}`).join(',')} versions={detail.versions} />
      </div>
    </>
  )
}
