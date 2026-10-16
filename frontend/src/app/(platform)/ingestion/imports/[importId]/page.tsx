'use client'

import Link from 'next/link'
import { useParams, useRouter, useSearchParams } from 'next/navigation'
import { Suspense, useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { FileCheck2, FileUp, Layers, Pin, ScrollText } from 'lucide-react'
import { api, call } from '@/api/client'
import { AuditTimeline } from '@/components/audit/AuditTimeline'
import { Forbidden } from '@/components/auth/Forbidden'
import { RequirePermission } from '@/components/auth/RequirePermission'
import { EmptyState } from '@/components/common/EmptyState'
import { LoadFailed } from '@/components/common/LoadFailed'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { MaskedValue, isMasked } from '@/components/common/MaskedValue'
import { PageHeader } from '@/components/common/PageHeader'
import { PageSection } from '@/components/common/PageSection'
import { startLoad, type LoadFailure } from '@/components/common/load'
import { ImportProfileTab } from '@/components/ingestion/ImportProfileTab'
import { ImportStatusPill } from '@/components/ingestion/ImportStatusPill'
import { ImportsPlanned } from '@/components/ingestion/ImportsTab'
import { MapImportLink } from '@/components/ingestion/MapImportLink'
import { formatBytes, type ImportDetail } from '@/components/ingestion/imports'
import { Badge } from '@/components/ui/badge'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { IMPORT_PROFILE_UI_ENABLED, IMPORTS_UI_ENABLED, MAPPING_UI_ENABLED } from '@/config/features'

type State = { kind: 'loading' } | { kind: 'ready'; job: ImportDetail } | LoadFailure

const CRUMBS = [
  { label: 'Ingestion Workbench', href: '/ingestion' },
  { label: 'Imports', href: '/ingestion?tab=imports' },
]

function Field({ label, children, testId }: { label: string; children: ReactNode; testId?: string }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd data-testid={testId}>{children}</dd>
    </>
  )
}

/**
 * FE-9.1: Status and Profile tabs, with the open one in `?tab=` so a link can open the
 * profile. Behind IMPORT_PROFILE_UI_ENABLED: off, the page is the Phase 8 layout.
 */
function ImportTabs({ importId, status }: { importId: string; status: ReactNode }) {
  const router = useRouter()
  const params = useSearchParams()
  const tab = params.get('tab') === 'profile' ? 'profile' : 'status'
  const select = (value: string) => {
    const next = new URLSearchParams(params.toString())
    if (value === 'profile') next.set('tab', 'profile')
    else next.delete('tab')
    const query = next.toString()
    router.replace(`/ingestion/imports/${importId}${query ? `?${query}` : ''}`, { scroll: false })
  }
  return (
    <Tabs value={tab} onValueChange={select}>
      <TabsList>
        <TabsTrigger value="status" data-testid="import-tab-status">
          Status
        </TabsTrigger>
        <TabsTrigger value="profile" data-testid="import-tab-profile">
          Profile
        </TabsTrigger>
      </TabsList>
      <TabsContent value="status" className="mt-4 space-y-4">
        {status}
      </TabsContent>
      <TabsContent value="profile" className="mt-4">
        <ImportProfileTab importId={importId} />
      </TabsContent>
    </Tabs>
  )
}

function ImportView({ importId }: { importId: string }) {
  const replayed = useSearchParams().get('replayed') === '1'
  const [state, setState] = useState<State>({ kind: 'loading' })
  const request = useRef<AbortController | null>(null)

  const load = useCallback(() => {
    // A newer load (or another import) cancels the previous one; a late answer never lands.
    request.current = startLoad(
      (signal) => call(() => api.GET('/api/v1/imports/{import_id}', { params: { path: { import_id: importId } }, signal })),
      { onReady: (job) => setState({ kind: 'ready', job }), onFailure: setState, failureTitle: 'Could not load the import' },
      request.current,
    )
  }, [importId])

  useEffect(() => {
    setState({ kind: 'loading' })
    load()
    return () => request.current?.abort()
  }, [load])

  const wrap = (body: ReactNode) => (
    <>
      <PageHeader title="Import" breadcrumbs={[...CRUMBS, { label: importId.slice(0, 8) }]} />
      <div className="px-6 pb-6">{body}</div>
    </>
  )

  if (state.kind === 'forbidden') return wrap(<Forbidden permission="import:read" correlationId={state.cid} />)
  if (state.kind === 'failed')
    return wrap(
      <LoadFailed
        icon={FileUp}
        title="The import could not be loaded"
        cid={state.cid}
        onRetry={() => {
          setState({ kind: 'loading' })
          load()
        }}
      />,
    )
  if (state.kind === 'not_found')
    return wrap(
      <EmptyState
        icon={FileUp}
        title="Import not found"
        description={`It does not exist or is not visible to you.${state.cid ? ` Correlation ID: ${state.cid}` : ''}`}
      />,
    )
  if (state.kind === 'loading') return <LoadingBlock rows={6} />

  const { job } = state
  const a = job.artifact
  const totals = job.control_totals ? Object.entries(job.control_totals) : []

  const sections = (
    <>
      <PageSection icon={FileCheck2} title="3. Status" description="Status as reported by the server.">
        <dl className="grid grid-cols-[200px_1fr] gap-y-1 text-sm" data-testid="import-summary">
          <Field label="Import ID">
            <span className="font-mono text-xs" data-testid="import-id">
              {job.id}
            </span>
          </Field>
          <Field label="Status">
            <ImportStatusPill status={job.status} />
          </Field>
          <Field label="Rows read" testId="import-row-count">
            <span className="font-mono">{job.row_count}</span>
          </Field>
          <Field label="Expected rows">
            <span className="font-mono">{job.expected_row_count ?? '—'}</span>
          </Field>
          <Field label="SHA-256">
            <span className="break-all font-mono text-xs" data-testid="import-sha256">
              {a.sha256}
            </span>
          </Field>
          <Field label="File">
            {a.original_filename ?? '—'}{' '}
            <span className="text-muted-foreground">
              · {formatBytes(a.size_bytes)} · <span className="font-mono">{a.detected_mime}</span>
            </span>
          </Field>
          <Field label="Format">
            <span className="font-mono">{job.format}</span>
          </Field>
          <Field label="Classification">{a.classification}</Field>
          <Field label="Raw file">
            <span className="font-mono text-xs">{a.retention_state}</span>
            <span className="text-muted-foreground"> · write-once, never deleted</span>
          </Field>
          <Field label="Data class">
            <span className="font-mono">{job.data_class}</span>
            {job.synthetic_attested && <span className="text-muted-foreground"> · attested synthetic by the uploader</span>}
            {job.synthetic_marker && <span className="text-muted-foreground"> · file carries the synthetic marker</span>}
          </Field>
          {job.retry_of_import_id && (
            <Field label="Retry of">
              <Link href={`/ingestion/imports/${job.retry_of_import_id}`} className="font-mono text-xs text-primary hover:underline">
                {job.retry_of_import_id}
              </Link>
            </Field>
          )}
          <Field label="Uploaded">
            <span className="font-mono text-xs">
              {job.created_by} · {new Date(job.created_at).toLocaleString()}
            </span>
          </Field>
          <Field label="Correlation ID">
            <span className="font-mono text-xs">{job.correlation_id ?? '—'}</span>
          </Field>
        </dl>
      </PageSection>

      <PageSection icon={Pin} title="Pinned versions" description="Fixed at upload: later contract or schema changes do not alter this import.">
        <dl className="grid grid-cols-[200px_1fr] gap-y-1 text-sm" data-testid="import-pins">
          <Field label="Source">
            <Link href={`/ingestion/sources/${job.source_id}`} className="font-mono text-primary hover:underline">
              {job.source_code}
            </Link>
          </Field>
          <Field label="Source contract">
            <span className="font-mono">{job.contract_version != null ? `v${job.contract_version}` : '—'}</span>{' '}
            <span className="font-mono text-xs text-muted-foreground">{job.source_contract_id}</span>
          </Field>
          <Field label="Schema version" testId="import-schema-version">
            <span className="font-mono">{job.schema_code ? `${job.schema_code} v${job.schema_version}` : '—'}</span>{' '}
            <span className="font-mono text-xs text-muted-foreground">{job.schema_version_id}</span>
          </Field>
          {MAPPING_UI_ENABLED && (
            <Field label="Mapping version" testId="import-mapping-version">
              <span className="font-mono text-xs">{job.mapping_version_id ?? 'none yet (set when the import is validated)'}</span>
            </Field>
          )}
        </dl>
      </PageSection>

      {totals.length > 0 && (
        <PageSection icon={FileCheck2} title="Declared control totals" description="As declared at upload; checked against the file in a later phase.">
          <dl className="grid grid-cols-[200px_1fr] gap-y-1 text-sm" data-testid="import-control-totals">
            {totals.map(([column, value]) => (
              <Field key={column} label={column}>
                {isMasked(value) ? <MaskedValue value={value} /> : <span className="font-mono">{value}</span>}
              </Field>
            ))}
          </dl>
        </PageSection>
      )}

      <PageSection icon={Layers} title="Batches">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Batch</TableHead>
              <TableHead>Sheet</TableHead>
              <TableHead className="text-right">Rows</TableHead>
              <TableHead className="text-right">Columns</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {job.batches.map((b) => (
              <TableRow key={b.batch_number} data-testid="import-batch">
                <TableCell className="font-mono text-sm">{b.batch_number}</TableCell>
                <TableCell className="text-sm">{b.sheet_name ?? '—'}</TableCell>
                <TableCell className="text-right font-mono text-sm">{b.row_count}</TableCell>
                <TableCell className="text-right font-mono text-sm">{b.column_count}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </PageSection>

      <PageSection icon={ScrollText} title="Audit history" description="Every event for this import, newest first.">
        <RequirePermission
          permission="audit:read"
          fallback={
            <p className="text-sm text-muted-foreground" data-testid="audit-unavailable">
              The audit history needs the <code className="font-mono">audit:read</code> permission.
            </p>
          }
        >
          <AuditTimeline resourceType="import_job" resourceId={job.id} showFilters={false} />
        </RequirePermission>
      </PageSection>
    </>
  )

  return (
    <>
      <PageHeader
        title={`Import ${job.id.slice(0, 8)}`}
        description={`${job.source_code} · ${a.original_filename ?? 'unnamed file'}`}
        breadcrumbs={[...CRUMBS, { label: job.id.slice(0, 8) }]}
        actions={
          <div className="flex items-center gap-2">
            {MAPPING_UI_ENABLED && job.status === 'MAPPING_REQUIRED' && (
              <MapImportLink importId={job.id} sourceId={job.source_id} contractId={job.source_contract_id} />
            )}
            {job.data_class === 'synthetic' && <Badge variant="warning">Synthetic data</Badge>}
            <ImportStatusPill status={job.status} />
          </div>
        }
      />
      <div className="space-y-4 px-6 pb-6">
        {replayed && (
          <p className="rounded-md border border-border bg-surface-2 px-3 py-2 text-sm" data-testid="import-replayed">
            This upload was already received: the server returned the original import (idempotent replay). Nothing new
            was stored.
          </p>
        )}

        {IMPORT_PROFILE_UI_ENABLED ? <ImportTabs importId={job.id} status={sections} /> : sections}
      </div>
    </>
  )
}

/** One import (FE-8.1 step 3): status, row count, SHA-256, pinned versions and audit history. */
export default function ImportDetailPage() {
  const { importId } = useParams<{ importId: string }>()
  if (!IMPORTS_UI_ENABLED)
    return (
      <>
        <PageHeader title="Import" breadcrumbs={CRUMBS} />
        <div className="px-6 pb-6">
          <ImportsPlanned />
        </div>
      </>
    )
  return (
    <RequirePermission
      permission="import:read"
      fallback={
        <>
          <PageHeader title="Import" breadcrumbs={CRUMBS} />
          <div className="px-6 pb-6">
            <Forbidden permission="import:read" />
          </div>
        </>
      }
    >
      <Suspense fallback={<LoadingBlock rows={6} />}>
        <ImportView importId={importId} />
      </Suspense>
    </RequirePermission>
  )
}
