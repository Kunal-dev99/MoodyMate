'use client'

import Link from 'next/link'
import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, ChevronDown, ChevronUp, Fingerprint, GitCompare, Play } from 'lucide-react'
import { useCurrentSession } from '@/components/auth/SessionProvider'
import { toastApiError } from '@/components/common/api-error-toast'
import { createLoader, type Loaded } from '@/components/common/load'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { TruncationNote } from '@/components/common/ListPager'
import { RunIdentity } from '@/components/calculations/RunIdentity'
import { RunStatusPill } from '@/components/calculations/RunStatusPill'
import { INPUT_REF_TEXT, type RunDetail } from '@/components/calculations/calculations'
import type { Refusal } from '@/components/ingestion/mappings'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { STRESS_COMPARE_UI_ENABLED } from '@/config/features'
import { runHref } from '@/lib/runs'
import { cn } from '@/lib/utils'
import { ScenarioRefusalNotice } from './ScenarioRefusalNotice'
import { comparisonHref } from './comparison'
import {
  approvalVariant,
  attemptFor,
  baselineOptionText,
  canRunVersion,
  fetchBaseline,
  fetchBaselines,
  fetchValidation,
  memberLabel,
  memberStatusText,
  runCountText,
  runGate,
  runScenario,
  scenarioRunRefusal,
  type BaselineOptions,
  type RunAttempt,
  type ScenarioRunOut,
  type ScenarioValidation,
} from './scenario-runs'
import type { ScenarioVersion } from './scenarios'

/** What the panel reads of a version. */
type RunnableVersion = Pick<ScenarioVersion, 'status' | 'version' | 'scenario_version_id'>

const selectClass = 'h-9 w-full rounded-md border border-input bg-background px-2 text-sm'

/** A sub-load whose failure is shown inline, with its correlation ID (never a guessed value). */
function LoadProblem({ state, what, testId }: { state: Loaded<unknown>; what: string; testId: string }) {
  if (state.kind === 'ready' || state.kind === 'loading') return null
  const text =
    state.kind === 'forbidden'
      ? `You are not allowed to read the ${what} (HTTP 403).`
      : state.kind === 'not_found'
        ? `The ${what} was not found or is not visible to you.`
        : `The ${what} could not be loaded.`
  return (
    <p className="text-xs text-destructive" data-testid={testId} data-kind={state.kind}>
      {text}
      {state.cid && <> Correlation ID: {state.cid}</>}
    </p>
  )
}

/** The baseline picker: succeeded, non-stressed maturity_ladder runs only. */
export function BaselinePicker({ id, state, value, disabled, onChange }: { id: string; state: Loaded<BaselineOptions>; value: string; disabled: boolean; onChange: (runId: string) => void }) {
  if (state.kind === 'loading') return <LoadingBlock rows={1} />
  if (state.kind !== 'ready') return <LoadProblem state={state} what="baseline runs" testId="baseline-load-problem" />
  const { runs, truncation } = state.value
  // Q18: one capped page per succeeded status; say so when the server cut one.
  const note = <TruncationNote note={truncation} testId="baseline-truncated" className="mt-1" />
  if (runs.length === 0)
    return (
      <>
        <p className="text-sm text-muted-foreground" data-testid="baseline-none">
          No baseline yet: a run of maturity_ladder that ended SUCCEEDED and is not itself stressed is needed.{' '}
          <Link href="/calculations/new" className="text-primary hover:underline">
            New run
          </Link>
        </p>
        {note}
      </>
    )
  return (
    <>
      <select id={id} className={selectClass} value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)} data-testid="baseline-picker" data-count={runs.length}>
        <option value="">Pick a baseline run…</option>
        {runs.map((r) => (
          <option key={r.run_id} value={r.run_id}>
            {baselineOptionText(r)}
          </option>
        ))}
      </select>
      {note}
    </>
  )
}

/** The dry run: what each shock matches on the baseline's inputs (value-free counts). */
export function ValidationBlock({ validation }: { validation: ScenarioValidation }) {
  const gate = runGate(validation)
  return (
    <div className="space-y-2" data-testid="run-validation" data-runnable={gate.runnable} data-run-count={validation.run_count}>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="font-medium">Dry run</span>
        {validation.approval_state && validation.approval_label && (
          <Badge variant={approvalVariant(validation.approval_state)} data-testid="validation-approval-label">
            {validation.approval_label}
          </Badge>
        )}
        <span className="text-xs text-muted-foreground" data-testid="validation-run-count">
          {runCountText(validation)}
        </span>
      </div>
      <Table data-testid="validation-shocks">
        <TableHeader>
          <TableRow>
            <TableHead className="w-12">#</TableHead>
            <TableHead>Template</TableHead>
            <TableHead className="text-right">Matched flows</TableHead>
            <TableHead className="text-right">Parts</TableHead>
            <TableHead className="text-right">Direction mismatches</TableHead>
            <TableHead className="text-right">Skipped: past due</TableHead>
            <TableHead className="text-right">Skipped: incomplete</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {validation.shocks.map((s) => (
            <TableRow key={s.shock_index} data-testid="validation-shock" data-shock-index={s.shock_index} data-matched={s.matched_flows} className={cn(s.matched_flows === 0 && 'bg-destructive/5')}>
              <TableCell className="font-mono text-xs">{s.shock_index}</TableCell>
              <TableCell className="font-mono text-xs">{s.template}</TableCell>
              <TableCell className="text-right font-mono text-sm" data-testid="validation-matched">
                {s.matched_flows}
                {s.matched_flows === 0 && (
                  <Badge variant="destructive" className="ml-2" data-testid="validation-zero-match">
                    No match
                  </Badge>
                )}
              </TableCell>
              <TableCell className="text-right font-mono text-sm">{s.parts}</TableCell>
              <TableCell className="text-right font-mono text-sm">{s.direction_mismatches}</TableCell>
              <TableCell className="text-right font-mono text-sm">{s.skipped_past_due}</TableCell>
              <TableCell className="text-right font-mono text-sm">{s.skipped_incomplete}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {gate.reasons.length > 0 && (
        <Alert variant="warning" data-testid="run-blocked">
          <AlertTriangle className="h-4 w-4" />
          <AlertTitle>Run is disabled</AlertTitle>
          <AlertDescription className="text-foreground">
            <ul className="list-disc pl-4">
              {gate.reasons.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      )}
    </div>
  )
}

/** The pins every stressed run copies from the baseline, exactly (D-45 §1). */
function BaselinePins({ run }: { run: RunDetail }) {
  return (
    <div className="space-y-2" data-testid="baseline-pins" data-run-id={run.run_id} data-manifest-sha256={run.manifest_sha256}>
      <div className="flex items-center gap-2 text-sm">
        <Fingerprint className="h-4 w-4 text-muted-foreground" />
        <span className="font-medium">Pins that will be reused</span>
        <span className="text-xs text-muted-foreground">The stressed runs copy the baseline&apos;s pins exactly; only the shocked cash flows differ.</span>
      </div>
      <RunIdentity run={run} />
      <Table data-testid="baseline-pin-rows">
        <TableHeader>
          <TableRow>
            <TableHead className="w-12">#</TableHead>
            <TableHead>Pin</TableHead>
            <TableHead>What</TableHead>
            <TableHead>SHA-256</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {run.input_refs.map((ref) => (
            <TableRow key={ref.sequence} data-testid="baseline-pin" data-ref-type={ref.ref_type}>
              <TableCell className="font-mono text-xs">{ref.sequence}</TableCell>
              <TableCell className="text-xs">{INPUT_REF_TEXT[ref.ref_type]}</TableCell>
              <TableCell className="text-xs">
                {ref.label}
                {ref.ref_id && <div className="font-mono text-muted-foreground">{ref.ref_id}</div>}
              </TableCell>
              <TableCell className="font-mono text-xs" title={ref.sha256 ?? undefined}>
                {ref.sha256 ? `${ref.sha256.slice(0, 16)}…` : '—'}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

/** The scenario_run the server returned: each member run with the status the server set. */
export function RunResult({ result }: { result: { run: ScenarioRunOut; replayed: boolean } }) {
  const { run, replayed } = result
  return (
    <div className="space-y-2 rounded-md border border-border p-3" data-testid="scenario-run-result" data-scenario-run-id={run.scenario_run_id} data-replayed={replayed}>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span>
          {replayed ? 'This request was already submitted; the server returned the original scenario run' : 'The server wrote scenario run'}{' '}
          <span className="font-mono text-xs">{run.scenario_run_id}</span>
        </span>
        <Badge variant={approvalVariant(run.approval_state)} data-testid="scenario-run-approval" data-approval-state={run.approval_state}>
          {run.approval_label}
        </Badge>
        {/* FE-25.2 hand-off: off (NEXT_PUBLIC_STRESS_COMPARE_UI), no link, as the comparison routes are a 404. */}
        {STRESS_COMPARE_UI_ENABLED && (
          <Button asChild size="sm" variant="outline" data-testid="scenario-run-result-compare">
            <Link href={comparisonHref(run.scenario_id, run.scenario_run_id)}>
              <GitCompare className="mr-2 h-4 w-4" />
              Compare with baseline
            </Link>
          </Button>
        )}
      </div>
      <p className="text-xs text-muted-foreground">
        Baseline{' '}
        <Link href={runHref(run.baseline_run_id)} className="font-mono text-primary hover:underline" data-testid="scenario-run-baseline">
          {run.baseline_run_id}
        </Link>{' '}
        · reporting date <span className="font-mono">{run.reporting_date}</span> · v{run.version} · shocks sha256{' '}
        <span className="font-mono" title={run.shocks_sha256}>
          {run.shocks_sha256.slice(0, 16)}…
        </span>{' '}
        · <span data-testid="scenario-run-statuses">{memberStatusText(run.members)}</span>
      </p>
      <Table data-testid="scenario-run-members">
        <TableHeader>
          <TableRow>
            <TableHead>Run</TableHead>
            <TableHead>Shocks</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Manifest SHA-256</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {run.members.map((m) => (
            <TableRow key={m.calculation_run_id} data-testid="scenario-run-member" data-run-id={m.calculation_run_id} data-status={m.status} data-combined={m.combined}>
              <TableCell>
                <Link href={runHref(m.calculation_run_id)} className="font-mono text-xs text-primary hover:underline" data-testid="scenario-run-member-link">
                  {m.calculation_run_id}
                </Link>
              </TableCell>
              <TableCell className="text-xs">{memberLabel(m)}</TableCell>
              <TableCell>
                <RunStatusPill status={m.status} testId="scenario-run-member-status" />
              </TableCell>
              <TableCell className="font-mono text-xs" title={m.manifest_sha256}>
                {m.manifest_sha256.slice(0, 16)}…
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

const noFetch = () => new Promise<never>(() => undefined)

/**
 * FE-25.1: "Run against baseline" on a CONFIRMED_FOR_EXPLORATION or APPROVED_FOR_TEST version,
 * for `calc:run` (cosmetic; the server checks it, D-04). Pick a succeeded, non-stressed
 * maturity_ladder baseline; the dry run shows each shock's match counts (Run stays disabled while
 * any is 0 or the server names a blocking reason) and the pins the stressed runs reuse; Run sends
 * one Idempotency-Key per attempt (a retry of the same body reuses it). What it shows afterwards
 * is the server's scenario_run, each member run with its own status. Hidden while
 * NEXT_PUBLIC_STRESS_COMPARE_UI=off (the Phase 24 explorer, D-02).
 */
export function ScenarioRunPanel({ scenarioId, version, enabled }: { scenarioId: string; version: RunnableVersion; enabled: boolean }) {
  const { has } = useCurrentSession()
  if (!STRESS_COMPARE_UI_ENABLED || !canRunVersion(version, has)) return null
  return <RunPanelBody scenarioId={scenarioId} version={version} enabled={enabled} />
}

function RunPanelBody({ scenarioId, version, enabled }: { scenarioId: string; version: RunnableVersion; enabled: boolean }) {
  const [open, setOpen] = useState(false)
  const [baselineId, setBaselineId] = useState('')
  const [baselines, setBaselines] = useState<Loaded<BaselineOptions>>({ kind: 'loading' })
  const [validation, setValidation] = useState<Loaded<ScenarioValidation>>({ kind: 'loading' })
  const [baseline, setBaseline] = useState<Loaded<RunDetail>>({ kind: 'loading' })
  const [attempt, setAttempt] = useState<RunAttempt | null>(null)
  const [busy, setBusy] = useState(false)
  const [refusal, setRefusal] = useState<Refusal | null>(null)
  const [result, setResult] = useState<{ run: ScenarioRunOut; replayed: boolean } | null>(null)

  const baselinesLoader = useMemo(() => createLoader(open ? fetchBaselines : noFetch, setBaselines, 'Could not load the baseline runs'), [open])
  const validationLoader = useMemo(
    () => createLoader(baselineId ? fetchValidation(scenarioId, version.version, baselineId) : noFetch, setValidation, 'Could not run the dry run'),
    [scenarioId, version.version, baselineId],
  )
  const baselineLoader = useMemo(() => createLoader(baselineId ? fetchBaseline(baselineId) : noFetch, setBaseline, 'Could not load the baseline run'), [baselineId])
  useEffect(() => {
    if (!open) return
    baselinesLoader.load()
    return baselinesLoader.abort
  }, [open, baselinesLoader])
  useEffect(() => {
    if (!baselineId) return
    validationLoader.load()
    baselineLoader.load()
    return () => {
      validationLoader.abort()
      baselineLoader.abort()
    }
  }, [baselineId, validationLoader, baselineLoader])

  const pick = (runId: string) => {
    if (busy) return
    setBaselineId(runId)
    setRefusal(null)
    setResult(null)
  }

  const gate = validation.kind === 'ready' ? runGate(validation.value) : null
  const ready = !!baselineId && enabled && !!gate?.runnable && baseline.kind === 'ready' && !busy

  async function send() {
    if (!ready) return
    const next = attemptFor(attempt, version.version, baselineId)
    setAttempt(next)
    setBusy(true)
    setRefusal(null)
    try {
      const done = await runScenario(scenarioId, next)
      setResult(done)
      // The request is answered: a new run of the same body is a new attempt with a new key.
      setAttempt(null)
    } catch (err) {
      const r = scenarioRunRefusal(err)
      toastApiError(err, r.title)
      setRefusal(r)
    } finally {
      setBusy(false)
    }
  }

  const pickerId = `baseline-${version.scenario_version_id}`
  return (
    <div className="space-y-3" data-testid="scenario-run-panel" data-version={version.version} data-open={open}>
      <Button size="sm" variant={open ? 'secondary' : 'default'} onClick={() => setOpen(!open)} data-testid="run-against-baseline" aria-expanded={open}>
        <Play className="mr-2 h-4 w-4" />
        Run against baseline
        {open ? <ChevronUp className="ml-2 h-4 w-4" /> : <ChevronDown className="ml-2 h-4 w-4" />}
      </Button>
      {open && (
        <div className="space-y-4 rounded-md border border-border p-3">
          <p className="text-xs text-muted-foreground">
            Runs v{version.version} on a baseline maturity ladder, deterministically and without AI: each stressed run copies the baseline&apos;s pins and applies the shocks to a
            scenario-specific copy of its cash flows. Illustrative, never a regulatory stress test.
          </p>
          {!enabled && (
            <p className="text-xs text-[hsl(var(--warning))]" data-testid="run-switched-off">
              Scenario changes are switched off (SCENARIOS_ENABLED): the server refuses new runs.
            </p>
          )}
          <div className="space-y-1">
            <label htmlFor={pickerId} className="text-sm font-medium">
              Baseline run
            </label>
            <BaselinePicker id={pickerId} state={baselines} value={baselineId} disabled={busy} onChange={pick} />
          </div>
          {baselineId && (
            <>
              {validation.kind === 'loading' ? <LoadingBlock rows={2} /> : validation.kind === 'ready' ? <ValidationBlock validation={validation.value} /> : <LoadProblem state={validation} what="dry run" testId="validation-load-problem" />}
              {baseline.kind === 'loading' ? <LoadingBlock rows={2} /> : baseline.kind === 'ready' ? <BaselinePins run={baseline.value} /> : <LoadProblem state={baseline} what="baseline run" testId="baseline-detail-problem" />}
              <div className="flex flex-wrap items-center gap-2">
                <Button size="sm" onClick={send} disabled={!ready} data-testid="scenario-run-submit">
                  <Play className="mr-2 h-4 w-4" />
                  {busy ? 'Running…' : validation.kind === 'ready' && validation.value.run_count > 1 ? `Run (${validation.value.run_count} runs)` : 'Run'}
                </Button>
                <span className="text-xs text-muted-foreground">Synchronous: the server answers when every run has ended. Recorded with your name and audited.</span>
              </div>
            </>
          )}
          {refusal && <ScenarioRefusalNotice refusal={refusal} />}
          {result && <RunResult result={result} />}
        </div>
      )}
    </div>
  )
}
