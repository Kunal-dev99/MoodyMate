import { MaskedValue, isMasked } from '@/components/common/MaskedValue'
import { deltaText, toneOf, type Shown } from './comparison'

/**
 * FE-25.2: a delta as the server sent it: grouped exact text with its sign, or `MaskedValue`
 * (never a blank or a zero). `data-value` keeps the server's exact text.
 */
export function DeltaValue({ value, testId = 'comparison-delta' }: { value: Shown; testId?: string }) {
  if (isMasked(value))
    return (
      <span data-testid={testId} data-masked="true">
        <MaskedValue value={value} />
      </span>
    )
  return (
    <span className="font-mono" data-testid={testId} data-value={value} data-tone={toneOf(value)}>
      {deltaText(value)}
    </span>
  )
}
