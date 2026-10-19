import { FlaskConical } from 'lucide-react'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { cn } from '@/lib/utils'

/**
 * D-44 §8 (with D-35 §3): the fixed wording every scenario response carries as
 * `labels.statement`. Shown until a response supplies the server's own text.
 */
export const SCENARIO_STATEMENT =
  'Illustrative stress exploration, not a regulatory stress test. Not a regulatory calculation; no parity with any vendor system is claimed.'

/**
 * FE-24.1: the label on every Agentic Stress-Test Explorer page. `statement` is the server's
 * `labels.statement` when a response carries one; the fixed D-44 §8 wording otherwise.
 */
export function StressExplorerBanner({ statement, className }: { statement?: string | null; className?: string }) {
  return (
    <Alert variant="warning" className={cn(className)} data-testid="stress-explorer-banner">
      <FlaskConical className="h-4 w-4" />
      <AlertTitle>Illustrative exploration — not regulatory</AlertTitle>
      <AlertDescription className="text-foreground" data-testid="stress-explorer-statement">
        {statement || SCENARIO_STATEMENT}
      </AlertDescription>
    </Alert>
  )
}
