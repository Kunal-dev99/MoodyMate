'use client'

import Link from 'next/link'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { AlertTriangle, Droplets, FlaskConical } from 'lucide-react'
import { ApiError, api, call } from '@/api/client'
import { Forbidden } from '@/components/auth/Forbidden'
import { EmptyState } from '@/components/common/EmptyState'
import { LoadFailed } from '@/components/common/LoadFailed'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { Masked, isMasked } from '@/components/common/Masked'
import { PageSection } from '@/components/common/PageSection'
import { createLoader, type Loaded } from '@/components/common/load'
import { ShownValue } from '@/components/domain/ShownValue'
import { importHref, instrumentHref } from '@/components/domain/domain'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { IllustrativeBanner } from './IllustrativeBanner'
import { ResultContextPanel } from './ResultContextPanel'
import { RunIdentity } from './RunIdentity'
import {
  EXCLUSION_TEXT,
  HQLA_LEVEL_TEXT,
  LCR_WARNING_TEXT,
  hasResults,
  hqlaAssetCount,
  lcrMaskHint,
  type HqlaAsset,
  type HqlaLevelTotal,
  type LcrCurrency,
  type Liquidity,
  type RunDetail,
} from './calculations'

/** GET …/liquidity; null for a run of another module (404 `liquidity_not_found`). */
export const fetchLiquidity = (runId: string) => (signal: AbortSignal) =>
  call(() => api.GET('/api/v1/calculation-runs/{run_id}/liquidity', { params: { path: { run_id: runId } }, signal })).catch((err: unknown) => {
    if (err instanceof ApiError && err.status === 404 && err.envelope.code === 'liquidity_not_found') return null
    throw err
  })

const COLUMNS = 10

/**
 * One LCR aggregate as the server sent it (FE-17.1): exact decimal text, or the "masked" marker
 * with why (D-33 minimum aggregation). `count` is the figure's own contribution count, or null
 * for a figure derived from others.
 */
export function LcrFigure({ value, count, min, testId }: { value: HqlaAsset['balance']; count: number | null; min: number; testId: string }) {
  const hint = lcrMaskHint(value, count, min)
  return (
    <span className="inline-flex flex-col">
      <ShownValue value={value} testId={testId} money />
      {hint && (
        <span className="mt-0.5 text-[11px] leading-tight text-muted-foreground" data-testid={`${testId}-hint`}>
          {hint}
        </span>
      )}
    </span>
  )
}

/**
 * The ratio as the server stated it (FE-17.1): its exact text; "masked" with why; or, when the
 * server computed none (net outflows zero or negative), "not computable" with the
 * LCR_DENOMINATOR_ZERO warning. The ratio is never derived here.
 */
export function LcrRatio({ row, min }: { row: Pick<LcrCurrency, 'ratio'>; min: number }) {
  const ratio = row.ratio
  if (ratio === null)
    return (
      <span className="inline-flex flex-col" data-testid="lcr-ratio" data-computable="false">
        <span className="font-medium text-[hsl(var(--warning))]">not computable</span>
        <span className="mt-0.5 text-[11px] leading-tight text-muted-foreground" data-testid="lcr-ratio-warning" data-code="LCR_DENOMINATOR_ZERO">
          <span className="font-mono">LCR_DENOMINATOR_ZERO</span>: {LCR_WARNING_TEXT.LCR_DENOMINATOR_ZERO}
        </span>
      </span>
    )
  return <LcrFigure value={ratio} count={null} min={min} testId="lcr-ratio" />
}

/** Whether the inflow cap binds, as the server said (masked when the figures it compares are). */
function CapApplied({ applied }: { applied: LcrCurrency['inflow_cap_applied'] }) {
  return (
    <Masked value={applied} cell testId="lcr-cap-applied" attrs={{ 'data-applied': 'masked' }}>
      {(a) =>
        a ? (
          <Badge variant="warning" data-testid="lcr-cap-applied" data-applied="true">
            cap applied
          </Badge>
        ) : (
          <span data-testid="lcr-cap-applied" data-applied="false">
            cap not binding
          </span>
        )
      }
    </Masked>
  )
}

function Tile({ label, children, note, testId }: { label: string; children: ReactNode; note?: ReactNode; testId?: string }) {
  return (
    <div className="rounded-md border border-border bg-surface-2 px-3 py-2" data-testid={testId}>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="text-sm">{children}</dd>
      {note && <dd className="mt-1 text-[11px] leading-tight text-muted-foreground">{note}</dd>}
    </div>
  )
}

function AssetRow({ a }: { a: HqlaAsset }) {
  const l = a.lineage
  return (
    <TableRow
      data-testid="hqla-asset"
      data-id={a.hqla_asset_result_id}
      data-included={a.included}
      data-level={a.level ?? ''}
      data-masked={isMasked(a.balance) || isMasked(a.hqla_value)}
      data-classification={a.classification}
    >
      <TableCell className="font-mono text-xs">{a.sequence}</TableCell>
      <TableCell>
        <Badge variant={a.included ? 'success' : 'outline'} data-testid="hqla-asset-status">
          {a.included ? 'Included' : 'Excluded'}
        </Badge>
      </TableCell>
      <TableCell className="text-xs">
        <ShownValue value={l.position_business_key} testId="hqla-asset-position" />
        <div className="font-mono text-muted-foreground" title="Position version">
          {l.position_version_id}
        </div>
      </TableCell>
      <TableCell className="text-xs">
        {l.instrument_id ? (
          <Link href={instrumentHref(l.instrument_id)} className="font-mono text-primary hover:underline" data-testid="hqla-asset-instrument-link">
            {isMasked(l.instrument_business_key) ? `${l.instrument_id.slice(0, 8)}…` : l.instrument_business_key}
          </Link>
        ) : (
          !isMasked(l.instrument_business_key) && <span className="font-mono">{l.instrument_business_key}</span>
        )}
        {isMasked(l.instrument_business_key) && (
          <span className="ml-1">
            <Masked value={l.instrument_business_key} />
          </span>
        )}
        <div className="text-muted-foreground">
          <ShownValue value={a.instrument_type} testId="hqla-asset-type" />
        </div>
      </TableCell>
      <TableCell className="text-xs">{a.level ? <span className="font-mono">{a.level}</span> : <span className="text-muted-foreground">—</span>}</TableCell>
      <TableCell className="text-right text-sm">
        <ShownValue value={a.balance} testId="hqla-asset-balance" money />
      </TableCell>
      <TableCell className="text-right text-sm">
        {a.haircut === null ? <span className="text-muted-foreground">—</span> : <span className="font-mono" data-testid="hqla-asset-haircut">{a.haircut}</span>}
      </TableCell>
      <TableCell className="text-right text-sm">
        <ShownValue value={a.hqla_value} testId="hqla-asset-hqla" money />
      </TableCell>
      <TableCell className="text-xs" data-testid="hqla-asset-reason" data-reason={a.exclusion_reason ?? ''}>
        {a.included ? (
          <span className="text-muted-foreground">Included: {a.level ? HQLA_LEVEL_TEXT[a.level] : 'eligible'} under the pinned rules</span>
        ) : a.exclusion_reason ? (
          <>
            <span className="font-mono">{a.exclusion_reason}</span>
            <div className="text-muted-foreground">{EXCLUSION_TEXT[a.exclusion_reason]}</div>
          </>
        ) : (
          <span className="text-muted-foreground">—</span>
        )}
      </TableCell>
      <TableCell className="text-xs">
        <Link href={importHref(l.import_id)} className="font-mono text-primary hover:underline" data-testid="hqla-asset-import-link" data-import-id={l.import_id}>
          Import {l.import_id.slice(0, 8)} · row {l.source_ordinal}
        </Link>
      </TableCell>
    </TableRow>
  )
}

function LevelRows({ level, assets, min }: { level: HqlaLevelTotal; assets: HqlaAsset[]; min: number }) {
  return (
    <>
      <TableRow className="bg-surface-2" data-testid="hqla-level" data-level={level.level} data-count={level.asset_count}>
        <TableCell colSpan={7} className="text-xs font-medium">
          {HQLA_LEVEL_TEXT[level.level]} <span className="font-mono text-muted-foreground">{level.level}</span> · {level.asset_count} included asset
          {level.asset_count === 1 ? '' : 's'}
        </TableCell>
        <TableCell className="text-right text-sm">
          <LcrFigure value={level.total} count={level.asset_count} min={min} testId="hqla-level-total" />
        </TableCell>
        <TableCell colSpan={2} />
      </TableRow>
      {assets.map((a) => (
        <AssetRow key={a.hqla_asset_result_id} a={a} />
      ))}
    </>
  )
}

/**
 * One currency's LCR (FE-17.1): the figures (HQLA, outflows, inflows before and after the cap
 * with the applied cap shown, net outflows and the ratio, or "not computable" with the
 * warning), then the HQLA components: each level with its included asset rows, and the excluded
 * rows with their reason. Every figure is the server's text (masked under D-33 when the caller is
 * not cleared); nothing is added up here.
 */
export function LcrCurrencyView({ row, min, horizonDays }: { row: LcrCurrency; min: number; horizonDays: number | null }) {
  const excluded = row.assets.filter((a) => !a.included)
  return (
    <div className="space-y-3" data-testid="lcr-currency" data-currency={row.currency} data-result-id={row.lcr_result_id}>
      <h3 className="font-mono text-sm font-medium">{row.currency}</h3>
      <dl className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6" data-testid="lcr-figures">
        <Tile label="HQLA (after haircuts)" note="L1 + L2A + L2B: the included asset rows">
          <LcrFigure value={row.hqla_total} count={hqlaAssetCount(row)} min={min} testId="lcr-hqla" />
        </Tile>
        <Tile
          label="Outflows (run-off)"
          note={
            <>
              balance × run-off over {row.outflow_count} position{row.outflow_count === 1 ? '' : 's'}; balance{' '}
              <LcrFigure value={row.outflow_balance} count={row.outflow_count} min={min} testId="lcr-outflow-balance" />
            </>
          }
        >
          <LcrFigure value={row.outflows} count={row.outflow_count} min={min} testId="lcr-outflows" />
        </Tile>
        <Tile
          label="Inflows before the cap"
          note={
            <>
              amount × inflow rate over {row.inflow_count} cash flow{row.inflow_count === 1 ? '' : 's'}
              {horizonDays !== null && ` due within ${horizonDays} days`}; gross{' '}
              <LcrFigure value={row.inflows_gross} count={row.inflow_count} min={min} testId="lcr-inflows-gross" />
            </>
          }
        >
          <LcrFigure value={row.inflows_before_cap} count={row.inflow_count} min={min} testId="lcr-inflows-before-cap" />
        </Tile>
        <Tile
          label="Inflow cap"
          testId="lcr-inflow-cap-tile"
          note={
            <>
              <span className="font-mono" data-testid="lcr-cap-rate">
                {row.inflow_cap_rate}
              </span>{' '}
              × outflows
            </>
          }
        >
          <LcrFigure value={row.inflow_cap_amount} count={null} min={min} testId="lcr-inflow-cap" />
        </Tile>
        <Tile label="Inflows after the cap" note={<CapApplied applied={row.inflow_cap_applied} />}>
          <LcrFigure value={row.inflows_after_cap} count={null} min={min} testId="lcr-inflows" />
        </Tile>
        <Tile label="Ratio (HQLA / net outflows)" note={
          <>
            net outflows (outflows − capped inflows){' '}
            <LcrFigure value={row.net_outflows} count={null} min={min} testId="lcr-net-outflows" />
          </>
        }>
          <LcrRatio row={row} min={min} />
        </Tile>
      </dl>
      {row.warnings.length > 0 && (
        <ul className="space-y-1 text-xs" data-testid="lcr-warnings">
          {row.warnings.map((code) => (
            <li key={code} className="flex items-start gap-1.5" data-testid="lcr-warning" data-code={code}>
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[hsl(var(--warning))]" />
              <span>
                <span className="font-mono">{code}</span> <span className="text-muted-foreground">{LCR_WARNING_TEXT[code]}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
      <Table data-testid="hqla-table" data-currency={row.currency}>
        <TableHeader>
          <TableRow>
            <TableHead className="w-12">#</TableHead>
            <TableHead>HQLA component</TableHead>
            <TableHead>Position</TableHead>
            <TableHead>Instrument and type</TableHead>
            <TableHead>Level</TableHead>
            <TableHead className="text-right">Balance</TableHead>
            <TableHead className="text-right">Haircut</TableHead>
            <TableHead className="text-right">HQLA value</TableHead>
            <TableHead>Inclusion or exclusion reason</TableHead>
            <TableHead>Import and source row</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {row.hqla_levels.map((level) => (
            <LevelRows key={level.level} level={level} assets={row.assets.filter((a) => a.included && a.level === level.level)} min={min} />
          ))}
          <TableRow className="bg-surface-2" data-testid="hqla-excluded" data-count={row.excluded_asset_count}>
            <TableCell colSpan={COLUMNS} className="text-xs font-medium">
              Excluded from HQLA · {row.excluded_asset_count} asset{row.excluded_asset_count === 1 ? '' : 's'}
            </TableCell>
          </TableRow>
          {excluded.map((a) => (
            <AssetRow key={a.hqla_asset_result_id} a={a} />
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

/** The prototype label of the LCR (D-35 §2–3), with the module's documented limitations as the server lists them. */
export function LcrPrototypeNotice({ limitations }: { limitations: string[] }) {
  return (
    <Alert variant="warning" data-testid="lcr-prototype-banner">
      <FlaskConical className="h-4 w-4" />
      <AlertTitle>LCR/HQLA prototype — not a regulatory LCR</AlertTitle>
      <AlertDescription className="text-foreground">
        <p className="text-xs">
          Per currency with no FX; the value is the position balance; parameters are seeded, illustrative and provisional. Not for client
          decisions.
        </p>
        {limitations.length > 0 && (
          <ul className="mt-1 list-disc space-y-0.5 pl-4 text-xs" data-testid="lcr-limitations">
            {limitations.map((t) => (
              <li key={t} data-testid="lcr-limitation">
                {t}
              </li>
            ))}
          </ul>
        )}
      </AlertDescription>
    </Alert>
  )
}

/**
 * FE-17.1: the liquidity view of an `lcr_hqla` run, per currency, with the run's identity and
 * the prototype label. Results exist only for a run the server ended SUCCEEDED or
 * SUCCEEDED_WITH_WARNINGS; otherwise nothing here is a result.
 */
export function LiquidityView({ run }: { run: RunDetail }) {
  const [state, setState] = useState<Loaded<Liquidity | null>>({ kind: 'loading' })
  const loader = useMemo(() => createLoader(fetchLiquidity(run.run_id), setState, 'Could not load the liquidity view'), [run.run_id])

  useEffect(() => {
    loader.load()
    return () => loader.abort()
  }, [loader])

  let body: ReactNode
  let liquidity: Liquidity | null = null
  if (state.kind === 'loading') body = <LoadingBlock rows={4} />
  else if (state.kind === 'forbidden') body = <Forbidden permission="calc:read" correlationId={state.cid} />
  else if (state.kind === 'failed') body = <LoadFailed icon={Droplets} title="The liquidity view could not be loaded" cid={state.cid} onRetry={loader.load} />
  else if (state.kind === 'not_found')
    body = (
      <EmptyState
        icon={Droplets}
        title="The liquidity view could not be read"
        description={`HTTP ${state.status ?? '—'}.${state.cid ? ` Correlation ID: ${state.cid}` : ''}`}
      />
    )
  else if (state.value === null)
    body = (
      <p className="text-sm text-muted-foreground" data-testid="liquidity-not-applicable">
        This run&apos;s module has no liquidity view.
      </p>
    )
  else {
    const value = state.value
    liquidity = value
    body =
      !hasResults(value.status) || value.currencies.length === 0 ? (
        <p className="text-sm text-muted-foreground" data-testid="liquidity-none" data-status={value.status}>
          {hasResults(value.status)
            ? 'The run read no position or inflow, so there is no currency.'
            : 'No liquidity result: the server has not ended this run SUCCEEDED or SUCCEEDED_WITH_WARNINGS, so nothing here is a result.'}
        </p>
      ) : (
        <div className="space-y-8">
          {value.currencies.map((c) => (
            <LcrCurrencyView key={c.currency} row={c} min={value.min_aggregate_count} horizonDays={value.inflow_horizon_days} />
          ))}
          <p className="text-xs text-muted-foreground">
            Per currency, no FX. HQLA = Σ eligible balance × (1 − haircut); outflows = Σ balance × run-off; inflows = min(Σ inflow × rate, cap ×
            outflows); ratio = HQLA / (outflows − inflows), 4 dp ROUND_HALF_EVEN, not computable when that is zero or negative. Aggregates are{' '}
            <span className="font-mono">internal</span>, except a figure built from fewer than{' '}
            <span className="font-mono" data-testid="lcr-min-aggregate-count">
              {value.min_aggregate_count}
            </span>{' '}
            contributions: it takes their classification, and so does every figure derived from it; zero figures stay clear. Asset rows carry the
            classification of what they read.
          </p>
        </div>
      )
  }

  return (
    <PageSection
      icon={Droplets}
      title="Liquidity coverage (LCR/HQLA prototype)"
      description="HQLA components, run-off outflows and capped inflows per currency, and the ratio, under the pinned illustrative rules."
    >
      <div className="space-y-4" data-testid="liquidity" data-run-id={run.run_id}>
        <RunIdentity run={run} />
        <IllustrativeBanner statement={liquidity?.statement ?? run.statement} compact />
        {/* FE-30.1: the LCR read's own context block (D-56 §5), checked against the run's pins. */}
        {liquidity && <ResultContextPanel context={liquidity.context} pins={run} source="liquidity" compact />}
        <LcrPrototypeNotice limitations={liquidity?.limitations ?? []} />
        {body}
      </div>
    </PageSection>
  )
}
