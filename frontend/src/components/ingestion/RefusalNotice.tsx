import { ShieldAlert } from 'lucide-react'
import { Alert } from '@/components/ui/alert'
import type { Refusal } from './mappings'

/**
 * A refused mapping request, inline (FE-10.2): the backend's error envelope as it came
 * (status, code, message, correlation ID). The UI never decides an action succeeded.
 */
export function RefusalNotice({ refusal }: { refusal: Refusal }) {
  return (
    <Alert variant="destructive" data-testid="mapping-refusal" data-code={refusal.code}>
      <ShieldAlert className="h-4 w-4" />
      <div className="space-y-1 text-sm">
        <div className="font-medium">{refusal.title}</div>
        <div className="text-foreground">{refusal.message}</div>
        <div className="font-mono text-xs text-muted-foreground">
          {refusal.status !== null && <>HTTP {refusal.status} · </>}
          <span data-testid="mapping-refusal-code">{refusal.code}</span>
          {refusal.cid && <> · Correlation ID: <span data-testid="mapping-refusal-cid">{refusal.cid}</span></>}
        </div>
      </div>
    </Alert>
  )
}
