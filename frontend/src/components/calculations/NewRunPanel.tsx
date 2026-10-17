'use client'

import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useEffect, useMemo, useReducer, useState, type ReactNode } from 'react'
import { Boxes, CalendarDays, Database, ListChecks, Play, Puzzle } from 'lucide-react'
import { Forbidden } from '@/components/auth/Forbidden'
import { EmptyState } from '@/components/common/EmptyState'
import { LoadFailed } from '@/components/common/LoadFailed'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { PageSection } from '@/components/common/PageSection'
import { createLoader, type Loaded } from '@/components/common/load'
import { DOMAIN_SCHEMA_CODES, importHref } from '@/components/domain/domain'
import { RefusalNotice } from '@/components/ingestion/RefusalNotice'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { cn } from '@/lib/utils'
import { ModuleOption } from './ModuleOption'
import { RunStatusPill } from './RunStatusPill'
import { SnapshotMembers } from './SnapshotMembers'
import {
  CANDIDATES_PER_SCHEMA,
  INITIAL_PANEL,
  INPUT_REF_TEXT,
  buildSnapshot,
  fetchCandidates,
  fetchModules,
  isSelectable,
  missingSchemas,
  panelReducer,
  pinsPreview,
  runHref,
  snapshotProblems,
  submitProblems,
  submitRun,
  type Candidates,
  type ModulePage,
  type ModuleVersion,
  type PanelState,
  type PinRow,
  type Snapshot,
} from './calculations'

const selectClass = 'h-9 w-full rounded-md border border-input bg-background px-2 text-sm'

function Problems({ problems, testId }: { problems: string[]; testId: string }) {
  if (problems.length === 0) return null
  return (
    <ul className="text-xs text-muted-foreground" data-testid={testId}>
      {problems.map((p) => (
        <li key={p}>{p}</li>
      ))}
    </ul>
  )
}

const Hash = ({ value }: { value: string | null }) =>
  value ? <span className="break-all font-mono text-[11px]">{value}</span> : <span className="text-muted-foreground">—</span>

/** Step 1: one projected import per schema code (D-30 §1), then build or reuse the snapshot. */
function DatasetStep({
  state,
  loaded,
  modules,
  onRetry,
  dispatch,
}: {
  state: PanelState
  loaded: Loaded<Candidates>
  modules: readonly ModuleVersion[]
  onRetry: () => void
  dispatch: Parameters<typeof buildSnapshot>[1]
}) {
  const reads = modules.filter(isSelectable)
  let body: ReactNode
  if (loaded.kind === 'loading') body = <LoadingBlock rows={3} />
  else if (loaded.kind === 'forbidden')
    body = (
      <div data-testid="candidates-forbidden">
        <Forbidden permission="import:read" correlationId={loaded.cid} />
      </div>
    )
  else if (loaded.kind === 'failed') body = <LoadFailed icon={Database} title="Projected imports could not be loaded" cid={loaded.cid} onRetry={onRetry} />
  else if (loaded.kind === 'not_found')
    body = (
      <EmptyState
        icon={Database}
        title="Projected imports could not be listed"
        description={`The request was refused.${loaded.cid ? ` Correlation ID: ${loaded.cid}` : ''}`}
      />
    )
  else {
    const candidates = loaded.value
    const none = DOMAIN_SCHEMA_CODES.every((c) => candidates[c].items.length === 0)
    const problems = snapshotProblems(state)
    body = (
      <div className="space-y-4">
        {none ? (
          <EmptyState
            icon={Database}
            title="No projected import yet"
            description="A data steward validates an import and projects it to the Financial Domain from its import page; it is then offered here."
          />
        ) : (
          <div className="grid gap-3 md:grid-cols-3">
            {DOMAIN_SCHEMA_CODES.map((code) => (
              <label key={code} className="flex flex-col gap-1 text-xs text-muted-foreground">
                <span>
                  <span className="font-mono text-foreground">{code}</span> · {candidates[code].total} projected
                  {candidates[code].total > candidates[code].items.length && ` (newest ${candidates[code].items.length} offered)`}
                </span>
                <select
                  aria-label={`Import for ${code}`}
                  className={selectClass}
                  value={state.selection[code] ?? ''}
                  disabled={state.snapshot.kind === 'building' || state.submit.kind === 'submitting'}
                  onChange={(e) => dispatch({ type: 'select', schema: code, importId: e.target.value })}
                  data-testid={`snapshot-pick-${code}`}
                >
                  <option value="">— none —</option>
                  {candidates[code].items.map((job) => (
                    <option key={job.id} value={job.id}>
                      {job.source_code} · {new Date(job.created_at).toLocaleString()} · {job.row_count} rows · {job.status} · {job.id.slice(0, 8)}
                    </option>
                  ))}
                </select>
              </label>
            ))}
          </div>
        )}
        <p className="text-xs text-muted-foreground" data-testid="candidates-scope">
          Offered: the {CANDIDATES_PER_SCHEMA} newest imports per schema that the server lists as projected into the Financial Domain (GET /imports?projected=true).
          QUARANTINED imports count with their accepted rows only; their held counts travel into the run manifest (D-30 §3).
        </p>
        {reads.map((m) => (
          <p key={m.module_version_id} className="text-xs text-muted-foreground">
            <span className="font-mono">
              {m.code} v{m.version}
            </span>{' '}
            reads:{' '}
            {m.input_contract.schemas.map((s) => `${s.schema_code}${s.required ? ' (required)' : ' (optional)'}`).join(', ')}.
          </p>
        ))}
        <div className="flex flex-wrap items-center gap-3">
          <Button size="sm" disabled={problems.length > 0} onClick={() => void buildSnapshot(state, dispatch)} data-testid="snapshot-build">
            <Boxes className="mr-2 h-4 w-4" />
            {state.snapshot.kind === 'building' ? 'Pinning…' : 'Build or reuse snapshot'}
          </Button>
          <Problems problems={problems.filter((p) => p !== 'A request is in flight.')} testId="snapshot-problems" />
        </div>
      </div>
    )
  }

  return (
    <PageSection
      icon={Database}
      title="1. Dataset snapshot"
      description="Pin at most one projected import per schema. The snapshot is content-addressed: the same members return the existing snapshot and nothing is written (D-30 §2)."
    >
      <div className="space-y-4" data-testid="new-run-dataset">
        {body}
        {state.snapshot.kind === 'refused' && <RefusalNotice refusal={state.snapshot.refusal} />}
        {state.snapshot.kind === 'built' && <SnapshotResult snapshot={state.snapshot.snapshot} origin={state.snapshot.origin} />}
      </div>
    </PageSection>
  )
}

function SnapshotResult({ snapshot, origin }: { snapshot: Snapshot; origin: 'created' | 'reused' }) {
  return (
    <div className="space-y-2" data-testid="snapshot-result" data-snapshot-id={snapshot.snapshot_id} data-origin={origin}>
      <p className="rounded-md border border-border bg-surface-2 px-3 py-2 text-sm" data-testid="snapshot-outcome">
        {origin === 'created'
          ? 'Pinned a new dataset snapshot.'
          : 'Reused the existing snapshot with exactly these members (content-addressed): nothing new was written.'}
      </p>
      <dl className="grid grid-cols-[180px_1fr] gap-y-1 text-sm">
        <dt className="text-muted-foreground">Snapshot ID</dt>
        <dd className="font-mono text-xs" data-testid="snapshot-id">
          {snapshot.snapshot_id}
        </dd>
        <dt className="text-muted-foreground">Content SHA-256</dt>
        <dd data-testid="snapshot-content-sha256">
          <Hash value={snapshot.content_sha256} />
        </dd>
        <dt className="text-muted-foreground">Created</dt>
        <dd className="font-mono text-xs">
          {snapshot.created_by} · {new Date(snapshot.created_at).toLocaleString()}
        </dd>
      </dl>
      <SnapshotMembers members={snapshot.members} />
    </div>
  )
}

/** Step 2: an AVAILABLE module. PLANNED modules are disabled "Planned — extension point" entries (FE-18.1). */
function ModuleStep({
  state,
  loaded,
  onRetry,
  dispatch,
}: {
  state: PanelState
  loaded: Loaded<ModulePage>
  onRetry: () => void
  dispatch: Parameters<typeof buildSnapshot>[1]
}) {
  let body: ReactNode
  if (loaded.kind === 'loading') body = <LoadingBlock rows={2} />
  else if (loaded.kind === 'forbidden') body = <Forbidden permission="rule:read" correlationId={loaded.cid} />
  else if (loaded.kind === 'failed') body = <LoadFailed icon={Puzzle} title="Modules could not be loaded" cid={loaded.cid} onRetry={onRetry} />
  else if (loaded.kind === 'not_found')
    body = (
      <EmptyState icon={Puzzle} title="Modules could not be listed" description={`The request was refused.${loaded.cid ? ` Correlation ID: ${loaded.cid}` : ''}`} />
    )
  else if (loaded.value.items.length === 0) body = <EmptyState icon={Puzzle} title="No calculation module is registered" />
  else {
    const snapshot = state.snapshot.kind === 'built' ? state.snapshot.snapshot : null
    body = (
      <ul className="space-y-2" data-testid="module-list" role="radiogroup" aria-label="Module">
        {loaded.value.items.map((m) => (
          <ModuleOption
            key={m.module_version_id}
            module={m}
            checked={state.moduleVersionId === m.module_version_id}
            busy={state.submit.kind === 'submitting'}
            missing={snapshot ? missingSchemas(m, snapshot) : []}
            onPick={(moduleVersionId) => dispatch({ type: 'module', moduleVersionId })}
          />
        ))}
      </ul>
    )
  }
  return (
    <PageSection icon={Puzzle} title="2. Module" description="Only AVAILABLE module versions run. PLANNED ones are declared extension points and never run (D-31 §1).">
      {body}
    </PageSection>
  )
}

function PinsTable({ rows }: { rows: PinRow[] }) {
  return (
    <Table data-testid="pins-preview">
      <TableHeader>
        <TableRow>
          <TableHead>Pin</TableHead>
          <TableHead>What</TableHead>
          <TableHead>Version</TableHead>
          <TableHead>ID</TableHead>
          <TableHead>SHA-256</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((r, i) => (
          <TableRow key={`${r.kind}-${i}`} data-testid="pin-row" data-kind={r.kind} data-id={r.id ?? ''} data-missing={r.missing}>
            <TableCell className="text-xs text-muted-foreground">{r.kind === 'REPORTING_DATE' ? 'Reporting date' : INPUT_REF_TEXT[r.kind]}</TableCell>
            <TableCell className="text-sm">
              <span className="font-mono">{r.label}</span>
              {r.note && <div className={cn('text-xs', r.missing ? 'text-destructive' : 'text-muted-foreground')}>{r.note}</div>}
            </TableCell>
            <TableCell className="font-mono text-sm" data-testid="pin-version">
              {r.version ?? '—'}
            </TableCell>
            <TableCell className="font-mono text-xs" data-testid="pin-id">
              {r.id ?? '—'}
            </TableCell>
            <TableCell className="max-w-[220px]">
              <Hash value={r.hash} />
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

/**
 * FE-15.1: the New-run panel (calc:run). The steps follow the state machine in
 * calculations.ts; every outcome shown is the server's answer, and refusals carry their code
 * and correlation ID. A submitted run opens on its own page, in the status the server set.
 */
export function NewRunPanel() {
  const router = useRouter()
  const [state, dispatch] = useReducer(panelReducer, INITIAL_PANEL)
  const [candidates, setCandidates] = useState<Loaded<Candidates>>({ kind: 'loading' })
  const [modules, setModules] = useState<Loaded<ModulePage>>({ kind: 'loading' })
  const candidateLoader = useMemo(() => createLoader(fetchCandidates, setCandidates, 'Could not load projected imports'), [])
  const moduleLoader = useMemo(() => createLoader(fetchModules, setModules, 'Could not load calculation modules'), [])

  useEffect(() => {
    candidateLoader.load()
    moduleLoader.load()
    return () => {
      candidateLoader.abort()
      moduleLoader.abort()
    }
  }, [candidateLoader, moduleLoader])

  const moduleList = modules.kind === 'ready' ? modules.value.items : []
  const picked = moduleList.find((m) => m.module_version_id === state.moduleVersionId) ?? null
  const snapshot = state.snapshot.kind === 'built' ? state.snapshot.snapshot : null
  const problems = submitProblems(state, moduleList)

  const submit = async () => {
    const run = await submitRun(state, moduleList, dispatch)
    if (run) router.push(runHref(run.run_id))
  }

  return (
    <div className="space-y-4" data-testid="new-run-panel">
      <DatasetStep state={state} loaded={candidates} modules={moduleList} onRetry={candidateLoader.load} dispatch={dispatch} />
      <ModuleStep state={state} loaded={modules} onRetry={moduleLoader.load} dispatch={dispatch} />
      <PageSection icon={CalendarDays} title="3. Reporting date" description="Buckets count calendar days after this date (D-35 §1).">
        <Input
          type="date"
          aria-label="Reporting date"
          className="w-48 font-mono"
          value={state.reportingDate}
          disabled={state.submit.kind === 'submitting'}
          onChange={(e) => dispatch({ type: 'reporting_date', value: e.target.value })}
          data-testid="reporting-date"
        />
      </PageSection>
      <PageSection
        icon={ListChecks}
        title="4. Review the pins, then submit"
        description="What the run will pin, as the server reports it now. The rule set is content-addressed from these rule versions when the run is written (D-32 §4); the run's manifest is the record."
      >
        <div className="space-y-4" data-testid="new-run-review">
          {picked ? (
            <PinsTable rows={pinsPreview(picked, snapshot, state.reportingDate)} />
          ) : (
            <p className="text-sm text-muted-foreground" data-testid="pins-empty">
              Pick a module to see the rule versions, assumption set and engine it pins.
            </p>
          )}
          {state.submit.kind === 'refused' && <RefusalNotice refusal={state.submit.refusal} />}
          {state.submit.kind === 'submitted' && (
            <p className="flex flex-wrap items-center gap-2 text-sm" data-testid="run-submitted" data-run-id={state.submit.run.run_id}>
              {state.submit.origin === 'replayed' ? 'This request was already submitted; the server returned the original run' : 'Run written'}:{' '}
              <Link href={runHref(state.submit.run.run_id)} className="font-mono text-xs text-primary hover:underline">
                {state.submit.run.run_id}
              </Link>
              <RunStatusPill status={state.submit.run.status} />
            </p>
          )}
          <div className="flex flex-wrap items-center gap-3">
            <Button onClick={() => void submit()} disabled={problems.length > 0} data-testid="run-submit">
              <Play className="mr-2 h-4 w-4" />
              {state.submit.kind === 'submitting' ? 'Running…' : 'Submit run'}
            </Button>
            <Problems problems={problems.filter((p) => p !== 'A request is in flight.')} testId="submit-problems" />
          </div>
          {snapshot && (
            <p className="text-xs text-muted-foreground">
              Members:{' '}
              {snapshot.members.map((m, i) => (
                <span key={m.import_id}>
                  {i > 0 && ', '}
                  <Link href={importHref(m.import_id)} className="font-mono text-primary hover:underline">
                    {m.schema_code}
                  </Link>
                </span>
              ))}
              . Runs are synchronous: the page opens the run when the server has ended it.
            </p>
          )}
        </div>
      </PageSection>
    </div>
  )
}
