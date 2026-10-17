import { ShownValue } from '@/components/domain/ShownValue'
import { ladderMaskHint, type LadderFigure, type LadderRow } from './calculations'

/**
 * One maturity-ladder figure as the server sent it (FE-15.2): exact decimal text, or the
 * "masked" marker (`MaskedValue`) with a short hint when D-33 minimum aggregation withheld it
 * (built from fewer than `min_aggregate_count` contributions, or derived from such a figure).
 */
export function LadderFigureValue({
  row,
  figure,
  minAggregateCount,
  testId,
}: {
  row: LadderRow
  figure: LadderFigure
  minAggregateCount: number
  testId: string
}) {
  const hint = ladderMaskHint(row, figure, minAggregateCount)
  return (
    <span className="inline-flex flex-col items-end">
      <ShownValue value={row[figure]} testId={testId} />
      {hint && (
        <span className="mt-0.5 text-[11px] leading-tight text-muted-foreground" data-testid={`${testId}-hint`}>
          {hint}
        </span>
      )}
    </span>
  )
}
