'use client'

import { Masked } from '@/components/common/Masked'
import { Button } from '@/components/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { cn } from '@/lib/utils'
import { DeltaValue } from './DeltaValue'
import { columnLabel, firstNegativeText, subsetKey, type ComparisonCurrency, type DrillTarget, type FirstNegativeCumulative, type ScenarioComparison } from './comparison'

/*
 * FE-25.2 (split out of ComparisonView.tsx by FE-35.2, unchanged): the comparison's rankings.
 * The server ranks and decides; a masked figure is never ranked or guessed.
 */

/** A first-negative bucket: its code, "never negative", or masked. */
function FirstNegativeCode({ code, testId }: { code: FirstNegativeCumulative['baseline_bucket_code']; testId: string }) {
  return (
    <Masked value={code} cell testId={testId}>
      {(c) => (
        <span className={cn('font-mono', c === null && 'text-muted-foreground')} data-testid={testId} data-value={c ?? 'null'}>
          {firstNegativeText(c)}
        </span>
      )}
    </Masked>
  )
}

/** The first negative cumulative bucket, baseline versus each member run (illustrative, D-46 §2). */
export function FirstNegative({ currencies }: { currencies: ComparisonCurrency[] }) {
  return (
    <Table data-testid="first-negative">
      <TableHeader>
        <TableRow>
          <TableHead>Currency</TableHead>
          <TableHead>Run</TableHead>
          <TableHead>Baseline</TableHead>
          <TableHead>Stressed</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {currencies.flatMap((c) =>
          c.first_negative_cumulative.map((f) => (
            <TableRow key={`${c.currency}-${subsetKey(f.shock_subset)}`} data-testid="first-negative-row" data-currency={c.currency} data-subset={subsetKey(f.shock_subset)} data-combined={f.combined}>
              <TableCell className="font-mono text-xs">{c.currency}</TableCell>
              <TableCell className="text-xs">{columnLabel(f)}</TableCell>
              <TableCell className="text-xs">
                <FirstNegativeCode code={f.baseline_bucket_code} testId="first-negative-baseline" />
              </TableCell>
              <TableCell className="text-xs">
                <FirstNegativeCode code={f.stressed_bucket_code} testId="first-negative-stressed" />
              </TableCell>
            </TableRow>
          )),
        )}
      </TableBody>
    </Table>
  )
}

/** The largest changes, ranked by the server over clear net deltas only (masked deltas are never ranked). */
export function LargestChanges({ comparison, onDrill }: { comparison: Pick<ScenarioComparison, 'largest_changes' | 'buckets'>; onDrill: (t: DrillTarget) => void }) {
  if (comparison.largest_changes.length === 0)
    return (
      <p className="text-sm text-muted-foreground" data-testid="largest-none">
        No clear net change to rank. Masked deltas are never ranked.
      </p>
    )
  return (
    <Table data-testid="largest-changes">
      <TableHeader>
        <TableRow>
          <TableHead className="w-12">#</TableHead>
          <TableHead>Currency</TableHead>
          <TableHead>Bucket</TableHead>
          <TableHead>Run</TableHead>
          <TableHead className="text-right">Δ net gap</TableHead>
          <TableHead />
        </TableRow>
      </TableHeader>
      <TableBody>
        {comparison.largest_changes.map((l) => {
          const label = comparison.buckets.find((b) => b.code === l.bucket_code)?.label ?? l.bucket_code
          return (
            <TableRow key={`${l.rank}`} data-testid="largest-change" data-rank={l.rank} data-currency={l.currency} data-bucket={l.bucket_code} data-subset={subsetKey(l.shock_subset)}>
              <TableCell className="font-mono text-xs">{l.rank}</TableCell>
              <TableCell className="font-mono text-xs">{l.currency}</TableCell>
              <TableCell className="text-xs">
                {label} <span className="font-mono text-muted-foreground">{l.bucket_code}</span>
              </TableCell>
              <TableCell className="text-xs">{columnLabel(l)}</TableCell>
              <TableCell className="text-right text-sm">
                <DeltaValue value={l.net_gap_delta} testId="largest-delta" />
              </TableCell>
              <TableCell className="text-right">
                <Button
                  size="sm"
                  variant="outline"
                  className="h-7"
                  onClick={() => onDrill({ currency: l.currency, bucketCode: l.bucket_code, bucketLabel: label, shockSubset: [...l.shock_subset], combined: l.combined })}
                  data-testid="largest-drill"
                >
                  Changed parts
                </Button>
              </TableCell>
            </TableRow>
          )
        })}
      </TableBody>
    </Table>
  )
}
