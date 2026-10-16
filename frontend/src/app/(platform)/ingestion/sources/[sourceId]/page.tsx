'use client'

import Link from 'next/link'
import { useParams } from 'next/navigation'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Database, FileCode2, FileUp, GitBranchPlus, ListChecks, ScrollText } from 'lucide-react'
import { api, call } from '@/api/client'
import { AuditTimeline } from '@/components/audit/AuditTimeline'
import { Forbidden } from '@/components/auth/Forbidden'
import { RequirePermission } from '@/components/auth/RequirePermission'
import { EmptyState } from '@/components/common/EmptyState'
import { LoadFailed } from '@/components/common/LoadFailed'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { startLoad, type LoadFailure } from '@/components/common/load'
import { PageHeader } from '@/components/common/PageHeader'
import { PageSection } from '@/components/common/PageSection'
import { toastApiError } from '@/components/common/api-error-toast'
import { ContractEditor } from '@/components/ingestion/ContractEditor'
import { ContractStatusPill } from '@/components/ingestion/ContractStatusPill'
import {
  SOURCE_KINDS,
  contractProblems,
  delimiterLabel,
  draftFromContract,
  errorTitle,
  newestFirst,
  toContractIn,
  type Contract,
  type ContractDraft,
  type SourceDetail,
} from '@/components/ingestion/sources'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { IMPORTS_UI_ENABLED } from '@/config/features'

type State = { kind: 'loading' } | { kind: 'ready'; source: SourceDetail } | LoadFailure

function ContractColumns({ c }: { c: Contract }) {
  const keys = new Set(c.business_key_columns)
  const totals = new Set(c.control_total_columns)
  return (
    <details>
      <summary className="cursor-pointer text-sm text-muted-foreground">{c.expected_columns.length} columns</summary>
      <ul className="mt-1 space-y-0.5 text-xs">
        {c.expected_columns.map((col) => (
          <li key={col.name} className="flex flex-wrap items-center gap-1">
            <span className="font-mono">{col.name}</span>
            <span className="text-muted-foreground">{col.required ? 'required' : 'optional'}</span>
            {keys.has(col.name) && <Badge variant="info">key</Badge>}
            {totals.has(col.name) && <Badge variant="secondary">control total</Badge>}
          </li>
        ))}
      </ul>
    </details>
  )
}

function AddContractVersion({ source, onAdded }: { source: SourceDetail; onAdded: () => void }) {
  const [draft, setDraft] = useState<ContractDraft | null>(null)
  const [saving, setSaving] = useState(false)

  if (!draft)
    return (
      <Button
        size="sm"
        data-testid="add-contract-version"
        disabled={!source.current_contract}
        onClick={() => source.current_contract && setDraft(draftFromContract(source.current_contract))}
      >
        <GitBranchPlus className="mr-1 h-3.5 w-3.5" />
        Add contract version
      </Button>
    )

  const problems = contractProblems(draft)
  const create = async () => {
    setSaving(true)
    try {
      await call(() =>
        api.POST('/api/v1/sources/{source_id}/contracts', { params: { path: { source_id: source.id } }, body: toContractIn(draft) }),
      )
      setDraft(null)
      onAdded()
    } catch (err) {
      toastApiError(err, errorTitle(err, 'Could not add the contract version'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">
        Starts from v{source.current_contract?.version}. The new version is Provisional; the current one becomes Superseded
        and is kept unchanged.
      </p>
      <ContractEditor draft={draft} onChange={setDraft} showChangeSummary />
      {problems.length > 0 && (
        <ul className="list-disc pl-5 text-sm text-destructive" data-testid="draft-problems">
          {problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}
      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={() => setDraft(null)} disabled={saving}>
          Cancel
        </Button>
        <Button onClick={create} disabled={problems.length > 0 || saving} data-testid="create-contract-version">
          Create version
        </Button>
      </div>
    </div>
  )
}

/** One source system (FE-7.2): profile, contract versions and its audit history. */
export default function SourceDetailPage() {
  const { sourceId } = useParams<{ sourceId: string }>()
  const [state, setState] = useState<State>({ kind: 'loading' })
  // Bumped after a change so the scoped audit timeline reloads with the new events.
  const [revision, setRevision] = useState(0)
  const request = useRef<AbortController | null>(null)

  const load = useCallback(() => {
    // A newer load (or another source) cancels the previous one; a late answer never lands.
    // A network or server failure ends in a retryable error state, never an endless loading block.
    request.current = startLoad(
      (signal) => call(() => api.GET('/api/v1/sources/{source_id}', { params: { path: { source_id: sourceId } }, signal })),
      { onReady: (source) => setState({ kind: 'ready', source }), onFailure: setState, failureTitle: 'Could not load the source' },
      request.current,
    )
  }, [sourceId])

  useEffect(() => {
    setState({ kind: 'loading' })
    load()
    return () => request.current?.abort()
  }, [load])

  if (state.kind === 'forbidden') return <Forbidden permission="source:read" correlationId={state.cid} />
  if (state.kind === 'failed')
    return (
      <div className="p-6">
        <LoadFailed
          icon={Database}
          title="The source could not be loaded"
          cid={state.cid}
          onRetry={() => {
            setState({ kind: 'loading' })
            load()
          }}
        />
      </div>
    )
  if (state.kind === 'not_found') {
    return (
      <div className="p-6">
        <EmptyState
          icon={Database}
          title="Source not found"
          description={`It does not exist or is not visible to you.${state.cid ? ` Correlation ID: ${state.cid}` : ''}`}
        />
      </div>
    )
  }
  if (state.kind === 'loading') return <LoadingBlock rows={6} />

  const { source } = state
  const p = source.profile

  return (
    <>
      <PageHeader
        title={source.name}
        description={source.description ?? undefined}
        breadcrumbs={[{ label: 'Ingestion Workbench', href: '/ingestion' }, { label: 'Sources', href: '/ingestion' }, { label: source.code }]}
        actions={
          <div className="flex items-center gap-2">
            {IMPORTS_UI_ENABLED && source.current_contract && (
              <RequirePermission permission="import:write" fallback={null}>
                <Button asChild size="sm" variant="outline" data-testid="source-new-import">
                  <Link href={`/ingestion/imports/new?source=${source.id}`}>
                    <FileUp className="mr-1 h-3.5 w-3.5" />
                    New import
                  </Link>
                </Button>
              </RequirePermission>
            )}
            {source.provisional && <Badge variant="warning">Provisional profile</Badge>}
            {source.current_contract && (
              <span className="flex items-center gap-1 text-sm text-muted-foreground">
                Contract <span className="font-mono">v{source.current_contract.version}</span>
                <ContractStatusPill status={source.current_contract.status} />
              </span>
            )}
          </div>
        }
      />
      <div className="space-y-4 px-6 pb-6">
        <PageSection icon={FileCode2} title="Profile" description={p.provisional ? 'Provisional until the client approves this source profile.' : undefined}>
          <dl className="grid grid-cols-[200px_1fr] gap-y-1 text-sm" data-testid="source-profile">
            <dt className="text-muted-foreground">Code</dt>
            <dd className="font-mono">{source.code}</dd>
            <dt className="text-muted-foreground">Kind</dt>
            <dd>{SOURCE_KINDS[source.kind].label}</dd>
            <dt className="text-muted-foreground">Format</dt>
            <dd className="font-mono">{p.format}</dd>
            <dt className="text-muted-foreground">Encoding</dt>
            <dd className="font-mono">{p.encoding}</dd>
            {p.format === 'CSV' && (
              <>
                <dt className="text-muted-foreground">Delimiter</dt>
                <dd>{delimiterLabel(p.delimiter)}</dd>
              </>
            )}
            {p.format === 'XLSX' && (
              <>
                <dt className="text-muted-foreground">Sheet</dt>
                <dd>{p.sheet_name ?? 'First sheet'}</dd>
              </>
            )}
            <dt className="text-muted-foreground">Cadence</dt>
            <dd>{p.cadence ?? '—'}</dd>
            <dt className="text-muted-foreground">Owner</dt>
            <dd>{p.owner}</dd>
            <dt className="text-muted-foreground">Default classification</dt>
            <dd>{p.default_classification}</dd>
            <dt className="text-muted-foreground">Registered</dt>
            <dd className="font-mono text-xs">
              {source.created_by} · {new Date(source.created_at).toLocaleString()}
            </dd>
          </dl>
        </PageSection>

        <PageSection icon={ListChecks} title="Contract versions" description="Versions are never edited: a new version supersedes the previous one.">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Version</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Target schema</TableHead>
                <TableHead>Columns</TableHead>
                <TableHead>Business key</TableHead>
                <TableHead>Control totals</TableHead>
                <TableHead>Content SHA-256</TableHead>
                <TableHead>Created</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {newestFirst(source.contracts).map((c) => (
                <TableRow key={c.id} data-testid="contract-row" data-version={c.version} data-status={c.status}>
                  <TableCell className="font-mono text-sm">v{c.version}</TableCell>
                  <TableCell>
                    <ContractStatusPill status={c.status} />
                  </TableCell>
                  <TableCell className="font-mono text-sm">{c.schema_code}</TableCell>
                  <TableCell>
                    <ContractColumns c={c} />
                  </TableCell>
                  <TableCell className="font-mono text-xs">{c.business_key_columns.join(', ')}</TableCell>
                  <TableCell className="font-mono text-xs">{c.control_total_columns.join(', ') || '—'}</TableCell>
                  <TableCell className="font-mono text-xs" title={c.content_sha256}>
                    {c.content_sha256.slice(0, 12)}…
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    <span className="font-mono">{c.created_by}</span>
                    <div>{new Date(c.created_at).toLocaleString()}</div>
                    {c.change_summary && <div className="text-foreground">“{c.change_summary}”</div>}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </PageSection>

        <RequirePermission permission="source:manage" fallback={null}>
          <PageSection icon={GitBranchPlus} title="New contract version">
            <AddContractVersion
              key={source.contract_count}
              source={source}
              onAdded={() => {
                load()
                setRevision((r) => r + 1)
              }}
            />
          </PageSection>
        </RequirePermission>

        <PageSection icon={ScrollText} title="Audit history" description="Every change to this source, newest first.">
          <RequirePermission
            permission="audit:read"
            fallback={
              <p className="text-sm text-muted-foreground" data-testid="audit-unavailable">
                The audit history needs the <code className="font-mono">audit:read</code> permission.
              </p>
            }
          >
            <AuditTimeline key={revision} resourceType="source_system" resourceId={source.id} showFilters={false} />
          </RequirePermission>
        </PageSection>
      </div>
    </>
  )
}
