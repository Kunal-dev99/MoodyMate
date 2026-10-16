import { Badge } from '@/components/ui/badge'
import { CompatibilityBadge } from './CompatibilityBadge'
import { valueLabel } from './registry'
import type { Proposal } from './proposals'

type Impact = NonNullable<Proposal['impact']>

/** Impact analysis (FE-5.2): compatibility summary, every change, and what depends on it. */
export function ImpactPanel({ impact }: { impact: Impact }) {
  return (
    <div className="space-y-4" data-testid="impact-panel">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="font-medium">Compatibility</span>
        <span data-testid="impact-overall">
          {impact.overall ? <CompatibilityBadge value={impact.overall} /> : <Badge variant="outline">No change</Badge>}
        </span>
        <span className="text-xs text-muted-foreground">
          {impact.changes.length} change{impact.changes.length === 1 ? '' : 's'} · {impact.dependents.length} dependent
          {impact.dependents.length === 1 ? '' : 's'} · analysed {new Date(impact.analysed_at).toLocaleString()}
        </span>
      </div>

      <div>
        <h4 className="mb-1 text-xs font-medium uppercase text-muted-foreground">Changes</h4>
        <ul className="space-y-1">
          {impact.changes.map((c) => (
            <li key={`${c.field}:${c.change}:${c.attribute ?? ''}`} className="flex flex-wrap items-center gap-2 text-sm" data-testid="impact-change">
              <CompatibilityBadge value={c.compatibility} />
              <span className="font-mono">{c.field}</span>
              <span className="text-xs uppercase text-muted-foreground">
                {c.change.toLowerCase()}
                {c.attribute ? ` · ${c.attribute}` : ''}
              </span>
              {c.change === 'MODIFIED' && (
                <span className="font-mono text-xs">
                  {valueLabel(c.from_value)} → {valueLabel(c.to_value)}
                </span>
              )}
              <span className="text-xs text-muted-foreground">{c.reason}</span>
            </li>
          ))}
        </ul>
      </div>

      <div>
        <h4 className="mb-1 text-xs font-medium uppercase text-muted-foreground">Affected dependents</h4>
        {impact.dependents.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nothing is registered as depending on this schema.</p>
        ) : (
          <ul className="space-y-1">
            {impact.dependents.map((d) => (
              <li
                key={`${d.dependent_type}:${d.dependent_ref}:${d.via.join('>')}`}
                className="flex flex-wrap items-center gap-2 text-sm"
                data-testid="impact-dependent"
              >
                <Badge variant="secondary" className="font-normal">
                  <span className="mr-1 text-[10px] uppercase text-muted-foreground">{d.dependent_type}</span>
                  {d.dependent_ref}
                </Badge>
                <span className="text-xs text-muted-foreground">
                  {d.depth === 1 ? 'direct' : `via ${d.via.join(' → ')}`}
                </span>
                {d.provisional && <span className="text-xs text-muted-foreground">(illustrative)</span>}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
