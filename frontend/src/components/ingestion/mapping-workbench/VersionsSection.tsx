'use client'

import { History } from 'lucide-react'
import { ActorLabel } from '@/components/common/ActorLabel'
import { PageSection } from '@/components/common/PageSection'
import { MappingStatusPill } from '@/components/ingestion/MappingStatusPill'
import type { MappingDetail, MappingVersion } from '@/components/ingestion/mappings'
import { Badge } from '@/components/ui/badge'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { formatTimestamp } from '@/lib/format'
import { cn } from '@/lib/utils'
import type { MappingWorkbenchState } from './useMappingWorkbench'
import { OUTCOME_VARIANT } from './shared'

/** Every version, newest first: a change is always a new version, never an edit (FE-10.2). */
export function VersionsSection({ m, version, select }: { m: MappingDetail; version: MappingVersion; select: MappingWorkbenchState['select'] }) {
  return (
    <PageSection icon={History} title="Versions" description="Oldest first is history; a change is always a new version, never an edit.">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Version</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Schema version</TableHead>
            <TableHead>Rules</TableHead>
            <TableHead>Author</TableHead>
            <TableHead>Last dry run</TableHead>
            <TableHead>Decision</TableHead>
            <TableHead className="whitespace-nowrap">Content SHA-256</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {[...m.versions].reverse().map((v) => (
            <TableRow
              key={v.id}
              data-testid="mapping-version-row"
              data-version={v.version}
              data-status={v.status}
              className={cn(v.id === version.id && 'bg-surface-2')}
            >
              <TableCell>
                <button
                  type="button"
                  className="font-mono text-sm text-primary hover:underline"
                  onClick={() => v.id !== version.id && select({ version: v.version })}
                  data-testid="mapping-version-select"
                >
                  v{v.version}
                </button>
                {v.id === version.id && <span className="ml-1 text-xs text-muted-foreground">(open)</span>}
              </TableCell>
              <TableCell>
                <MappingStatusPill status={v.status} />
              </TableCell>
              <TableCell className="font-mono text-xs">{v.schema_version != null ? `v${v.schema_version}` : '—'}</TableCell>
              <TableCell className="font-mono text-xs">{v.rules.length}</TableCell>
              <TableCell className="text-xs">
                <ActorLabel actor={v.author} />
                <div className="font-sans text-muted-foreground">{formatTimestamp(v.created_at)}</div>
                {v.change_summary && <div className="font-sans text-foreground">“{v.change_summary}”</div>}
              </TableCell>
              <TableCell className="text-xs">
                {v.last_validation ? (
                  <Badge variant={OUTCOME_VARIANT[v.last_validation.outcome]}>{v.last_validation.outcome}</Badge>
                ) : (
                  <span className="text-muted-foreground">none</span>
                )}
              </TableCell>
              <TableCell className="text-xs">
                {v.decided_by ? (
                  <>
                    <ActorLabel actor={v.decided_by} />
                    <div className="text-muted-foreground">{v.decided_at && formatTimestamp(v.decided_at)}</div>
                    {v.decision_comment && <div>“{v.decision_comment}”</div>}
                  </>
                ) : (
                  <span className="text-muted-foreground">—</span>
                )}
              </TableCell>
              <TableCell className="font-mono text-xs" title={v.content_sha256}>
                {v.content_sha256.slice(0, 12)}…
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </PageSection>
  )
}
