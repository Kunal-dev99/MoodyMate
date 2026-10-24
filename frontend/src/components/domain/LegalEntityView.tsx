'use client'

import Link from 'next/link'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { Building, Link2, ListTree } from 'lucide-react'
import { api, call } from '@/api/client'
import { Forbidden } from '@/components/auth/Forbidden'
import { ActorLabel } from '@/components/common/ActorLabel'
import { EmptyState } from '@/components/common/EmptyState'
import { LoadFailed } from '@/components/common/LoadFailed'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { PageHeader } from '@/components/common/PageHeader'
import { PageSection } from '@/components/common/PageSection'
import { createLoader, type Loaded } from '@/components/common/load'
import { Badge } from '@/components/ui/badge'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { formatTimestamp } from '@/lib/format'
import { ShownValue } from './ShownValue'
import {
  SOURCE_KIND_LABELS,
  legalEntitiesHref,
  linkCountRows,
  listAllLinks,
  type IdentityLink,
  type LegalEntityDetail,
} from './identity'

export const LEGAL_ENTITY_CRUMBS = [
  { label: 'Financial Domain', href: '/domain' },
  { label: 'Legal entities', href: legalEntitiesHref },
]

/** D-58 §2: every legal entity is provisional; its attributes and source-of-truth rule wait for the client. */
export function ProvisionalIdentityBadge() {
  return (
    <Badge
      variant="warning"
      data-testid="provisional-identity-badge"
      title="Provisional: attributes and the source-of-truth rule are deferred to the client (D-58 §2, Data Model §11)."
    >
      Provisional identity
    </Badge>
  )
}

function Field({ label, children, testId }: { label: string; children: ReactNode; testId?: string }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd data-testid={testId}>{children}</dd>
    </>
  )
}

const Mono = ({ children }: { children: ReactNode }) => <span className="break-all font-mono text-xs">{children}</span>

type Ready = { detail: LegalEntityDetail; links: IdentityLink[] }

const fetchLegalEntity = (id: string) => (signal: AbortSignal) =>
  Promise.all([
    call(() => api.GET('/api/v1/legal-entities/{legal_entity_id}', { params: { path: { legal_entity_id: id } }, signal })),
    listAllLinks({ target_kind: 'LEGAL_ENTITY', target_id: id }, signal),
  ]).then(([detail, links]): Ready => ({ detail, links }))

/**
 * FE-31.1 (D-58): one legal entity, the server's link counts per source kind and the links
 * naming it. Links carry ids only (D-58 §7): a position version has no page of its own, so its
 * row id is shown as text. Read-only; a link is never changed (D-58 §5).
 */
export function LegalEntityView({ legalEntityId }: { legalEntityId: string }) {
  const [state, setState] = useState<Loaded<Ready>>({ kind: 'loading' })
  const loader = useMemo(() => createLoader(fetchLegalEntity(legalEntityId), setState, 'Could not load the legal entity'), [legalEntityId])

  useEffect(() => {
    loader.load()
    return () => loader.abort()
  }, [loader])

  const wrap = (body: ReactNode) => (
    <>
      <PageHeader title="Legal entity" breadcrumbs={[...LEGAL_ENTITY_CRUMBS, { label: legalEntityId.slice(0, 8) }]} />
      <div className="px-6 pb-6">{body}</div>
    </>
  )

  if (state.kind === 'loading') return <LoadingBlock rows={5} />
  if (state.kind === 'forbidden') return wrap(<Forbidden permission="domain:read" correlationId={state.cid} />)
  if (state.kind === 'failed') return wrap(<LoadFailed icon={Building} title="The legal entity could not be loaded" cid={state.cid} onRetry={loader.load} />)
  if (state.kind === 'not_found')
    return wrap(
      <EmptyState
        icon={Building}
        title="Legal entity not found"
        description={`It does not exist or is not visible to you.${state.cid ? ` Correlation ID: ${state.cid}` : ''}`}
      />,
    )

  const { detail, links } = state.value
  const label = typeof detail.code === 'string' ? detail.code : detail.legal_entity_id.slice(0, 8)
  return (
    <>
      <PageHeader
        title={`Legal entity ${label}`}
        description={`Source ${detail.source_code}. The code is masked by the classification of the position field it came from.`}
        breadcrumbs={[...LEGAL_ENTITY_CRUMBS, { label }]}
      />
      <div className="space-y-4 px-6 pb-6">
        <PageSection
          icon={ListTree}
          title="Identity"
          description="Stable per tenant, source system and code. Append-only."
          headerRight={detail.provisional ? <ProvisionalIdentityBadge /> : undefined}
        >
          <dl className="grid grid-cols-[200px_1fr] gap-y-1 text-sm" data-testid="legal-entity-identity">
            <Field label="Legal entity ID" testId="legal-entity-id">
              <Mono>{detail.legal_entity_id}</Mono>
            </Field>
            <Field label="Code" testId="legal-entity-detail-code">
              <ShownValue value={detail.code} />
            </Field>
            <Field label="Source">
              <Link href={`/ingestion/sources/${detail.source_id}`} className="font-mono text-primary hover:underline">
                {detail.source_code}
              </Link>
            </Field>
            <Field label="First seen">
              <Mono>{formatTimestamp(detail.created_at)}</Mono>
            </Field>
            {linkCountRows(detail.link_counts).map(([kind, count]) => (
              <Field key={kind} label={`Links from ${SOURCE_KIND_LABELS[kind].toLowerCase()}s`}>
                <span className="font-mono" data-testid="legal-entity-link-count" data-kind={kind} data-count={count}>
                  {count}
                </span>
              </Field>
            ))}
          </dl>
        </PageSection>
        <PageSection
          icon={Link2}
          title={`Identity links (${links.length})`}
          description="Rows whose text key resolved to exactly this identity in your tenant. Runs never read links, so results and their hashes do not depend on them."
        >
          {links.length === 0 ? (
            <p className="text-sm text-muted-foreground" data-testid="legal-entity-no-links">
              No row links to this legal entity yet.
            </p>
          ) : (
            <Table data-testid="legal-entity-links">
              <TableHeader>
                <TableRow>
                  <TableHead>Source row</TableHead>
                  <TableHead>Row ID</TableHead>
                  <TableHead>Method</TableHead>
                  <TableHead>Resolver</TableHead>
                  <TableHead>Linked</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {links.map((l) => (
                  <TableRow key={l.link_id} data-testid="legal-entity-link-row" data-source-kind={l.source_kind} data-source-row-id={l.source_row_id}>
                    <TableCell className="text-sm">{SOURCE_KIND_LABELS[l.source_kind]}</TableCell>
                    <TableCell>
                      <Mono>{l.source_row_id}</Mono>
                    </TableCell>
                    <TableCell className="font-mono text-xs">{l.method}</TableCell>
                    <TableCell className="font-mono text-xs">v{l.resolver_version}</TableCell>
                    <TableCell className="text-xs">
                      <span className="font-mono">{formatTimestamp(l.created_at)}</span> · <ActorLabel actor={l.created_by} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </PageSection>
      </div>
    </>
  )
}
