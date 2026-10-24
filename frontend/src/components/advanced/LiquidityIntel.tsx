'use client'

import { useEffect, useRef, useState } from 'react'
import { AlertTriangle, CheckCircle2, CircleDashed, FileSearch, FlaskConical, GitCompare, History, Lock, Play, Search, ShieldCheck, Sparkles } from 'lucide-react'
import { PageSection } from '@/components/common/PageSection'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Label } from '@/components/ui/label'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'
import { DEMO_BANNER, ratio } from './agentDemo'
import {
  BALANCES_NOTE, DEPOSIT_EVIDENCE, LATEST, NOT_COMPUTABLE, PREVIOUS, RUNOFF_COLUMN, bridge, calculate, crore, largestDepositChange, lcrDelta, movementText, pct, pp,
  shockDeposits, type LcrResult,
} from './liquidityIntel'

const STEPS = ['Request', 'Validate runs', 'Historical movement', 'Explanation', 'Define scenario', 'Authorise & execute', 'Results & audit']
/**
 * Mock-only walkthrough steps. They deliberately avoid the platform's lifecycle state names
 * (Q20): this page never shows a run state the backend has not reported.
 */
const SCENARIO_STEPS = [
  { id: 'draft', label: 'Mock draft' },
  { id: 'checking', label: 'Browser check' },
  { id: 'checked', label: 'Browser-checked' },
  { id: 'confirm', label: 'Awaiting analyst' },
  { id: 'approval', label: 'Awaiting approver' },
  { id: 'approved', label: 'Simulated approval' },
  { id: 'computing', label: 'Computing in browser' },
  { id: 'shown', label: 'Mock result shown' },
] as const
type ScenarioState = (typeof SCENARIO_STEPS)[number]['id']

/** Every LcrResult field except the ratio is an amount in hundredths of ₹ crore. */
type AmountKey = Exclude<keyof LcrResult, 'lcr'>

const stamp = () => `${new Date().toISOString().slice(11, 19)} UTC`
const prevR = calculate(PREVIOUS.inputs)
const latestR = calculate(LATEST.inputs)
const BRIDGE = bridge(PREVIOUS.inputs, LATEST.inputs)
const SCENARIO = calculate(shockDeposits(LATEST.inputs, 25))
const MOVEMENT = lcrDelta(prevR.lcr, latestR.lcr)
const SAME_DATE = PREVIOUS.date === LATEST.date
const SAME_RULES = PREVIOUS.rules === LATEST.rules
const HQLA_DELTA = latestR.hqla - prevR.hqla
const TOP_DEPOSIT = largestDepositChange(DEPOSIT_EVIDENCE)
const signedCrore = (n: number) => `${n > 0 ? '+' : ''}${crore(n)}`
const hqlaText = HQLA_DELTA === 0 ? `Recognised HQLA was unchanged at ${crore(latestR.hqla)}` : `Recognised HQLA changed by ${signedCrore(HQLA_DELTA)}`

/**
 * Only checks this mock can actually compute from its synthetic inputs are shown as passed or
 * warned; everything the real workflow checks server-side is listed as not performed here.
 */
const RUN_CHECKS: { check: string; result: string; kind: 'ok' | 'warn' | 'skipped' }[] = [
  { check: 'Reporting date', result: SAME_DATE ? `Both ${LATEST.date}` : `${PREVIOUS.date} vs ${LATEST.date}: different month-ends, labelled in every result`, kind: SAME_DATE ? 'ok' : 'warn' },
  { check: 'Rule & assumption version labels', result: SAME_RULES ? `Identical labels: ${LATEST.rules}` : `Differ: ${PREVIOUS.rules} vs ${LATEST.rules}`, kind: SAME_RULES ? 'ok' : 'warn' },
  { check: 'LCR computable', result: prevR.lcr !== null && latestR.lcr !== null ? 'Net outflows above zero in both runs' : `LCR ${NOT_COMPUTABLE} in at least one run (net outflows ≤ 0)`, kind: prevR.lcr !== null && latestR.lcr !== null ? 'ok' : 'warn' },
  ...['Run status', 'Model identity', 'Input snapshot retrieval', 'Currency & scope', 'Data lineage', 'Authorisation', 'Reproducibility'].map((check) => ({
    check,
    result: 'Not performed in this mock; the real workflow checks this on the server',
    kind: 'skipped' as const,
  })),
]
const WARNINGS = RUN_CHECKS.filter((c) => c.kind === 'warn').length
const SKIPPED = RUN_CHECKS.filter((c) => c.kind === 'skipped').length

/** Mock walk-through of the Liquidity Intelligence workflow (design spec: LCR movement + 25% deposit shock). */
export function LiquidityIntel() {
  const [question, setQuestion] = useState('Why did the LCR change between the last two runs?')
  const [withShock, setWithShock] = useState(true)
  const [stage, setStage] = useState(0)
  const [checks, setChecks] = useState(0)
  const [meaning, setMeaning] = useState<'amount' | 'rate' | null>(null)
  const [state, setState] = useState<ScenarioState | null>(null)
  const [audit, setAudit] = useState<{ ts: string; msg: string }[]>([])
  const timers = useRef<ReturnType<typeof setTimeout>[]>([])
  useEffect(() => () => timers.current.forEach(clearTimeout), [])
  const later = (ms: number, fn: () => void) => timers.current.push(setTimeout(fn, ms))
  const log = (msg: string) => setAudit((a) => [{ ts: stamp(), msg }, ...a])

  function start() {
    timers.current.forEach(clearTimeout)
    setStage(1)
    setChecks(0)
    setMeaning(null)
    setState(null)
    setAudit([])
    log(`Investigation INV-0027 opened by Risk analyst: “${question}”`)
    later(500, () => { setStage(2); log(`Picked the two synthetic lcr_hqla runs: ${PREVIOUS.id}, ${LATEST.id}`) })
    RUN_CHECKS.forEach((_, i) => later(700 + i * 250, () => setChecks(i + 1)))
    later(700 + RUN_CHECKS.length * 250 + 200, () => { log(`Mock run checks computed: ${WARNINGS} labelled warning${WARNINGS === 1 ? '' : 's'}, ${SKIPPED} not performed in this mock`); setStage(3) })
    later(700 + RUN_CHECKS.length * 250 + 900, () => {
      log(BRIDGE.reconciles ? `Browser bridge_v1 attribution sums to ${pp(MOVEMENT)}` : 'Browser bridge_v1 attribution: ATTRIBUTION_INCOMPLETE (does not reconcile or not computable)')
      setStage(4)
    })
    later(700 + RUN_CHECKS.length * 250 + 1600, () => {
      log('Mock explanation written from the synthetic evidence records EVD-0027 (4 findings)')
      if (withShock) {
        setStage(5)
        setState('draft')
        log('Scenario SCN-0031 drafted: deposit-outflow shock 25% (meaning not yet confirmed)')
      }
    })
  }

  function chooseMeaning(m: 'amount' | 'rate') {
    setMeaning(m)
    if (m === 'rate') {
      log('Analyst chose “+25 pp to runoff rates”: blocked, not an approved scenario template')
      return
    }
    log('Analyst confirmed meaning: eligible deposit-outflow amounts +25% before runoff methodology')
    setState('checking')
    later(600, () => { setState('checked'); log(`Scenario parameters checked in the browser: one shock, applied once, against synthetic baseline ${LATEST.id}`) })
    later(1000, () => { setState('confirm'); setStage(6) })
  }

  function confirm() {
    setState('approval')
    log('Analyst confirmed execution of SCN-0031 v1; approval requested (maker-checker)')
  }
  function approve() {
    setState('approved')
    log('Simulated approver (second user) approved SCN-0031 v1 with comment “Sensitivity test for ALCO pack”')
    later(500, () => { setState('computing'); log(`Mock scenario run RUN-SCN-0031-1 started on synthetic snapshot ${LATEST.snapshot} (baseline untouched)`) })
    later(1400, () => {
      setState('shown')
      setStage(7)
      log('Mock scenario result RUN-SCN-0031-1 computed in the browser (simulated; no platform run state, nothing stored)')
    })
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_300px]">
      <Alert variant="warning" className="lg:col-span-2" data-testid="demo-banner">
        <AlertTriangle className="h-4 w-4" />
        <AlertDescription>{DEMO_BANNER}</AlertDescription>
      </Alert>
      <div className="min-w-0 space-y-4">
        <Stepper stage={stage} withShock={withShock} />

        <PageSection icon={Search} title="Ask" description="The mock agent plans the investigation and explains results. In the real workflow the calculation engine produces every number; on this page the browser computes them from synthetic inputs." accent="primary">
          <Textarea id="li-question" value={question} onChange={(e) => setQuestion(e.target.value)} rows={2} />
          <div className="mt-3 flex flex-wrap items-center gap-4">
            <div className="flex items-center gap-2">
              <Checkbox id="li-shock" checked={withShock} onCheckedChange={(v) => setWithShock(v === true)} />
              <Label htmlFor="li-shock" className="text-sm">Then test a 25% deposit-outflow shock against the same baseline</Label>
            </div>
            <Button type="button" onClick={start}>
              <Sparkles className="mr-1 h-4 w-4" />
              {stage ? 'Restart investigation' : 'Start investigation'}
            </Button>
          </div>
        </PageSection>

        {stage >= 2 && (
          <PageSection icon={ShieldCheck} title="Step 2 · Validate the two historical runs" description="“Last two runs” is resolved and checked, never assumed comparable.">
            <div className="mb-3 grid gap-3 sm:grid-cols-2">
              <RunCard label="Previous run" run={PREVIOUS} r={prevR} />
              <RunCard label="Latest run" run={LATEST} r={latestR} />
            </div>
            <ul className="space-y-1.5 text-sm">
              {RUN_CHECKS.slice(0, checks).map((c) => (
                <li key={c.check} className="flex items-start gap-2">
                  {c.kind === 'warn' ? (
                    <AlertTriangle className="mt-0.5 h-4 w-4 flex-none text-warning" />
                  ) : c.kind === 'skipped' ? (
                    <CircleDashed className="mt-0.5 h-4 w-4 flex-none text-muted-foreground" />
                  ) : (
                    <CheckCircle2 className="mt-0.5 h-4 w-4 flex-none text-success" />
                  )}
                  <span><b>{c.check}</b> · <span className="text-muted-foreground">{c.result}</span></span>
                </li>
              ))}
            </ul>
          </PageSection>
        )}

        {stage >= 3 && (
          <PageSection icon={GitCompare} title="Step 3 · Historical movement (deterministic)" description="Each synthetic run’s components, computed in the browser; then an input-replacement bridge attributes the change.">
            <div className="overflow-x-auto">
              <Table className="tabular-nums">
                <TableHeader>
                  <TableRow>
                    <TableHead>Component</TableHead>
                    <TableHead className="text-right">RUN-LCR-0412 · 30 Sep</TableHead>
                    <TableHead className="text-right">RUN-LCR-0419 · 31 Oct</TableHead>
                    <TableHead className="text-right">Change</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {([
                    ['Recognised HQLA', 'hqla'],
                    ['Deposit outflows', 'depositOutflows'],
                    ['Other outflows', 'otherOutflows'],
                    ['Total outflows', 'totalOutflows'],
                    ['Recognised inflows (cap 75%)', 'recognisedInflows'],
                    ['Net cash outflows', 'netOutflows'],
                  ] as [string, AmountKey][]).map(([label, k]) => (
                    <TableRow key={k} className={cn(k === 'netOutflows' && 'font-semibold')}>
                      <TableCell>{label}</TableCell>
                      <TableCell className="text-right">{crore(prevR[k])}</TableCell>
                      <TableCell className="text-right">{crore(latestR[k])}</TableCell>
                      <TableCell className={cn('text-right', latestR[k] - prevR[k] !== 0 && 'font-semibold')}>{crore(latestR[k] - prevR[k])}</TableCell>
                    </TableRow>
                  ))}
                  <TableRow className="bg-primary/5 font-semibold">
                    <TableCell>LCR</TableCell>
                    <TableCell className="text-right">{pct(prevR.lcr)}</TableCell>
                    <TableCell className="text-right">{pct(latestR.lcr)}</TableCell>
                    <TableCell className={cn('text-right', MOVEMENT !== null && MOVEMENT < 0 && 'text-danger')}>{pp(MOVEMENT)}</TableCell>
                  </TableRow>
                </TableBody>
              </Table>
            </div>
            <h4 className="mb-1 mt-4 text-sm font-semibold">Attribution bridge (bridge_v1, documented replacement order)</h4>
            <Waterfall />
            <p className="mt-2 flex items-center gap-1.5 text-xs text-muted-foreground">
              {BRIDGE.reconciles ? <CheckCircle2 className="h-3.5 w-3.5 text-success" /> : <AlertTriangle className="h-3.5 w-3.5 text-danger" />}
              {BRIDGE.reconciles ? `Contributions sum to the computed ${pp(MOVEMENT)}.` : 'ATTRIBUTION_INCOMPLETE: contributions do not reconcile or are not computable.'} Order-dependent by design; the method version is disclosed.
            </p>
          </PageSection>
        )}

        {stage >= 4 && (
          <PageSection icon={FileSearch} title="Step 4 · Historical finding (observed)" description="The movement between the two synthetic runs. Not a scenario." accent="primary">
            <div className="space-y-3 text-sm">
              <Layer n={1} title="Observed result"><b data-testid="li-movement">{movementText(prevR.lcr, latestR.lcr)}</b>. The runs are dated {PREVIOUS.date} and {LATEST.date}; rule version labels are {SAME_RULES ? 'identical' : 'different'}.</Layer>
              <Layer n={2} title="Quantified drivers">
                {hqlaText}. Net cash outflows changed by {signedCrore(latestR.netOutflows - prevR.netOutflows)}.{' '}
                <b>Deposit outflows contribute {pp(BRIDGE.steps[1].contribution)}</b>, other outflows {pp(BRIDGE.steps[2].contribution)}, and recognised inflows {pp(BRIDGE.steps[3].contribution)}.
              </Layer>
              <Layer n={3} title="Supporting evidence">
                <div className="overflow-x-auto">
                  <Table className="tabular-nums">
                    <TableHeader><TableRow><TableHead>Deposit segment</TableHead><TableHead className="text-right">30 Sep</TableHead><TableHead className="text-right">31 Oct</TableHead><TableHead className="text-right">Change</TableHead><TableHead>{RUNOFF_COLUMN}</TableHead><TableHead>Source</TableHead></TableRow></TableHeader>
                    <TableBody>
                      {DEPOSIT_EVIDENCE.map((e) => (
                        <TableRow key={e.segment}>
                          <TableCell>{e.segment}</TableCell>
                          <TableCell className="text-right">{crore(e.prev)}</TableCell>
                          <TableCell className="text-right">{crore(e.latest)}</TableCell>
                          <TableCell className={cn('text-right', e.latest !== e.prev && 'font-semibold')}>{crore(e.latest - e.prev)}</TableCell>
                          <TableCell>{e.runoff}</TableCell>
                          <TableCell className="font-mono text-xs text-muted-foreground">{e.source}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
                <p className="mt-1 text-xs text-muted-foreground">{BALANCES_NOTE}</p>
              </Layer>
              <Layer n={4} title="Interpretation">
                In this mock the largest deposit change is {TOP_DEPOSIT.segment.toLowerCase()} ({signedCrore(TOP_DEPOSIT.latest - TOP_DEPOSIT.prev)}); {hqlaText.charAt(0).toLowerCase() + hqlaText.slice(1)}.{' '}
                <span className="text-muted-foreground">Open question: whether the movement is seasonal or month-end specific; two runs with different reporting dates cannot separate this.</span>{' '}
                Suggested next step: test sensitivity to a further deposit-outflow increase.
              </Layer>
              <details className="rounded-md border border-border p-2 text-xs">
                <summary className="cursor-pointer text-muted-foreground">Machine-readable evidence record (EVD-0027-02)</summary>
                <pre className="mt-2 overflow-x-auto font-mono">{JSON.stringify({ finding_code: 'DEPOSIT_OUTFLOW_INCREASE', baseline_run_id: PREVIOUS.id, comparison_run_id: LATEST.id, driver_group: 'DEPOSIT_OUTFLOWS', contribution: BRIDGE.steps[1].contribution === null ? null : (BRIDGE.steps[1].contribution / 10000).toFixed(4), contribution_unit: 'percentage_points', evidence_refs: ['IMP-1031-DEP#1-184', 'IMP-1031-DEP#185-2210'], rule_versions: ['lcr_runoff v3', 'inflow_cap v1'], confidence_status: 'ILLUSTRATIVE_MOCK', limitations: [...(SAME_DATE ? [] : ['REPORTING_DATE_DIFFERS']), 'SYNTHETIC_DATA', 'RUNOFF_NOT_APPLIED'] }, null, 2)}</pre>
              </details>
            </div>
          </PageSection>
        )}

        {stage >= 5 && withShock && (
          <PageSection icon={FlaskConical} title="Step 5 · Define the 25% deposit-outflow shock" description="A hypothetical, run separately. “25%” must mean one thing before anything executes." accent="warning">
            <ScenarioStates state={state} />
            <p className="mt-3 text-sm"><b>Agent asks:</b> “25%” can mean two materially different things. Which do you intend?</p>
            <div className="mt-2 grid gap-2 sm:grid-cols-2">
              <MeaningOption selected={meaning === 'amount'} disabled={meaning === 'amount'} onClick={() => chooseMeaning('amount')} title="Eligible deposit-outflow amounts +25%" note="Applied before the configured runoff methodology. Recommended default." />
              <MeaningOption selected={meaning === 'rate'} onClick={() => chooseMeaning('rate')} title="Runoff rates +25 percentage points" note="A different assumption; not an approved template." />
            </div>
            {meaning === 'rate' && <p className="mt-2 rounded-md border border-[hsl(var(--danger)/0.3)] bg-[hsl(var(--danger)/0.08)] p-2 text-sm text-danger">Blocked for clarification: changing runoff rates is not an approved scenario template. Choose the first option or ask for a methodology change.</p>}
            {meaning === 'amount' && (
              <div className="mt-3 overflow-x-auto">
                <Table>
                  <TableBody className="text-sm">
                    {[
                      ['scenario_type', 'DEPOSIT_OUTFLOW_SHOCK'],
                      ['baseline_run_id', `${LATEST.id} (synthetic)`],
                      ['base_snapshot_id', LATEST.snapshot],
                      ['shock_type', 'Relative increase in eligible deposit-outflow amount'],
                      ['shock_fraction', '"0.25"'],
                      ['target_population', 'All deposit segments eligible under lcr_runoff v3 (6,050 rows)'],
                      ['reporting_date', `${LATEST.date} (same as baseline)`],
                      ['methodology_version', 'deposit_shock v1'],
                    ].map(([k, v]) => (
                      <TableRow key={k}><TableCell className="w-48 font-mono text-xs text-muted-foreground">{k}</TableCell><TableCell>{v}</TableCell></TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </PageSection>
        )}

        {stage >= 6 && withShock && (
          <PageSection icon={Lock} title="Step 6 · Authorise and execute" description="Executing a scenario needs explicit confirmation and maker-checker approval. Historical runs are never edited.">
            <ScenarioStates state={state} />
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <Button type="button" onClick={confirm} disabled={state !== 'confirm'}>
                <CheckCircle2 className="mr-1 h-4 w-4" />Confirm execution (analyst)
              </Button>
              <Button type="button" variant="outline" onClick={approve} disabled={state !== 'approval'}>
                <Play className="mr-1 h-4 w-4" />Approve as Approver (second user, simulated)
              </Button>
              {state === 'approval' && <span className="text-xs text-muted-foreground">The analyst cannot approve their own scenario.</span>}
            </div>
          </PageSection>
        )}

        {stage >= 7 && withShock && (
          <div className="grid gap-4 xl:grid-cols-2">
            <PageSection title="Historical finding · observed" description={`${PREVIOUS.id} → ${LATEST.id}`} accent="primary">
              <p className="text-3xl font-semibold tabular-nums">{pct(prevR.lcr)} → {pct(latestR.lcr)}</p>
              <p className="mt-1 text-sm">{movementText(prevR.lcr, latestR.lcr)}; deposit outflows contribute {pp(BRIDGE.steps[1].contribution)}. {hqlaText}.</p>
              <Badge variant="info" className="mt-2">What happened</Badge>
            </PageSection>
            <PageSection title="Scenario finding · hypothetical" description={`RUN-SCN-0031-1 on baseline ${LATEST.id}`} accent="warning">
              <p className="text-3xl font-semibold tabular-nums">{pct(latestR.lcr)} → {pct(SCENARIO.lcr)}</p>
              <p className="mt-1 text-sm">If eligible deposit outflows were 25% higher, net outflows would change by {signedCrore(SCENARIO.netOutflows - latestR.netOutflows)}. Hypothetical: {movementText(latestR.lcr, SCENARIO.lcr)}.</p>
              <div className="mt-2 flex flex-wrap gap-2">
                <Badge variant="warning">What could happen</Badge>
                {SCENARIO.lcr !== null && SCENARIO.lcr < 1_000_000 && <Badge variant="destructive">Below 100% (illustrative threshold)</Badge>}
              </div>
            </PageSection>
            <div className="xl:col-span-2">
              <PageSection title="Scenario calculation" description="Same browser arithmetic as above, not the calculation engine; the inflow cap is recalculated after the shock; HQLA unchanged because the scenario does not touch assets.">
                <div className="overflow-x-auto">
                  <Table className="tabular-nums">
                    <TableHeader><TableRow><TableHead>Component</TableHead><TableHead className="text-right">Baseline {LATEST.id}</TableHead><TableHead className="text-right">After 25% shock</TableHead></TableRow></TableHeader>
                    <TableBody>
                      {([
                        ['Recognised HQLA', 'hqla'],
                        ['Deposit outflows', 'depositOutflows'],
                        ['Total outflows', 'totalOutflows'],
                        ['Inflow cap (75% of outflows)', 'inflowCap'],
                        ['Recognised inflows', 'recognisedInflows'],
                        ['Net cash outflows', 'netOutflows'],
                      ] as [string, AmountKey][]).map(([label, k]) => (
                        <TableRow key={k}><TableCell>{label}</TableCell><TableCell className="text-right">{crore(latestR[k])}</TableCell><TableCell className={cn('text-right', SCENARIO[k] !== latestR[k] && 'font-semibold')}>{crore(SCENARIO[k])}</TableCell></TableRow>
                      ))}
                      <TableRow className="bg-primary/5 font-semibold"><TableCell>LCR</TableCell><TableCell className="text-right">{pct(latestR.lcr)}</TableCell><TableCell className={cn('text-right', SCENARIO.lcr !== null && latestR.lcr !== null && SCENARIO.lcr < latestR.lcr && 'text-danger')}>{pct(SCENARIO.lcr)}</TableCell></TableRow>
                    </TableBody>
                  </Table>
                </div>
                <p className="mt-2 font-mono text-xs text-muted-foreground">RUN-SCN-0031-1 (mock) · synthetic snapshot {LATEST.snapshot} (unchanged) · deposit_shock v1 · reporting date {LATEST.date} · historical runs {PREVIOUS.id}, {LATEST.id} untouched</p>
              </PageSection>
            </div>
          </div>
        )}
      </div>

      <aside className="min-w-0 space-y-4">
        <PageSection icon={History} title="Audit trail">
          {audit.length === 0 ? (
            <p className="text-sm text-muted-foreground">Start the investigation to see each step recorded.</p>
          ) : (
            <ul className="max-h-[32rem] space-y-2 overflow-auto text-xs">
              {audit.map((a, i) => (
                <li key={i}><span className="font-mono text-muted-foreground">{a.ts}</span><br />{a.msg}</li>
              ))}
            </ul>
          )}
        </PageSection>
        <PageSection icon={ShieldCheck} title="Design principles (target workflow)">
          <ul className="list-disc space-y-1 pl-4 text-xs text-muted-foreground">
            <li>This page is a mock: browser arithmetic on synthetic data</li>
            <li>The calculation engine is the only source of financial results</li>
            <li>The agent plans and explains; it cannot invent numbers or change rules</li>
            <li>Every conclusion traces to authorised evidence</li>
            <li>Historical runs are immutable; scenario runs are separate</li>
            <li>Approval depends on the action: read-only investigation, draft, or execution</li>
          </ul>
        </PageSection>
      </aside>
    </div>
  )
}

function Stepper({ stage, withShock }: { stage: number; withShock: boolean }) {
  return (
    <ol className="flex flex-wrap gap-2">
      {STEPS.map((s, i) => {
        const n = i + 1
        const skipped = !withShock && n >= 5
        const done = stage > n || (stage === 7 && n === 7) || (!withShock && stage >= 4 && n <= 4)
        const active = stage === n && !done
        return (
          <li key={s} className={cn('flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs', done ? 'border-[hsl(var(--success)/0.3)] bg-[hsl(var(--success)/0.1)] text-success' : active ? 'border-primary bg-primary/10 text-primary' : 'border-border text-muted-foreground', skipped && 'opacity-40')}>
            <span className="font-semibold">{n}</span>{s}
          </li>
        )
      })}
    </ol>
  )
}

function RunCard({ label, run, r }: { label: string; run: typeof PREVIOUS; r: LcrResult }) {
  return (
    <div className="rounded-md border border-border p-3 text-sm">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</span>
        <Badge variant="secondary">Synthetic run</Badge>
      </div>
      <div className="mt-1 font-mono text-xs">{run.id} · {run.date}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums">{pct(r.lcr)}</div>
      <div className="text-xs text-muted-foreground">synthetic snapshot {run.snapshot}</div>
      <div className="text-xs text-muted-foreground">{run.rules}</div>
    </div>
  )
}

function Layer({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-3">
      <span className="mt-0.5 flex h-5 w-5 flex-none items-center justify-center rounded-full bg-primary/10 text-[11px] font-semibold text-primary">{n}</span>
      <div className="min-w-0 flex-1"><div className="font-semibold">{title}</div><div>{children}</div></div>
    </div>
  )
}

function MeaningOption({ title, note, selected, disabled, onClick }: { title: string; note: string; selected: boolean; disabled?: boolean; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} disabled={disabled} className={cn('rounded-md border p-3 text-left text-sm transition-colors', selected ? 'border-primary bg-primary/5' : 'border-border hover:border-primary/60')}>
      <div className="font-semibold">{title}</div>
      <div className="text-xs text-muted-foreground">{note}</div>
    </button>
  )
}

function ScenarioStates({ state }: { state: ScenarioState | null }) {
  const at = state ? SCENARIO_STEPS.findIndex((s) => s.id === state) : -1
  return (
    <div className="flex flex-wrap items-center gap-1 text-[11px]">
      {SCENARIO_STEPS.map((s, i) => (
        <span key={s.id} className="flex items-center gap-1">
          <span className={cn('rounded px-1.5 py-0.5 font-mono', i < at ? 'bg-[hsl(var(--success)/0.1)] text-success' : i === at ? 'bg-primary text-primary-foreground' : 'bg-surface-2 text-muted-foreground')}>{s.label}</span>
          {i < SCENARIO_STEPS.length - 1 && <span className="text-muted-foreground">→</span>}
        </span>
      ))}
    </div>
  )
}

function Waterfall() {
  const W = 720, H = 220, pl = 64, pr = 12, pt = 16, pb = 44
  const start = prevR.lcr
  const end = latestR.lcr
  if (start === null || end === null || BRIDGE.steps.some((s) => s.from === null || s.to === null)) {
    return <p className="text-sm text-muted-foreground">Attribution chart {NOT_COMPUTABLE}: at least one run has no positive net outflow.</p>
  }
  const bars = [
    { label: 'Previous LCR', from: 0, to: start, kind: 'total' as const },
    ...BRIDGE.steps.map((s) => ({ label: s.label.replace(' (numerator)', ''), from: s.from as number, to: s.to as number, kind: 'step' as const })),
    { label: 'Latest LCR', from: 0, to: end, kind: 'total' as const },
  ]
  const positives = bars.map((b) => Math.min(b.from, b.to)).filter((v) => v > 0)
  const lo = (positives.length ? Math.min(...positives) : 0) - 50000
  const hi = Math.max(...bars.map((b) => Math.max(b.from, b.to))) + 50000
  // Zero guard on the scale ratio.
  const y = (v: number) => pt + (ratio(hi - Math.max(v, lo), hi - lo) ?? 0) * (H - pt - pb)
  const gw = (W - pl - pr) / bars.length
  const bw = Math.min(54, gw * 0.6)
  const ticks = [hi, (hi + lo) / 2, lo]
  return (
    <div className="overflow-x-auto">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full min-w-[560px]" role="img" aria-label="LCR attribution waterfall">
        {ticks.map((t, i) => (
          <g key={i}>
            <line x1={pl} x2={W - pr} y1={y(t)} y2={y(t)} className="stroke-border" />
            <text x={pl - 6} y={y(t) + 4} textAnchor="end" fontSize="11" className="fill-muted-foreground">{(t / 10000).toFixed(0)}%</text>
          </g>
        ))}
        {bars.map((b, i) => {
          const x = pl + gw * i + (gw - bw) / 2
          const top = b.kind === 'total' ? y(b.to) : y(Math.max(b.from, b.to))
          const bottom = b.kind === 'total' ? y(lo) : y(Math.min(b.from, b.to))
          const delta = b.to - b.from
          const cls = b.kind === 'total' ? 'fill-primary' : delta < 0 ? 'fill-danger' : delta > 0 ? 'fill-success' : 'fill-muted-foreground/40'
          const lines = b.label.split(' ')
          return (
            <g key={b.label}>
              <rect x={x} y={top} width={bw} height={Math.max(2, bottom - top)} rx={2} className={cls} />
              <text x={x + bw / 2} y={top - 4} textAnchor="middle" fontSize="11" className="fill-foreground">{b.kind === 'total' ? `${(b.to / 10000).toFixed(2)}%` : pp(delta).replace(' pp', '')}</text>
              <text x={x + bw / 2} y={H - pb + 14} textAnchor="middle" fontSize="10" className="fill-muted-foreground">
                <tspan x={x + bw / 2}>{lines.slice(0, 2).join(' ')}</tspan>
                {lines.length > 2 && <tspan x={x + bw / 2} dy="12">{lines.slice(2).join(' ')}</tspan>}
              </text>
            </g>
          )
        })}
      </svg>
    </div>
  )
}
