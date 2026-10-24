import { Masked } from '@/components/common/Masked'
import { deltaText, toneOf, type Shown } from './comparison'

/**
 * FE-25.2: a delta as the server sent it: grouped exact text with its sign, or `MaskedValue`
 * (never a blank or a zero). `data-value` keeps the server's exact text.
 */
export function DeltaValue({ value, testId = 'comparison-delta' }: { value: Shown; testId?: string }) {
  return (
    <Masked value={value} cell testId={testId}>
      {(v) => (
        <span className="font-mono" data-testid={testId} data-value={v} data-tone={toneOf(v)}>
          {deltaText(v)}
        </span>
      )}
    </Masked>
  )
}
