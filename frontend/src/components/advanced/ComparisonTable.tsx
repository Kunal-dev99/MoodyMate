'use client'

import { Fragment, useState } from 'react'
import { AlertTriangle, ChevronDown, ChevronRight, TrendingDown } from 'lucide-react'
import { MaskedFlag, isMasked } from '@/components/common/Masked'
import { ShownValue } from '@/components/domain/ShownValue'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { cn } from '@/lib/utils'
import { DeltaValue } from './DeltaValue'
import {
  FIGURE_LABEL,
  bucketDays,
  columnLabel,
  combinedOnlyUnknown,
  heatLevel,
  heatScale,
  subsetKey,
  toneOf,
  turnsNegative,
  type ComparisonBucket,
  type ComparisonCell,
  type ComparisonCurrency,
  type DrillTarget,
  type FigureKey,
  type Metric,
  type Shown,
} from './comparison'

/*
 * FE-25.2 (split out of ComparisonView.tsx by FE-35.2, unchanged): one currency's
 * currency × bucket heat map. Every figure is the server's, masked by the server.
 */

// Static class lists (Tailwind JIT): heat shades 1..4 for a fall (stress) and a rise.
const HEAT_NEGATIVE = ['', 'bg-destructive/10', 'bg-destructive/20', 'bg-destructive/30', 'bg-destructive/40']
const HEAT_POSITIVE = ['', 'bg-success/10', 'bg-success/20', 'bg-success/30', 'bg-success/40']

/**
 * One heat-map cell: the delta of the metric shown, shaded by its exact magnitude against the
 * currency's largest clear delta, with the server's turns-negative flag. A `null` flag reads a
 * masked figure: shown as unknown, never as "no".
 */
export function DeltaCell({
  cell,
  metric,
  max,
  active,
  onDrill,
}: {
  cell: ComparisonCell
  metric: Metric
  max: Shown | null
  active?: boolean
  onDrill?: () => void
}) {
  const delta = cell[metric].delta
  const tone = toneOf(delta)
  const level = heatLevel(delta, max)
  const flag = turnsNegative(cell, metric)
  return (
    <TableCell
      className={cn('text-right align-top text-sm', tone === 'negative' && HEAT_NEGATIVE[level], tone === 'positive' && HEAT_POSITIVE[level], active && 'ring-2 ring-inset ring-primary')}
      data-testid="comparison-cell"
      data-run-id={cell.calculation_run_id}
      data-subset={subsetKey(cell.shock_subset)}
      data-combined={cell.combined}
      data-metric={metric}
      data-delta={isMasked(delta) ? 'masked' : delta}
      data-heat={level}
      data-turns-negative={flag}
      data-changed={cell.changed_flow_count}
    >
      <DeltaValue value={delta} />
      <div className="mt-1 flex flex-wrap items-center justify-end gap-1">
        {flag === 'yes' && (
          <Badge variant="destructive" className="px-1.5 py-0 text-[10px]" data-testid="turns-negative">
            <TrendingDown className="mr-1 h-3 w-3" />
            Turns negative
          </Badge>
        )}
        {flag === 'unknown' && (
          <MaskedFlag flag="turns negative" className="text-[10px] text-muted-foreground" testId="turns-negative-unknown" />
        )}
        {onDrill && cell.changed_flow_count > 0 && (
          <Button size="sm" variant={active ? 'secondary' : 'ghost'} className="h-6 px-1.5 text-[11px]" onClick={onDrill} data-testid="comparison-drill">
            {cell.changed_flow_count} changed {cell.changed_flow_count === 1 ? 'flow' : 'flows'}
          </Button>
        )}
      </div>
    </TableCell>
  )
}

const FIGURES: readonly FigureKey[] = ['inflows', 'outflows', 'net_gap', 'cumulative_gap']

/** On expand: baseline, stressed and delta of every figure under every member run. */
function ExpandedFigures({ cells }: { cells: ComparisonCell[] }) {
  return (
    <div className="overflow-x-auto" data-testid="comparison-expanded">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Figure</TableHead>
            {cells.map((c) => (
              <TableHead key={c.calculation_run_id} colSpan={3} className="text-center">
                {columnLabel(c)}
              </TableHead>
            ))}
          </TableRow>
          <TableRow>
            <TableHead />
            {cells.map((c) => (
              <Fragment key={c.calculation_run_id}>
                <TableHead className="text-right text-xs">Baseline</TableHead>
                <TableHead className="text-right text-xs">Stressed</TableHead>
                <TableHead className="text-right text-xs">Δ</TableHead>
              </Fragment>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {FIGURES.map((f) => (
            <TableRow key={f} data-testid="comparison-figure" data-figure={f}>
              <TableCell className="text-xs">{FIGURE_LABEL[f]}</TableCell>
              {cells.map((c) => (
                <Fragment key={c.calculation_run_id}>
                  <TableCell className="text-right text-xs">
                    <ShownValue value={c[f].baseline} money testId="figure-baseline" />
                  </TableCell>
                  <TableCell className="text-right text-xs">
                    <ShownValue value={c[f].stressed} money testId="figure-stressed" />
                  </TableCell>
                  <TableCell className="text-right text-xs">
                    <DeltaValue value={c[f].delta} testId="figure-delta" />
                  </TableCell>
                </Fragment>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

/**
 * One currency's currency × bucket table and heat map (FE-25.2): a column per shock alone and one
 * for the combined run; turns-negative markers per cell, and a distinct highlight on a bucket
 * that breaks only when the shocks are combined. Rows expand to inflows and outflows.
 */
export function ComparisonTable({
  currency,
  buckets,
  metric,
  drill,
  onDrill,
  defaultExpanded = [],
}: {
  currency: ComparisonCurrency
  buckets: ComparisonBucket[]
  metric: Metric
  drill?: DrillTarget | null
  onDrill?: (target: DrillTarget) => void
  defaultExpanded?: string[]
}) {
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set(defaultExpanded))
  const max = heatScale(currency, metric)
  const columns = currency.rows[0]?.cells ?? []
  const toggle = (code: string) =>
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(code)) next.delete(code)
      else next.add(code)
      return next
    })
  return (
    <div className="space-y-2" data-testid="comparison-currency" data-currency={currency.currency}>
      <h3 className="font-mono text-sm font-medium">{currency.currency}</h3>
      <Table data-testid="comparison-table" data-currency={currency.currency} data-metric={metric}>
        <TableHeader>
          <TableRow>
            <TableHead className="w-8" />
            <TableHead>Bucket</TableHead>
            {columns.map((c) => (
              <TableHead key={c.calculation_run_id} className="text-right" data-testid="comparison-column" data-subset={subsetKey(c.shock_subset)} data-combined={c.combined}>
                {columnLabel(c)}
                <div className="text-[10px] font-normal text-muted-foreground">Δ {FIGURE_LABEL[metric].toLowerCase()}</div>
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {currency.rows.map((r) => {
            const open = expanded.has(r.bucket_code)
            const b = buckets.find((x) => x.code === r.bucket_code)
            const only = r.breaks_only_combined
            const unknown = combinedOnlyUnknown(r)
            return (
              <Fragment key={r.bucket_code}>
                <TableRow
                  className={cn(only === true && 'bg-warning/10 outline outline-2 -outline-offset-2 outline-warning')}
                  data-testid="comparison-row"
                  data-currency={currency.currency}
                  data-bucket={r.bucket_code}
                  data-combined-only={only === null ? 'null' : String(only)}
                >
                  <TableCell className="align-top">
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-6 w-6 p-0"
                      onClick={() => toggle(r.bucket_code)}
                      aria-expanded={open}
                      aria-label={`${open ? 'Hide' : 'Show'} inflows and outflows for ${r.bucket_label}`}
                      data-testid="comparison-expand"
                    >
                      {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                    </Button>
                  </TableCell>
                  <TableCell className="align-top text-sm">
                    {r.bucket_label}
                    <div className="font-mono text-xs text-muted-foreground">
                      {r.bucket_code}
                      {b && ` · ${bucketDays(b)}`}
                    </div>
                    {only === true && (
                      <Badge variant="warning" className="mt-1" data-testid="combined-only">
                        <AlertTriangle className="mr-1 h-3 w-3" />
                        Breaks only when combined
                      </Badge>
                    )}
                    {unknown === 'masked' && (
                      <MaskedFlag
                        flag="combined-only"
                        as="div"
                        className="mt-1 text-[10px] text-muted-foreground"
                        testId="combined-only-unknown"
                        attrs={{ 'data-reason': 'masked' }}
                      />
                    )}
                    {unknown === 'not_assessable' && (
                      <div
                        className="mt-1 text-[10px] text-muted-foreground"
                        data-testid="combined-only-unknown"
                        data-reason="not_assessable"
                        title="Fewer single-shock runs were compared than the scenario has shocks, so the flag cannot be assessed."
                      >
                        combined-only: not assessable (needs every single-shock run)
                      </div>
                    )}
                  </TableCell>
                  {r.cells.map((c) => (
                    <DeltaCell
                      key={c.calculation_run_id}
                      cell={c}
                      metric={metric}
                      max={max}
                      active={drill?.currency === currency.currency && drill.bucketCode === r.bucket_code && subsetKey(drill.shockSubset) === subsetKey(c.shock_subset)}
                      onDrill={
                        onDrill &&
                        (() => onDrill({ currency: currency.currency, bucketCode: r.bucket_code, bucketLabel: r.bucket_label, shockSubset: [...c.shock_subset], combined: c.combined }))
                      }
                    />
                  ))}
                </TableRow>
                {open && (
                  <TableRow data-testid="comparison-row-detail" data-bucket={r.bucket_code}>
                    <TableCell />
                    <TableCell colSpan={1 + r.cells.length} className="bg-surface-2">
                      <ExpandedFigures cells={r.cells} />
                    </TableCell>
                  </TableRow>
                )}
              </Fragment>
            )
          })}
        </TableBody>
      </Table>
    </div>
  )
}
