'use client'

import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import { Suspense, useEffect, useState } from 'react'
import { ChevronLeft, ChevronRight, Database, FileUp, Plus } from 'lucide-react'
import { api, call, ApiError, type Schemas } from '@/api/client'
import { Forbidden } from '@/components/auth/Forbidden'
import { RequirePermission } from '@/components/auth/RequirePermission'
import { useCurrentSession } from '@/components/auth/SessionProvider'
import { EmptyState } from '@/components/common/EmptyState'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { PageHeader } from '@/components/common/PageHeader'
import { PageSection } from '@/components/common/PageSection'
import { toastApiError } from '@/components/common/api-error-toast'
import { ContractStatusPill } from '@/components/ingestion/ContractStatusPill'
import { ImportsPlanned, ImportsTab } from '@/components/ingestion/ImportsTab'
import { SOURCE_KINDS } from '@/components/ingestion/sources'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { IMPORTS_UI_ENABLED } from '@/config/features'

const PAGE_SIZE = 50

type State =
  | { kind: 'loading' }
  | { kind: 'ready'; page: Schemas['SourcePage'] }
  | { kind: 'forbidden'; cid: string | null }
  | { kind: 'failed' }

function SourcesTab() {
  const [offset, setOffset] = useState(0)
  const [state, setState] = useState<State>({ kind: 'loading' })

  useEffect(() => {
    // Moving to another page cancels the previous request; a late answer never lands.
    const request = new AbortController()
    setState({ kind: 'loading' })
    call(() => api.GET('/api/v1/sources', { params: { query: { limit: PAGE_SIZE, offset } }, signal: request.signal }))
      .then((page) => !request.signal.aborted && setState({ kind: 'ready', page }))
      .catch((err: unknown) => {
        if (request.signal.aborted) return
        if (err instanceof ApiError && err.status === 403) setState({ kind: 'forbidden', cid: err.correlationId })
        else {
          setState({ kind: 'failed' })
          toastApiError(err, 'Could not load sources')
        }
      })
    return () => request.abort()
  }, [offset])

  if (state.kind === 'forbidden') return <Forbidden permission="source:read" correlationId={state.cid} />
  if (state.kind === 'loading') return <LoadingBlock rows={4} />
  if (state.kind === 'failed')
    return <EmptyState icon={Database} title="Sources could not be loaded" description="Reload the page to try again." />

  const { page } = state
  if (page.total === 0)
    return (
      <EmptyState
        icon={Database}
        title="No sources registered"
        description="A data steward registers each source system with its profile and a provisional contract."
      />
    )

  return (
    <PageSection
      icon={Database}
      title={`${page.total} source${page.total === 1 ? '' : 's'}`}
      description="Profiles and contracts stay provisional until the client confirms its real source shapes."
    >
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Source</TableHead>
            <TableHead>Kind</TableHead>
            <TableHead>Format</TableHead>
            <TableHead>Owner</TableHead>
            <TableHead>Target schema</TableHead>
            <TableHead>Contract</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {page.items.map((s) => (
            <TableRow key={s.id} data-testid="source-row" data-code={s.code}>
              <TableCell>
                <Link href={`/ingestion/sources/${s.id}`} className="font-medium text-primary hover:underline">
                  {s.name}
                </Link>
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                  <span className="font-mono">{s.code}</span>
                  {s.provisional && (
                    <Badge variant="warning" title="Not client-approved">
                      Provisional
                    </Badge>
                  )}
                </div>
              </TableCell>
              <TableCell className="text-sm">{SOURCE_KINDS[s.kind].label}</TableCell>
              <TableCell className="font-mono text-sm">{s.profile.format}</TableCell>
              <TableCell className="text-sm text-muted-foreground">{s.profile.owner}</TableCell>
              <TableCell className="font-mono text-sm">{s.current_contract?.schema_code ?? '—'}</TableCell>
              <TableCell>
                {s.current_contract ? (
                  <span className="flex items-center gap-2">
                    <span className="font-mono text-sm">v{s.current_contract.version}</span>
                    <ContractStatusPill status={s.current_contract.status} />
                  </span>
                ) : (
                  <span className="text-muted-foreground">—</span>
                )}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {page.total > PAGE_SIZE && (
        <div className="mt-3 flex items-center justify-between text-xs text-muted-foreground">
          <span>
            {page.offset + 1}–{page.offset + page.items.length} of {page.total}
          </span>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
              <ChevronLeft className="h-3.5 w-3.5" />
              Previous
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={offset + PAGE_SIZE >= page.total}
              onClick={() => setOffset(offset + PAGE_SIZE)}
            >
              Next
              <ChevronRight className="h-3.5 w-3.5" />
            </Button>
          </div>
        </div>
      )}
    </PageSection>
  )
}

const TABS = ['sources', 'imports'] as const
type Tab = (typeof TABS)[number]

/** The tabs, with the open one in `?tab=` so links (e.g. a breadcrumb) can open Imports. */
function WorkbenchTabs() {
  const router = useRouter()
  const params = useSearchParams()
  const requested = params.get('tab')
  const tab: Tab = TABS.find((t) => t === requested) ?? 'sources'

  return (
    <Tabs value={tab} onValueChange={(v) => router.replace(v === 'sources' ? '/ingestion' : `/ingestion?tab=${v}`, { scroll: false })}>
      <TabsList>
        <TabsTrigger value="sources">Sources</TabsTrigger>
        <TabsTrigger value="imports">Imports</TabsTrigger>
      </TabsList>
      <TabsContent value="sources" className="mt-4">
        <SourcesTab />
      </TabsContent>
      <TabsContent value="imports" className="mt-4">
        {IMPORTS_UI_ENABLED ? <ImportsTab /> : <ImportsPlanned />}
      </TabsContent>
    </Tabs>
  )
}

/** Ingestion Workbench landing page (FE-7.1): Sources, and Imports from Phase 8 (FE-8.2). */
export default function IngestionPage() {
  const { has } = useCurrentSession()

  return (
    <>
      <PageHeader
        title="Ingestion Workbench"
        description="Registered source systems, their profiles and versioned source contracts."
        actions={
          <>
            {IMPORTS_UI_ENABLED && (
              <RequirePermission permission="import:write" fallback={null}>
                <Button asChild size="sm" variant="outline" data-testid="new-import">
                  <Link href="/ingestion/imports/new">
                    <FileUp className="mr-1 h-3.5 w-3.5" />
                    New import
                  </Link>
                </Button>
              </RequirePermission>
            )}
            <RequirePermission permission="source:manage" fallback={null}>
              <Button asChild size="sm" data-testid="register-source">
                <Link href="/ingestion/sources/new">
                  <Plus className="mr-1 h-3.5 w-3.5" />
                  Register source
                </Link>
              </Button>
            </RequirePermission>
          </>
        }
      />
      <div className="px-6 pb-6">
        {/* Cosmetic: the API enforces source:read too (D-04). */}
        {!has('source:read') ? (
          <Forbidden permission="source:read" />
        ) : (
          <Suspense fallback={<LoadingBlock rows={4} />}>
            <WorkbenchTabs />
          </Suspense>
        )}
      </div>
    </>
  )
}
