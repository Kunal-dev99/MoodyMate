import Link from 'next/link'
import { FileStack } from 'lucide-react'
import { EmptyState } from '@/components/common/EmptyState'
import { PageSection } from '@/components/common/PageSection'
import { SchemaListGate, type SchemaList, type SchemaListState } from '@/components/schemas/SchemaListLoad'
import { SchemaStatusPill } from '@/components/schemas/StatusPill'
import { Badge } from '@/components/ui/badge'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'

/** The schema catalogue's list (FE-4.1), once GET /schemas has answered. */
export function SchemaCatalogue({ list }: { list: SchemaList }) {
  return list.items.length === 0 ? (
    <EmptyState icon={FileStack} title="No schemas" description="No canonical schemas are registered yet." />
  ) : (
    <PageSection
      icon={FileStack}
      title={`${list.total} schema${list.total === 1 ? '' : 's'}`}
      description="Seeded schemas are illustrative and provisional until the client approves its canonical model."
    >
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Schema</TableHead>
            <TableHead>Owner</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Effective version</TableHead>
            <TableHead>Latest</TableHead>
            <TableHead>Dependents</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {list.items.map((s) => (
            <TableRow key={s.id} data-testid="schema-row" data-code={s.code}>
              <TableCell>
                <Link href={`/schemas/${s.id}`} className="font-medium text-primary hover:underline">
                  {s.name}
                </Link>
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                  <span className="font-mono">{s.code}</span>
                  {s.provisional && (
                    <Badge variant="warning" title="Seeded for illustration; not client-approved">
                      Provisional
                    </Badge>
                  )}
                </div>
              </TableCell>
              <TableCell className="text-sm text-muted-foreground">{s.owner}</TableCell>
              <TableCell>
                <SchemaStatusPill status={s.status} />
              </TableCell>
              <TableCell className="font-mono text-sm">
                {s.effective_version ? (
                  <>
                    v{s.effective_version.version}
                    {s.effective_version.effective_from && (
                      <span className="ml-1 text-xs text-muted-foreground">
                        since {s.effective_version.effective_from}
                      </span>
                    )}
                  </>
                ) : (
                  <span className="text-muted-foreground">none in effect</span>
                )}
              </TableCell>
              <TableCell>
                <span className="mr-2 font-mono text-sm">v{s.latest_version.version}</span>
                <SchemaStatusPill status={s.latest_version.status} />
              </TableCell>
              <TableCell>
                {s.dependents.length === 0 ? (
                  <span className="text-muted-foreground">—</span>
                ) : (
                  <div className="flex flex-wrap gap-1">
                    {s.dependents.map((d) => (
                      <Badge
                        key={`${d.dependent_type}:${d.dependent_ref}`}
                        variant="secondary"
                        title={d.description ?? undefined}
                        className="font-normal"
                      >
                        <span className="mr-1 text-[10px] uppercase text-muted-foreground">{d.dependent_type}</span>
                        {d.dependent_ref}
                      </Badge>
                    ))}
                  </div>
                )}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </PageSection>
  )
}

/**
 * `/schemas` for each state of the schema list load (FE-14.3): loading, 403, "Try again",
 * or the catalogue.
 */
export function SchemaCatalogueView({ state, onRetry }: { state: SchemaListState; onRetry: () => void }) {
  return (
    <SchemaListGate state={state} onRetry={onRetry}>
      {(list) => <SchemaCatalogue list={list} />}
    </SchemaListGate>
  )
}
