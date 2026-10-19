import { ShieldAlert } from 'lucide-react'
import type { Refusal } from '@/components/ingestion/mappings'
import { Alert } from '@/components/ui/alert'
import type { ProblemLine } from './scenarios'

/** A refused scenario request, inline: the server's envelope as it came, with its correlation ID. */
export function ScenarioRefusalNotice({ refusal, problems = [] }: { refusal: Refusal; problems?: ProblemLine[] }) {
  return (
    <Alert variant="destructive" data-testid="scenario-refusal" data-code={refusal.code}>
      <ShieldAlert className="h-4 w-4" />
      <div className="space-y-1 text-sm">
        <div className="font-medium">{refusal.title}</div>
        <div className="text-foreground">{refusal.message}</div>
        {problems.length > 0 && (
          <ul className="list-disc pl-4 text-foreground" data-testid="scenario-refusal-problems">
            {problems.map((p, i) => (
              <li key={i}>{p.text}</li>
            ))}
          </ul>
        )}
        <div className="font-mono text-xs text-muted-foreground">
          {refusal.status !== null && <>HTTP {refusal.status} · </>}
          <span data-testid="scenario-refusal-code">{refusal.code}</span>
          {refusal.cid && <> · Correlation ID: {refusal.cid}</>}
        </div>
      </div>
    </Alert>
  )
}
