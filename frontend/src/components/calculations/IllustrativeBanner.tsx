import { FlaskConical } from 'lucide-react'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { cn } from '@/lib/utils'
import { ILLUSTRATIVE_STATEMENT } from './calculations'

/**
 * The D-35 §3 label on every calculations view and result (FE-15.1): an illustrative
 * prototype, not a regulatory calculation, no vendor parity. `statement` is the server's own
 * text when the response carries one; the fixed D-35 wording otherwise.
 */
export function IllustrativeBanner({ statement, className, compact }: { statement?: string | null; className?: string; compact?: boolean }) {
  const text = statement || ILLUSTRATIVE_STATEMENT
  if (compact)
    return (
      <p className={cn('text-xs text-[hsl(var(--warning))]', className)} data-testid="illustrative-statement">
        <FlaskConical className="mr-1 inline h-3.5 w-3.5 align-[-2px]" />
        {text}
      </p>
    )
  return (
    <Alert variant="warning" className={className} data-testid="illustrative-banner">
      <FlaskConical className="h-4 w-4" />
      <AlertTitle>Illustrative prototype — not regulatory</AlertTitle>
      <AlertDescription className="text-foreground" data-testid="illustrative-statement">
        {text} Methodology parameters are seeded, provisional and illustrative; nothing here may be used for client decisions.
      </AlertDescription>
    </Alert>
  )
}
