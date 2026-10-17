import Link from 'next/link'
import type { Schemas } from '@/api/client'
import { importHref } from '@/components/domain/domain'
import { ReconciliationOutcomePill } from '@/components/ingestion/ReconciliationPills'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import type { SnapshotMember } from './calculations'

/** A pinned member as the snapshot (SnapshotOut) or the run manifest (ManifestMember) lists it. */
type Member = Schemas['ManifestMember'] & Partial<Pick<SnapshotMember, 'source_code'>>

const Hash = ({ value }: { value: string }) => <span className="break-all font-mono text-[11px]">{value}</span>

/**
 * The imports a dataset snapshot pins (D-30 §1), one per schema code: each with its import
 * manifest hash, projection, schema and mapping versions, row counts (QUARANTINED held rows
 * included, D-30 §3) and reconciliation outcome. Every id is shown in full.
 */
export function SnapshotMembers({ members, testId = 'snapshot-members' }: { members: readonly Member[]; testId?: string }) {
  return (
    <Table data-testid={testId}>
      <TableHeader>
        <TableRow>
          <TableHead>Schema</TableHead>
          <TableHead>Import</TableHead>
          <TableHead>Pinned versions</TableHead>
          <TableHead>Manifest SHA-256</TableHead>
          <TableHead className="text-right">Accepted / held / rejected</TableHead>
          <TableHead>Reconciliation</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {members.map((m) => (
          <TableRow key={m.import_id} data-testid="snapshot-member" data-schema={m.schema_code} data-import-id={m.import_id}>
            <TableCell className="font-mono text-sm">
              {m.schema_code}
              {m.schema_version !== null && <span className="text-muted-foreground"> v{m.schema_version}</span>}
            </TableCell>
            <TableCell className="text-xs">
              <Link href={importHref(m.import_id)} className="font-mono text-primary hover:underline" data-testid="snapshot-member-import">
                {m.import_id}
              </Link>
              {m.source_code && <div className="font-mono text-muted-foreground">{m.source_code}</div>}
            </TableCell>
            <TableCell className="space-y-0.5 text-xs">
              <div>
                <span className="text-muted-foreground">import manifest </span>
                <span className="font-mono">{m.import_manifest_id}</span>
              </div>
              <div>
                <span className="text-muted-foreground">schema version </span>
                <span className="font-mono">{m.schema_version_id}</span>
              </div>
              <div>
                <span className="text-muted-foreground">mapping version </span>
                <span className="font-mono">{m.mapping_version_id}</span>
              </div>
              <div>
                <span className="text-muted-foreground">projection </span>
                <span className="font-mono">{m.projection_id}</span>
                <span className="text-muted-foreground"> · projector v{m.projector_version}</span>
              </div>
            </TableCell>
            <TableCell className="max-w-[220px]">
              <Hash value={m.manifest_sha256} />
            </TableCell>
            <TableCell className="text-right font-mono text-sm" data-testid="snapshot-member-counts">
              {m.accepted_count} / {m.quarantined_count} / {m.rejected_count}
            </TableCell>
            <TableCell>
              <ReconciliationOutcomePill outcome={m.reconciliation_outcome} />
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}
