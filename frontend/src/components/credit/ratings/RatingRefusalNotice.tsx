import { ShieldAlert } from 'lucide-react'
import type { Refusal } from '@/components/ingestion/mappings'
import { Alert } from '@/components/ui/alert'

/** A refused rating request, inline: the server's envelope as it came, with its correlation ID. */
export function RatingRefusalNotice({ refusal }: { refusal: Refusal }) {
  return (
    <Alert variant="destructive" data-testid="rating-refusal" data-code={refusal.code}>
      <ShieldAlert className="h-4 w-4" />
      <div className="space-y-1 text-sm">
        <div className="font-medium">{refusal.title}</div>
        <div className="text-foreground">{refusal.message}</div>
        <div className="font-mono text-xs text-muted-foreground">
          {refusal.status !== null && <>HTTP {refusal.status} · </>}
          <span data-testid="rating-refusal-code">{refusal.code}</span>
          {refusal.cid && (
            <>
              {' '}
              · Correlation ID: <span data-testid="rating-refusal-cid">{refusal.cid}</span>
            </>
          )}
        </div>
      </div>
    </Alert>
  )
}
