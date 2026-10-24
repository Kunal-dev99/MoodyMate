import { Coins } from 'lucide-react'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { FX_BLOCKED_NOTICE } from '@/components/domain/terms'
import type { ResultContext } from './validation'

/**
 * FE-32.1 (D-61 §6): on the maturity-ladder and LCR views, results are per currency and FX
 * consolidation is blocked (`fx_policy_not_approved`) until an approved rate source and
 * conversion policy exist. Shown whatever the read sends (fail closed): the result context's
 * `consolidation` value is kept on the element, and an older server that sends none still gets
 * the notice. Nothing in the UI converts or totals across currencies.
 */
export function FxConsolidationNotice({ consolidation, source }: { consolidation: ResultContext['consolidation'] | undefined; source: string }) {
  return (
    <Alert variant="info" data-testid="fx-consolidation-notice" data-source={source} data-consolidation={consolidation ?? 'unknown'}>
      <Coins className="h-4 w-4" />
      <AlertDescription className="text-xs text-foreground">{FX_BLOCKED_NOTICE}</AlertDescription>
    </Alert>
  )
}
