'use client'

import Link from 'next/link'
import { useEffect, useState } from 'react'
import { FileStack } from 'lucide-react'
import { api, call, ApiError, type Schemas } from '@/api/client'
import { Forbidden } from '@/components/auth/Forbidden'
import { EmptyState } from '@/components/common/EmptyState'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { PageHeader } from '@/components/common/PageHeader'
import { PageSection } from '@/components/common/PageSection'
import { toastApiError } from '@/components/common/api-error-toast'
import { StatusPill } from '@/components/schemas/StatusPill'
import { Badge } from '@/components/ui/badge'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'

type State =
  | { kind: 'loading' }
  | { kind: 'ready'; list: Schemas['SchemaList'] }
  | { kind: 'forbidden'; cid: string | null }

/** Schema catalogue (FE-4.1): every registered canonical schema in the tenant. */
export default function SchemaCataloguePage() {
  const [state, setState] = useState<State>({ kind: 'loading' })

  useEffect(() => {
    call(() => api.GET('/api/v1/schemas'))
      .then((list) => setState({ kind: 'ready', list }))
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 403) setState({ kind: 'forbidden', cid: err.correlationId })
        else toastApiError(err, 'Could not load schemas')
      })
  }, [])

  return (
    <>
      <PageHeader
        title="Schema catalogue"
        description="Versioned canonical schemas: owner, lifecycle status, the version in effect and what depends on each."
      />
      <div className="px-6 pb-6">
        {state.kind === 'forbidden' ? (
          <Forbidden permission="schema:read" correlationId={state.cid} />
        ) : state.kind === 'loading' ? (
          <LoadingBlock rows={4} />
        ) : state.list.items.length === 0 ? (
          <EmptyState icon={FileStack} title="No schemas" description="No canonical schemas are registered yet." />
        ) : (
          <PageSection
            icon={FileStack}
            title={`${state.list.total} schema${state.list.total === 1 ? '' : 's'}`}
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
                {state.list.items.map((s) => (
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
                      <StatusPill status={s.status} />
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
                      <StatusPill status={s.latest_version.status} />
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
        )}
      </div>
    </>
  )
}
