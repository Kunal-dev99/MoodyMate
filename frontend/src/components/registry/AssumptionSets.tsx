'use client'

import Link from 'next/link'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { BookMarked, History } from 'lucide-react'
import { api, call } from '@/api/client'
import { Forbidden } from '@/components/auth/Forbidden'
import { IllustrativeBanner } from '@/components/calculations/IllustrativeBanner'
import { EmptyState } from '@/components/common/EmptyState'
import { LoadFailed } from '@/components/common/LoadFailed'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { PageHeader } from '@/components/common/PageHeader'
import { PageSection } from '@/components/common/PageSection'
import { createLoader, type Loaded } from '@/components/common/load'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { cn } from '@/lib/utils'
import { LabelBadges, VersionStatusPill } from './RegistryBadges'
import { assumptionSetHref, assumptionSetsHref, type AssumptionSetDetail, type AssumptionSetList } from './registry'

const READ_ONLY = 'Seeded and read-only in this release: authoring is deferred (D-32 §6). Every run pins the ACTIVE version of the baseline set.'
const when = (at: string | null) => (at ? new Date(at).toLocaleString() : '—')
const effective = (from: string | null, to: string | null) => `${from ?? '—'} → ${to ?? 'open'}`

const fetchList = (signal: AbortSignal) => call(() => api.GET('/api/v1/assumption-sets', { signal }))
const fetchDetail = (id: string) => (signal: AbortSignal) =>
  call(() => api.GET('/api/v1/assumption-sets/{assumption_set_id}', { params: { path: { assumption_set_id: id } }, signal }))

function Field({ label, children, testId }: { label: string; children: ReactNode; testId?: string }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd data-testid={testId}>{children}</dd>
    </>
  )
}

/** The assumption keys a version holds (keys only, never values: the API sends none). */
function Keys({ keys }: { keys: readonly string[] }) {
  if (keys.length === 0) return <span className="text-muted-foreground">None: no module reads an assumption yet (v1)</span>
  return <span className="font-mono text-xs">{keys.join(', ')}</span>
}

/** FE-16.1: every assumption-set version of the tenant (GET /assumption-sets), by code then version. Read-only. */
export function AssumptionSetListView() {
  const [state, setState] = useState<Loaded<AssumptionSetList>>({ kind: 'loading' })
  const loader = useMemo(() => createLoader(fetchList, setState, 'Could not load the assumption sets'), [])

  useEffect(() => {
    loader.load()
    return () => loader.abort()
  }, [loader])

  let body
  if (state.kind === 'loading') body = <LoadingBlock rows={3} />
  else if (state.kind === 'forbidden') body = <Forbidden permission="rule:read" correlationId={state.cid} />
  else if (state.kind === 'failed') body = <LoadFailed icon={BookMarked} title="The assumption sets could not be loaded" cid={state.cid} onRetry={loader.load} />
  else if (state.kind === 'not_found')
    body = (
      <EmptyState icon={BookMarked} title="The assumption sets could not be listed" description={`The request was refused.${state.cid ? ` Correlation ID: ${state.cid}` : ''}`} />
    )
  else if (state.value.items.length === 0) body = <EmptyState icon={BookMarked} title="No assumption set is registered" />
  else
    body = (
      <Table data-testid="assumption-set-list">
        <TableHeader>
          <TableRow>
            <TableHead>Assumption set</TableHead>
            <TableHead>Version</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Owner</TableHead>
            <TableHead>Provenance</TableHead>
            <TableHead>Effective</TableHead>
            <TableHead>Approved by</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {state.value.items.map((a) => (
            <TableRow key={a.assumption_set_id} data-testid="assumption-set-row" data-code={a.code} data-assumption-set-id={a.assumption_set_id} data-status={a.status}>
              <TableCell>
                <Link href={assumptionSetHref(a.assumption_set_id)} className="font-medium text-primary hover:underline" data-testid="assumption-set-link">
                  {a.name}
                </Link>
                <div className="font-mono text-xs text-muted-foreground">{a.code}</div>
                <LabelBadges illustrative={a.illustrative} provisional={a.provisional} className="mt-1" />
              </TableCell>
              <TableCell className="font-mono text-sm">v{a.version}</TableCell>
              <TableCell>
                <VersionStatusPill status={a.status} testId="assumption-set-status" />
              </TableCell>
              <TableCell className="text-xs">{a.owner}</TableCell>
              <TableCell className="max-w-xs text-xs">{a.provenance}</TableCell>
              <TableCell className="font-mono text-xs">{effective(a.effective_from, a.effective_to)}</TableCell>
              <TableCell className="text-xs">
                {a.approved_by ?? '—'}
                {a.approved_at && <div className="text-muted-foreground">{when(a.approved_at)}</div>}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    )

  return (
    <PageSection icon={BookMarked} title={state.kind === 'ready' ? `Assumption sets (${state.value.total})` : 'Assumption sets'} description={READ_ONLY}>
      {body}
    </PageSection>
  )
}

const CRUMBS = [
  { label: 'Financial Domain', href: '/domain' },
  { label: 'Assumption sets', href: assumptionSetsHref },
]

/**
 * FE-16.1: one assumption-set version (GET /assumption-sets/{id}), the id a run manifest pins:
 * provenance, owner, effective dates, status and approval, its keys, and every version of the
 * same code. Read-only.
 */
export function AssumptionSetView({ assumptionSetId }: { assumptionSetId: string }) {
  const [state, setState] = useState<Loaded<AssumptionSetDetail>>({ kind: 'loading' })
  const loader = useMemo(() => createLoader(fetchDetail(assumptionSetId), setState, 'Could not load the assumption set'), [assumptionSetId])

  useEffect(() => {
    loader.load()
    return () => loader.abort()
  }, [loader])

  const wrap = (body: ReactNode) => (
    <>
      <PageHeader title="Assumption set" breadcrumbs={[...CRUMBS, { label: assumptionSetId.slice(0, 8) }]} />
      <div className="px-6 pb-6">{body}</div>
    </>
  )

  if (state.kind === 'loading') return wrap(<LoadingBlock rows={5} />)
  if (state.kind === 'forbidden') return wrap(<Forbidden permission="rule:read" correlationId={state.cid} />)
  if (state.kind === 'failed') return wrap(<LoadFailed icon={BookMarked} title="The assumption set could not be loaded" cid={state.cid} onRetry={loader.load} />)
  if (state.kind === 'not_found')
    return wrap(
      <EmptyState
        icon={BookMarked}
        title="Assumption set not found"
        description={`It does not exist or is not visible to you.${state.cid ? ` Correlation ID: ${state.cid}` : ''}`}
      />,
    )

  const a = state.value
  return (
    <>
      <PageHeader
        title={a.name}
        description={`${a.code} v${a.version} · ${READ_ONLY}`}
        breadcrumbs={[...CRUMBS, { label: `${a.code} v${a.version}` }]}
        actions={
          <span data-testid="assumption-set-labels">
            <LabelBadges illustrative={a.illustrative} provisional={a.provisional} />
          </span>
        }
      />
      <div className="space-y-4 px-6 pb-6" data-testid="assumption-set-detail" data-assumption-set-id={a.assumption_set_id}>
        {a.illustrative && <IllustrativeBanner />}
        <PageSection
          icon={BookMarked}
          title={`Version ${a.version}`}
          description="The assumption_set_id is what a run manifest pins."
          headerRight={<VersionStatusPill status={a.status} testId="assumption-set-detail-status" />}
        >
          <dl className="grid grid-cols-[180px_1fr] gap-y-1 text-sm">
            <Field label="Assumption set ID" testId="assumption-set-id">
              <span className="break-all font-mono text-xs">{a.assumption_set_id}</span>
            </Field>
            <Field label="Code">
              <span className="font-mono">{a.code}</span>
            </Field>
            <Field label="Owner">{a.owner}</Field>
            <Field label="Provenance" testId="assumption-set-provenance">
              {a.provenance}
            </Field>
            <Field label="Effective">
              <span className="font-mono text-xs">{effective(a.effective_from, a.effective_to)}</span>
            </Field>
            <Field label="Authored by">
              <span className="text-xs">{a.authored_by}</span> <span className="text-xs text-muted-foreground">{when(a.created_at)}</span>
            </Field>
            <Field label="Approved by">
              <span className="text-xs">{a.approved_by ?? '—'}</span> <span className="text-xs text-muted-foreground">{a.approved_at ? when(a.approved_at) : ''}</span>
            </Field>
            <Field label="Content SHA-256">
              <span className="break-all font-mono text-xs">{a.content_sha256}</span>
            </Field>
            <Field label="Assumption keys" testId="assumption-set-keys">
              <Keys keys={a.assumption_keys} />
            </Field>
          </dl>
        </PageSection>
        <PageSection icon={History} title={`Versions of ${a.code} (${a.versions.length})`} description="Every version of the same code, oldest first. Superseded versions are kept, never deleted.">
          <Table data-testid="assumption-set-versions">
            <TableHeader>
              <TableRow>
                <TableHead>Version</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Effective</TableHead>
                <TableHead>Approved</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {a.versions.map((v) => {
                const current = v.assumption_set_id === a.assumption_set_id
                return (
                  <TableRow key={v.assumption_set_id} className={cn(current && 'bg-surface-2')} data-testid="assumption-set-version" data-version={v.version} data-status={v.status}>
                    <TableCell>
                      {current ? (
                        <span className="font-mono text-sm">v{v.version} (shown)</span>
                      ) : (
                        <Link href={assumptionSetHref(v.assumption_set_id)} className="font-mono text-sm text-primary hover:underline">
                          v{v.version}
                        </Link>
                      )}
                    </TableCell>
                    <TableCell>
                      <VersionStatusPill status={v.status} />
                    </TableCell>
                    <TableCell className="font-mono text-xs">{effective(v.effective_from, v.effective_to)}</TableCell>
                    <TableCell className="text-xs">{when(v.approved_at)}</TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </PageSection>
      </div>
    </>
  )
}
