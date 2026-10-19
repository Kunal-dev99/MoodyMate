'use client'

import { useEffect, useRef, useState } from 'react'
import { Bot, FileSearch, History, Play, ShieldCheck, Sparkles } from 'lucide-react'
import { PageSection } from '@/components/common/PageSection'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'
import {
  BASELINE, BUCKETS, EXAMPLES, TEMPLATES, applyShocks, describe, fingerprint, ladder, money, parseField, problems, propose, signed,
  type DemoShock, type FieldKey, type LadderRow, type Proposal,
} from './agentDemo'

interface Version {
  n: number
  shocks: DemoShock[]
  origin: string
  status: 'PROPOSED' | 'CONFIRMED' | 'RUN'
}
interface RunResult {
  version: number
  shocks: DemoShock[]
  stressed: LadderRow[]
  perShock: LadderRow[][]
  fp: string
}

const deltaClass = (n: number) => (n > 0 ? 'text-success font-semibold' : n < 0 ? 'text-danger font-semibold' : 'text-muted-foreground')
const stamp = () => `${new Date().toISOString().slice(11, 19)} UTC`

/**
 * Demo mode of the Agentic Stress-Test Explorer: plain-English request -> mock agent proposal ->
 * analyst review and confirm -> deterministic baseline-vs-stress ladder -> evidence-backed
 * explanation -> adapt into a new version. Synthetic data, no API calls; illustrative only.
 */
export function AgentDemo() {
  const [request, setRequest] = useState(EXAMPLES[0])
  const [trace, setTrace] = useState<Proposal['trace']>([])
  const [ask, setAsk] = useState<string | null>(null)
  const [thinking, setThinking] = useState(false)
  const [versions, setVersions] = useState<Version[]>([])
  const [run, setRun] = useState<RunResult | null>(null)
  const [audit, setAudit] = useState<{ ts: string; msg: string }[]>([])
  const timers = useRef<ReturnType<typeof setTimeout>[]>([])
  const resultsRef = useRef<HTMLDivElement>(null)
  const proposalRef = useRef<HTMLDivElement>(null)
  useEffect(() => () => timers.current.forEach(clearTimeout), [])

  const log = (msg: string) => setAudit((a) => [{ ts: stamp(), msg }, ...a])
  const current = versions[versions.length - 1]

  function addVersion(shocks: DemoShock[], origin: string) {
    setVersions((vs) => {
      const v: Version = { n: vs.length + 1, shocks: shocks.map((s) => ({ ...s, defaults: [...s.defaults] })), origin, status: 'PROPOSED' }
      log(`Scenario v${v.n} created (${origin}, ${shocks.length} shock${shocks.length > 1 ? 's' : ''})`)
      return [...vs, v]
    })
    setTimeout(() => proposalRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 50)
  }

  function onPropose() {
    const text = request.trim()
    if (!text) return
    const r = propose(text)
    log(`Analyst request: “${text}”`)
    setTrace([])
    setAsk(null)
    setThinking(true)
    r.trace.forEach((step, i) => {
      timers.current.push(setTimeout(() => setTrace((t) => [...t, step]), 400 * (i + 1)))
    })
    timers.current.push(
      setTimeout(() => {
        setThinking(false)
        if (r.ask) {
          setAsk(r.ask)
          log('Agent asked a clarifying question instead of guessing')
        } else addVersion(r.shocks, 'AI proposed')
      }, 400 * (r.trace.length + 1)),
    )
  }

  function edit(i: number, k: FieldKey, raw: string) {
    setVersions((vs) => {
      const last = vs[vs.length - 1]
      const shocks = last.shocks.map((s, j) => (j === i ? { ...s, [k]: parseField(k, raw), defaults: s.defaults.filter((d) => d !== k) } : s))
      return [...vs.slice(0, -1), { ...last, shocks }]
    })
    log(`Analyst edited v${current.n} shock ${i + 1}: ${k} = ${raw}`)
  }

  function onRun() {
    if (!current) return
    if (current.status === 'PROPOSED') log(`Analyst confirmed v${current.n} for exploration`)
    const stressed = ladder(applyShocks(current.shocks))
    const perShock = current.shocks.map((s) => ladder(applyShocks([s])))
    const fp = fingerprint(stressed)
    setVersions((vs) => [...vs.slice(0, -1), { ...current, status: 'RUN' }])
    setRun({ version: current.n, shocks: current.shocks, stressed, perShock, fp })
    log(`Run complete for v${current.n} against baseline snapshot SNAP-2026-12-20 · fingerprint ${fp}`)
    setTimeout(() => resultsRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 50)
  }

  const blocked = current ? current.shocks.some((s) => Object.keys(problems(s)).length > 0) : true

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_300px]">
      <div className="min-w-0 space-y-4">
        <PageSection icon={Bot} title="1. Ask the agent" description="Describe the stress in plain English. The agent maps it to the approved templates only: delayed loan repayments, deposit withdrawals, funding unavailable." accent="primary">
          <Textarea id="agent-request" data-testid="agent-request" value={request} onChange={(e) => setRequest(e.target.value)} rows={3} />
          <div className="mt-3 flex flex-wrap gap-2">
            {EXAMPLES.map((e) => (
              <Button key={e} type="button" variant="outline" size="sm" className="h-auto whitespace-normal py-1 text-left text-xs" onClick={() => setRequest(e)}>
                {e}
              </Button>
            ))}
          </div>
          <div className="mt-3 flex items-center gap-3">
            <Button type="button" onClick={onPropose} disabled={thinking} data-testid="agent-propose">
              <Sparkles className="mr-1 h-4 w-4" />
              {thinking ? 'Agent is planning…' : 'Propose scenario'}
            </Button>
          </div>
          {trace.length > 0 && (
            <ol className="mt-4 space-y-1.5 text-sm">
              {trace.map((s, i) => (
                <li key={i} className="flex items-start gap-2">
                  <Badge variant="info" className="mt-0.5 font-mono text-[10px]">{s.tag}</Badge>
                  <span>{s.text}</span>
                </li>
              ))}
            </ol>
          )}
          {ask && (
            <div className="mt-4 rounded-md border border-[hsl(var(--warning)/0.3)] bg-[hsl(var(--warning)/0.1)] p-3 text-sm">
              <b>Agent asks:</b> {ask}
            </div>
          )}
        </PageSection>

        <div ref={proposalRef}>
          <PageSection icon={FileSearch} title="2. Review the proposed scenario" description="Validated against template bounds before anything runs. Edit any value; the agent never confirms on your behalf.">
            {!current ? (
              <p className="text-sm text-muted-foreground">No proposal yet. Ask the agent in step 1.</p>
            ) : (
              <div className="space-y-3">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="secondary">v{current.n}</Badge>
                  <Badge variant="warning">{current.origin}</Badge>
                  <span className="text-xs text-muted-foreground">Exploration only, not approved for shared use</span>
                </div>
                {current.shocks.map((s, i) => {
                  const p = problems(s)
                  return (
                    <div key={i} className="rounded-md border border-border p-3">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span className="text-sm font-semibold">Shock {i + 1} · {TEMPLATES[s.t].name}</span>
                        <Badge variant="outline" className="font-mono text-[10px]">{s.t}</Badge>
                      </div>
                      <p className="mb-2 mt-1 text-sm text-muted-foreground">{Object.keys(p).length ? 'Fix the highlighted value to continue.' : describe(s)}</p>
                      <div className="flex flex-wrap gap-4">
                        {TEMPLATES[s.t].fields.map(([k, label]) => (
                          <div key={`${current.n}-${i}-${k}`} className="space-y-1">
                            <Label htmlFor={`f-${i}-${k}`} className="text-xs uppercase tracking-wide text-muted-foreground">{label}</Label>
                            <Input
                              id={`f-${i}-${k}`}
                              defaultValue={Number.isNaN(s[k]) ? '' : String(s[k])}
                              onBlur={(e) => edit(i, k, e.target.value)}
                              onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
                              className={cn('w-32 font-mono', p[k] && 'border-danger')}
                            />
                            {p[k] ? (
                              <p className="text-xs text-danger">{p[k]}</p>
                            ) : s.defaults.includes(k) ? (
                              <p className="text-xs text-warning">Proposed default, please confirm</p>
                            ) : null}
                          </div>
                        ))}
                      </div>
                    </div>
                  )
                })}
                <div className="flex flex-wrap items-center gap-3">
                  <Button type="button" onClick={onRun} disabled={blocked} data-testid="agent-run">
                    <Play className="mr-1 h-4 w-4" />
                    {current.status === 'PROPOSED' ? 'Confirm for exploration and run' : 'Run again'}
                  </Button>
                  {blocked && <Badge variant="destructive">Blocked by validation</Badge>}
                </div>
              </div>
            )}
          </PageSection>
        </div>

        <div ref={resultsRef}>
          <PageSection title="3. Baseline vs stress by time bucket" description="The original data is never changed. The engine applies the shocks to a scenario copy and recalculates the maturity ladder. ₹ lakh, one currency.">
            {run ? <Results run={run} /> : <p className="text-sm text-muted-foreground">Confirm a scenario to run it.</p>}
          </PageSection>
        </div>

        <PageSection title="4. Explanation with evidence" description="Written from the run output only. Each statement points to the cash flows that caused it.">
          {run ? (
            <Explanation run={run} onAdapt={(label, shocks) => { log(`Analyst asked: “${label}”`); addVersion(shocks, `Adapted from v${run.version}`) }} />
          ) : (
            <p className="text-sm text-muted-foreground">Appears after a run.</p>
          )}
        </PageSection>
      </div>

      <aside className="min-w-0 space-y-4">
        <PageSection icon={History} title="Scenario versions">
          {versions.length === 0 ? (
            <p className="text-sm text-muted-foreground">None yet.</p>
          ) : (
            <ul className="divide-y divide-border text-sm">
              {versions.map((v) => (
                <li key={v.n} className="flex items-center justify-between gap-2 py-2">
                  <span>
                    <b>v{v.n}</b> · {v.shocks.length} shock{v.shocks.length > 1 ? 's' : ''}
                    <br />
                    <span className="text-xs text-muted-foreground">{v.origin}</span>
                  </span>
                  <Badge variant={v.status === 'RUN' ? 'success' : v.status === 'CONFIRMED' ? 'info' : 'warning'}>
                    {v.status === 'RUN' ? 'Run complete' : v.status === 'CONFIRMED' ? 'Confirmed' : 'Proposed'}
                  </Badge>
                </li>
              ))}
            </ul>
          )}
        </PageSection>
        <PageSection title="Audit trail">
          {audit.length === 0 ? (
            <p className="text-sm text-muted-foreground">No activity yet.</p>
          ) : (
            <ul className="max-h-96 space-y-2 overflow-auto text-xs">
              {audit.map((a, i) => (
                <li key={i}>
                  <span className="font-mono text-muted-foreground">{a.ts}</span>
                  <br />
                  {a.msg}
                </li>
              ))}
            </ul>
          )}
        </PageSection>
        <PageSection icon={ShieldCheck} title="Controls in place">
          <ul className="list-disc space-y-1 pl-4 text-xs text-muted-foreground">
            <li>The agent selects from three approved templates only</li>
            <li>Bounds: 0 &lt; share ≤ 100%, delay 1–3650 days</li>
            <li>The agent has no database write access</li>
            <li>The analyst confirms every run; shared use needs an approver who is not the author</li>
            <li>Exact decimal arithmetic and a result fingerprint per run</li>
          </ul>
        </PageSection>
      </aside>
    </div>
  )
}

function Results({ run }: { run: RunResult }) {
  const S = run.stressed
  const worst = S.reduce((w, r, i) => (Math.abs(r.net - BASELINE[i].net) > Math.abs(S[w].net - BASELINE[w].net) ? i : w), 0)
  const low = (rows: LadderRow[]) => Math.min(...rows.map((r) => r.cumulative))
  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-3">
        <Kpi label="Cumulative gap at 30 days" value={signed(S[1].cumulative)} note={<>baseline {signed(BASELINE[1].cumulative)} · <span className={deltaClass(S[1].cumulative - BASELINE[1].cumulative)}>{signed(S[1].cumulative - BASELINE[1].cumulative)}</span></>} />
        <Kpi label="Lowest cumulative gap" value={signed(low(S))} note={<>baseline {signed(low(BASELINE))}</>} />
        <Kpi label="Most affected bucket" value={S[worst].label} note={<>net gap {signed(S[worst].net - BASELINE[worst].net)}</>} />
      </div>
      <Chart stressed={S} />
      <div className="overflow-x-auto">
        <Table className="tabular-nums">
          <TableHeader>
            <TableRow>
              <TableHead>Bucket</TableHead>
              <TableHead className="text-right">Inflows base</TableHead>
              <TableHead className="text-right">Inflows stress</TableHead>
              <TableHead className="text-right">Outflows base</TableHead>
              <TableHead className="text-right">Outflows stress</TableHead>
              <TableHead className="text-right">Net base</TableHead>
              <TableHead className="text-right">Net stress</TableHead>
              <TableHead className="text-right">Δ net</TableHead>
              <TableHead className="text-right">Cumulative stress</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {S.map((r, i) => {
              const b = BASELINE[i]
              return (
                <TableRow key={r.label} className={cn(i === worst && 'bg-primary/5')}>
                  <TableCell className="font-medium">{r.label}</TableCell>
                  <TableCell className="text-right">{money(b.inflows)}</TableCell>
                  <TableCell className="text-right">{money(r.inflows)}</TableCell>
                  <TableCell className="text-right">{money(b.outflows)}</TableCell>
                  <TableCell className="text-right">{money(r.outflows)}</TableCell>
                  <TableCell className="text-right">{signed(b.net)}</TableCell>
                  <TableCell className="text-right">{signed(r.net)}</TableCell>
                  <TableCell className={cn('text-right', deltaClass(r.net - b.net))}>{signed(r.net - b.net)}</TableCell>
                  <TableCell className="text-right">{signed(r.cumulative)}</TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      </div>
      <div>
        <h4 className="mb-1 text-sm font-semibold">Impact of each shock on its own (Δ net gap)</h4>
        <div className="overflow-x-auto">
          <Table className="tabular-nums">
            <TableHeader>
              <TableRow>
                <TableHead>Shock</TableHead>
                {BUCKETS.map((b) => <TableHead key={b.label} className="text-right">{b.label}</TableHead>)}
              </TableRow>
            </TableHeader>
            <TableBody>
              {run.shocks.map((s, j) => (
                <TableRow key={j}>
                  <TableCell>Shock {j + 1} · {TEMPLATES[s.t].name}</TableCell>
                  {run.perShock[j].map((r, i) => (
                    <TableCell key={i} className={cn('text-right', deltaClass(r.net - BASELINE[i].net))}>{signed(r.net - BASELINE[i].net)}</TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </div>
      <p className="font-mono text-xs text-muted-foreground">
        Scenario v{run.version} · baseline snapshot SNAP-2026-12-20 · maturity ladder v1 · result fingerprint {run.fp} · re-running reproduces it exactly
      </p>
    </div>
  )
}

function Kpi({ label, value, note }: { label: string; value: string; note: React.ReactNode }) {
  return (
    <div className="rounded-md border border-border p-3">
      <div className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="text-lg font-semibold tabular-nums">{value}</div>
      <div className="text-xs text-muted-foreground">{note}</div>
    </div>
  )
}

function Chart({ stressed }: { stressed: LadderRow[] }) {
  const W = 720, H = 230, pl = 56, pr = 10, pt = 12, pb = 36
  const vals = stressed.flatMap((r, i) => [r.net, BASELINE[i].net])
  const mx = Math.max(...vals, 0), mn = Math.min(...vals, 0)
  const span = mx - mn || 1
  const y = (v: number) => pt + ((mx - v) / span) * (H - pt - pb)
  const gw = (W - pl - pr) / stressed.length
  const bw = Math.min(28, gw / 3)
  const ticks = [mx, Math.round((mx + mn) / 2), mn]
  return (
    <div>
      <div className="overflow-x-auto">
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full min-w-[520px]" role="img" aria-label="Net gap by bucket, baseline vs stressed">
          {ticks.map((t, i) => (
            <g key={i}>
              <line x1={pl} x2={W - pr} y1={y(t)} y2={y(t)} className="stroke-border" />
              <text x={pl - 6} y={y(t) + 4} textAnchor="end" fontSize="11" className="fill-muted-foreground">{money(t)}</text>
            </g>
          ))}
          <line x1={pl} x2={W - pr} y1={y(0)} y2={y(0)} className="stroke-muted-foreground" />
          {stressed.map((r, i) => {
            const cx = pl + gw * i + gw / 2
            const bars: [number, string, number][] = [[BASELINE[i].net, 'fill-muted-foreground/50', -bw - 2], [r.net, 'fill-primary', 2]]
            return (
              <g key={r.label}>
                {bars.map(([v, cls, o], k) => (
                  <rect key={k} x={cx + o} y={Math.min(y(0), y(v))} width={bw} height={Math.max(1, Math.abs(y(v) - y(0)))} rx={2} className={cls}>
                    <title>{money(v)}</title>
                  </rect>
                ))}
                <text x={cx} y={H - pb + 16} textAnchor="middle" fontSize="11" className="fill-muted-foreground">{r.label}</text>
              </g>
            )
          })}
        </svg>
      </div>
      <div className="mt-1 flex gap-4 text-xs text-muted-foreground">
        <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm bg-muted-foreground/50" />Baseline net gap</span>
        <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm bg-primary" />Stressed net gap</span>
      </div>
    </div>
  )
}

function Explanation({ run, onAdapt }: { run: RunResult; onAdapt: (label: string, shocks: DemoShock[]) => void }) {
  const S = run.stressed
  const deltas = S.map((r, i) => ({ i, d: r.net - BASELINE[i].net })).filter((x) => x.d !== 0).sort((a, b) => Math.abs(b.d) - Math.abs(a.d))
  const changed = applyShocks(run.shocks).filter((f) => f.effects.length)
  const timingOnly = run.shocks.every((s) => s.t !== 'DEPOSIT_WITHDRAWAL')
  const total = S.reduce((a, r) => a + r.net, 0) - BASELINE.reduce((a, r) => a + r.net, 0)
  const base = run.shocks
  const adapts: [string, () => DemoShock[]][] = []
  const di = base.findIndex((s) => s.t === 'DELAYED_INFLOWS')
  if (di >= 0) adapts.push(['What if the delay were 30 days?', () => base.map((s, j) => (j === di ? { ...s, days: 30, defaults: [] } : { ...s }))])
  if (!base.some((s) => s.t === 'DEPOSIT_WITHDRAWAL')) adapts.push(['Add 15% higher deposit withdrawals', () => [...base.map((s) => ({ ...s })), { t: 'DEPOSIT_WITHDRAWAL', fraction: 15, defaults: [] }]])
  if (!base.some((s) => s.t === 'FUNDING_UNAVAILABLE')) adapts.push(['Interbank line unavailable for 7 days', () => [...base.map((s) => ({ ...s })), { t: 'FUNDING_UNAVAILABLE', fraction: 100, days: 7, defaults: [] }]])

  if (!deltas.length) return <p className="text-sm">No bucket changed: the shocks matched cash flows but did not move any across a bucket boundary.</p>
  const top = deltas[0]
  return (
    <div className="space-y-3 text-sm">
      <p>
        The largest change is in <b>{S[top.i].label}</b>, where the net gap moves by <b>{signed(top.d)}</b> ₹ lakh.{' '}
        {timingOnly
          ? 'The scenario changes when money arrives, not how much: total net flow across all buckets is unchanged.'
          : `Total net flow across all buckets changes by ${signed(total)} ₹ lakh, because higher deposit withdrawals are real extra outflows.`}{' '}
        The 30-day cumulative gap moves from {signed(BASELINE[1].cumulative)} to {signed(S[1].cumulative)}.
      </p>
      <ul className="list-disc space-y-1 pl-5">
        {deltas.slice(0, 3).map((x) => (
          <li key={x.i}>
            <b>{S[x.i].label}</b>: net gap {signed(BASELINE[x.i].net)} → {signed(S[x.i].net)} ({signed(x.d)}). Caused by{' '}
            {base
              .map((s, j) => ({ j, d: run.perShock[j][x.i].net - BASELINE[x.i].net }))
              .filter((c) => c.d !== 0)
              .map((c) => `shock ${c.j + 1} (${TEMPLATES[base[c.j].t].name.toLowerCase()}) ${signed(c.d)}`)
              .join(', ')}
            .
          </li>
        ))}
      </ul>
      <div>
        <p className="text-xs text-muted-foreground">Evidence: the cash flows the engine changed (scenario copy only; source records untouched)</p>
        <ul className="mt-1 space-y-0.5 pl-4 font-mono text-xs text-muted-foreground">
          {changed.map((f) => (
            <li key={f.id}>{f.id} · {f.desc} · {f.effects.map((e) => `shock ${e.shock + 1}: ${e.text}`).join('; ')}</li>
          ))}
        </ul>
      </div>
      <p className="text-xs text-muted-foreground">Every figure is read from run fingerprint {run.fp}. The agent did not compute any number.</p>
      {adapts.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 border-t border-border pt-3">
          <span className="text-xs text-muted-foreground">Adapt:</span>
          {adapts.map(([label, make]) => (
            <Button key={label} type="button" variant="outline" size="sm" onClick={() => onAdapt(label, make())}>{label}</Button>
          ))}
        </div>
      )}
    </div>
  )
}
