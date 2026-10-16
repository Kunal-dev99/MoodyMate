'use client'

import { Lock } from 'lucide-react'
import type { Schemas } from '@/api/client'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'

type Masked = Schemas['MaskedValue']

/** True when an API value was withheld by the server's masking policy (BE-3.5). */
export function isMasked(value: unknown): value is Masked {
  return (
    typeof value === 'object' &&
    value !== null &&
    (value as { masked?: unknown }).masked === true &&
    typeof (value as { classification?: unknown }).classification === 'string'
  )
}

/**
 * A value the server masked (FE-3.2). Always renders explicit "masked" text with a
 * tooltip explaining why, never an empty string, so a withheld value is never mistaken
 * for a blank one.
 */
export function MaskedValue({ value }: { value: Masked }) {
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span
            className="inline-flex items-center gap-1 rounded-sm border border-border bg-surface-2 px-1.5 py-0.5 font-mono text-xs text-muted-foreground"
            data-testid="masked-value"
            aria-label={`Masked by policy: ${value.classification} data`}
          >
            <Lock className="h-3 w-3" />
            masked
          </span>
        </TooltipTrigger>
        <TooltipContent>
          Masked by policy: <strong>{value.classification}</strong> data. Your role&apos;s
          clearance does not include it.
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}
