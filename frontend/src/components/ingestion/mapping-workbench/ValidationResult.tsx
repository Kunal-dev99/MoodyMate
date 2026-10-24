import { ActorLabel } from '@/components/common/ActorLabel'
import { bySeverity, type MappingValidation } from '@/components/ingestion/mappings'
import { Badge } from '@/components/ui/badge'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { formatTimestamp } from '@/lib/format'
import { cn } from '@/lib/utils'
import { OUTCOME_VARIANT, SEVERITY_VARIANT } from './shared'

/** The dry run's result (BE-10.4): outcome, issues (never values) and counts per rule. */
export function ValidationResult({ v }: { v: MappingValidation }) {
  return (
    <div className="space-y-3" data-testid="mapping-validation" data-outcome={v.outcome} data-ok={String(v.ok)}>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Badge variant={OUTCOME_VARIANT[v.outcome]} data-testid="mapping-validation-outcome">
          {v.outcome}
        </Badge>
        <span className="font-mono text-xs">
          v{v.version} · {v.row_count} rows · {v.blocker_count} blockers · {v.error_count} errors · {v.warning_count} warnings
        </span>
        <span className="text-xs text-muted-foreground">
          {v.ok ? 'This version may be submitted for approval.' : 'Fix the blockers and errors, save a new version and validate again.'}
        </span>
      </div>
      {v.issues.length > 0 && (
        <ul className="space-y-1" data-testid="mapping-issues">
          {bySeverity(v.issues).map((i, n) => (
            <li key={n} className="flex flex-wrap items-center gap-2 text-sm" data-testid="mapping-issue" data-severity={i.severity} data-code={i.code} data-target={i.target_field ?? ''}>
              <Badge variant={SEVERITY_VARIANT[i.severity]}>{i.severity}</Badge>
              <span className="font-mono text-xs text-muted-foreground">{i.code}</span>
              <span>{i.message}</span>
              {i.count != null && <span className="font-mono text-xs text-muted-foreground">({i.count} rows)</span>}
              {i.reasons && (
                <span className="font-mono text-xs text-muted-foreground">
                  {Object.entries(i.reasons)
                    .map(([k, c]) => `${k}: ${c}`)
                    .join(' · ')}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Rule</TableHead>
            <TableHead>Source column</TableHead>
            <TableHead>Target</TableHead>
            <TableHead>Transform</TableHead>
            <TableHead className="text-right">Rows</TableHead>
            <TableHead className="text-right">Mapped</TableHead>
            <TableHead className="text-right">Empty</TableHead>
            <TableHead className="text-right">Failed</TableHead>
            <TableHead>Failure reasons</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {v.rule_results.map((r) => (
            <TableRow key={r.position} data-testid="mapping-rule-result" data-failed={r.failed}>
              <TableCell className="font-mono text-xs">{r.position}</TableCell>
              <TableCell className="font-mono text-xs">{r.source_column ?? '—'}</TableCell>
              <TableCell className="font-mono text-xs">
                {r.column_policy === 'PRESERVE' ? <span className="text-muted-foreground">preserved</span> : `${r.target_field} ${r.target_type ? `(${r.target_type})` : ''}`}
              </TableCell>
              <TableCell className="font-mono text-xs">{r.transform}</TableCell>
              <TableCell className="text-right font-mono text-xs">{r.rows}</TableCell>
              <TableCell className="text-right font-mono text-xs">{r.mapped}</TableCell>
              <TableCell className="text-right font-mono text-xs">{r.empty}</TableCell>
              <TableCell className={cn('text-right font-mono text-xs', r.failed > 0 && 'text-destructive')}>{r.failed}</TableCell>
              <TableCell className="font-mono text-xs text-muted-foreground">
                {Object.entries(r.failures)
                  .map(([k, c]) => `${k}: ${c}`)
                  .join(' · ') || '—'}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <p className="font-mono text-xs text-muted-foreground">
        Validation {v.id} · import {v.import_id} · content {v.mapping_sha256.slice(0, 12)}… · by <ActorLabel actor={v.created_by} /> · {formatTimestamp(v.created_at)}
      </p>
    </div>
  )
}
