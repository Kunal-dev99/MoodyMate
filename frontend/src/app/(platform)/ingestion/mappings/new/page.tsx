'use client'

import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import { Suspense, useCallback, useEffect, useRef, useState } from 'react'
import { GitMerge } from 'lucide-react'
import { api, call, ApiError } from '@/api/client'
import { Forbidden } from '@/components/auth/Forbidden'
import { RequirePermission } from '@/components/auth/RequirePermission'
import { EmptyState } from '@/components/common/EmptyState'
import { LoadFailed } from '@/components/common/LoadFailed'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { PageHeader } from '@/components/common/PageHeader'
import { PageSection } from '@/components/common/PageSection'
import { toastApiError } from '@/components/common/api-error-toast'
import { startLoad, type LoadFailure } from '@/components/common/load'
import { ContractStatusPill } from '@/components/ingestion/ContractStatusPill'
import { MappingsPlanned } from '@/components/ingestion/MappingsPlanned'
import { RefusalNotice } from '@/components/ingestion/RefusalNotice'
import { mappingErrorTitle, mappingRefusal, suggestedRules, type MappingSummary, type Refusal } from '@/components/ingestion/mappings'
import type { SourceDetail } from '@/components/ingestion/sources'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { MAPPING_UI_ENABLED } from '@/config/features'
import { TruncationNote } from '@/components/common/ListPager'
import { truncationOf, type Truncation } from '@/lib/paging'

const CRUMBS = [
  { label: 'Ingestion Workbench', href: '/ingestion' },
  { label: 'Mappings', href: '/ingestion?tab=mappings' },
  { label: 'New' },
]
const selectClass = 'h-9 rounded-md border border-input bg-background px-2 text-sm'

type Column = { name: string; suggested_target_field: string | null }
type Ready = {
  source: SourceDetail
  contractId: string
  columns: Column[]
  /** True when the columns come from an import's profile (suggestions available). */
  profiled: boolean
  existing: MappingSummary | null
  /** Q18: the existing-mapping check read one capped page of the source's mappings; set when the server cut it. */
  mappingsNote: Truncation | null
}
type State =
  | { kind: 'loading' }
  | { kind: 'choose'; sources: { id: string; code: string; name: string }[]; sourcesNote: Truncation | null }
  | ({ kind: 'ready' } & Ready)
  | LoadFailure

/** Loads what a new mapping starts from: the import's profile, else the source's current contract. */
async function loadStart(sourceId: string | null, importId: string | null, signal: AbortSignal): Promise<State> {
  let columns: Column[] | null = null
  let contractId: string | null = null
  if (importId) {
    const job = await call(() => api.GET('/api/v1/imports/{import_id}', { params: { path: { import_id: importId } }, signal }))
    sourceId = job.source_id
    contractId = job.source_contract_id
    try {
      const profile = await call(() => api.GET('/api/v1/imports/{import_id}/profile', { params: { path: { import_id: importId } }, signal }))
      columns = profile.columns.map((c) => ({ name: c.name, suggested_target_field: c.suggested_target_field }))
    } catch (err) {
      if (!(err instanceof ApiError) || err.status !== 404) throw err
    }
  }
  if (!sourceId) {
    const page = await call(() => api.GET('/api/v1/sources', { params: { query: { limit: 200 } }, signal }))
    return { kind: 'choose', sources: page.items.map((s) => ({ id: s.id, code: s.code, name: s.name })), sourcesNote: truncationOf(page, 'sources') }
  }
  const source = await call(() => api.GET('/api/v1/sources/{source_id}', { params: { path: { source_id: sourceId } }, signal }))
  const contract = source.contracts.find((c) => c.id === contractId) ?? source.current_contract
  if (!contract) return { kind: 'not_found', status: null, cid: null }
  const mappings = await call(() => api.GET('/api/v1/mappings', { params: { query: { source_id: source.id, limit: 200 } }, signal }))
  return {
    kind: 'ready',
    source,
    contractId: contract.id,
    columns: columns ?? contract.expected_columns.map((c) => ({ name: c.name, suggested_target_field: null })),
    profiled: columns !== null,
    existing: mappings.items.find((m) => m.source_contract_id === contract.id) ?? null,
    mappingsNote: truncationOf(mappings, 'mappings of this source'),
  }
}

function defaultCode(source: SourceDetail, contractId: string): string {
  const schema = source.contracts.find((c) => c.id === contractId)?.schema_code ?? 'mapping'
  return `${source.code}_${schema}`.toLowerCase().replace(/[^a-z0-9_]/g, '_').replace(/^[^a-z]+/, '').slice(0, 100)
}

function CreateForm({ ready, importId }: { ready: Ready; importId: string | null }) {
  const router = useRouter()
  const { source, contractId, columns, profiled, mappingsNote } = ready
  const contract = source.contracts.find((c) => c.id === contractId)
  const [code, setCode] = useState(() => defaultCode(source, contractId))
  const [name, setName] = useState(`${source.name} to ${contract?.schema_code ?? 'schema'}`)
  const [description, setDescription] = useState('')
  const [summary, setSummary] = useState('')
  const [useSuggestions, setUseSuggestions] = useState(profiled)
  const [saving, setSaving] = useState(false)
  const [refusal, setRefusal] = useState<Refusal | null>(null)

  const problems: string[] = []
  if (!/^[a-z][a-z0-9_]{0,99}$/.test(code)) problems.push('Code: lower_snake_case, starting with a letter.')
  if (!name.trim()) problems.push('Give the mapping a name.')

  const create = async () => {
    setSaving(true)
    setRefusal(null)
    try {
      const m = await call(() =>
        api.POST('/api/v1/mappings', {
          body: {
            code,
            name: name.trim(),
            description: description.trim() || null,
            source_contract_id: contractId,
            rules: suggestedRules(columns, useSuggestions),
            change_summary: summary.trim() || null,
          },
        }),
      )
      router.push(`/ingestion/mappings/${m.id}${importId ? `?import=${importId}` : ''}`)
    } catch (err) {
      toastApiError(err, mappingErrorTitle(err, 'Could not create the mapping'))
      setRefusal(mappingRefusal(err, 'Could not create the mapping'))
      setSaving(false)
    }
  }

  return (
    <PageSection icon={GitMerge} title="New mapping" description="Creates the mapping with Draft version 1, pinned to the target schema's active version.">
      <dl className="mb-4 grid grid-cols-[200px_1fr] gap-y-1 text-sm">
        <dt className="text-muted-foreground">Source</dt>
        <dd className="font-mono">{source.code}</dd>
        <dt className="text-muted-foreground">Source contract</dt>
        <dd className="flex items-center gap-2">
          <span className="font-mono">v{contract?.version}</span>
          {contract && <ContractStatusPill status={contract.status} />}
        </dd>
        <dt className="text-muted-foreground">Target schema</dt>
        <dd className="font-mono">{contract?.schema_code}</dd>
        <dt className="text-muted-foreground">Columns</dt>
        <dd className="text-xs">
          {columns.length} {profiled ? "from the import's profile" : "from the contract's expected columns"}
        </dd>
      </dl>
      <div className="grid max-w-2xl gap-3">
        <div className="grid gap-1">
          <Label htmlFor="mapping-code">Code</Label>
          <Input id="mapping-code" value={code} onChange={(e) => setCode(e.target.value)} maxLength={100} className="font-mono" />
        </div>
        <div className="grid gap-1">
          <Label htmlFor="mapping-name">Name</Label>
          <Input id="mapping-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={200} />
        </div>
        <div className="grid gap-1">
          <Label htmlFor="mapping-description">Description (optional)</Label>
          <Textarea id="mapping-description" value={description} onChange={(e) => setDescription(e.target.value)} maxLength={1000} />
        </div>
        <div className="grid gap-1">
          <Label htmlFor="mapping-summary">Change summary for version 1 (optional)</Label>
          <Input id="mapping-summary" value={summary} onChange={(e) => setSummary(e.target.value)} maxLength={1000} />
        </div>
        <label className="flex items-start gap-2 text-sm">
          <Checkbox
            checked={useSuggestions}
            disabled={!profiled}
            onCheckedChange={(v) => setUseSuggestions(v === true)}
            data-testid="mapping-use-suggestions"
            className="mt-0.5"
          />
          <span>
            Start from the profile&apos;s suggested target fields (Copy). Other columns start as Preserve.
            <span className="block text-xs text-muted-foreground">
              {profiled
                ? 'Suggestions are name matches only: review every row in the workbench and run a dry run before submitting.'
                : 'No import profile was given, so every column starts as Preserve.'}
            </span>
          </span>
        </label>
        {problems.length > 0 && (
          <ul className="list-disc pl-5 text-sm text-destructive" data-testid="mapping-create-problems">
            {problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        )}
        {refusal && <RefusalNotice refusal={refusal} />}
        {/* Q18: an existing mapping beyond the page read is not found here; the server still refuses a duplicate. */}
        <TruncationNote note={mappingsNote} testId="mapping-existing-truncated" />
        <div>
          <Button onClick={create} disabled={saving || problems.length > 0} data-testid="mapping-create">
            Create mapping
          </Button>
        </div>
      </div>
    </PageSection>
  )
}

function NewMapping() {
  const params = useSearchParams()
  const router = useRouter()
  const sourceId = params.get('source')
  const importId = params.get('import')
  const [state, setState] = useState<State>({ kind: 'loading' })
  const request = useRef<AbortController | null>(null)

  const load = useCallback(() => {
    request.current = startLoad((signal) => loadStart(sourceId, importId, signal), {
      onReady: setState,
      onFailure: setState,
      failureTitle: 'Could not load the source',
    }, request.current)
  }, [sourceId, importId])

  useEffect(() => {
    setState({ kind: 'loading' })
    load()
    return () => request.current?.abort()
  }, [load])

  if (state.kind === 'loading') return <LoadingBlock rows={6} />
  if (state.kind === 'forbidden') return <Forbidden correlationId={state.cid} />
  if (state.kind === 'failed')
    return (
      <LoadFailed
        icon={GitMerge}
        title="The source could not be loaded"
        cid={state.cid}
        onRetry={() => {
          setState({ kind: 'loading' })
          load()
        }}
      />
    )
  if (state.kind === 'not_found')
    return <EmptyState icon={GitMerge} title="Nothing to map" description="The source or import does not exist, is not visible to you, or has no contract." />
  if (state.kind === 'choose')
    return (
      <PageSection icon={GitMerge} title="Choose a source" description="A mapping maps the source's current contract to its target schema.">
        <select
          className={selectClass}
          aria-label="Source"
          data-testid="mapping-source"
          defaultValue=""
          onChange={(e) => e.target.value && router.replace(`/ingestion/mappings/new?source=${e.target.value}`)}
        >
          <option value="" disabled>
            Choose a source…
          </option>
          {state.sources.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name} ({s.code})
            </option>
          ))}
        </select>
        <TruncationNote note={state.sourcesNote} testId="mapping-source-truncated" className="mt-2" />
      </PageSection>
    )
  if (state.existing)
    return (
      <EmptyState
        icon={GitMerge}
        title="This contract already has a mapping"
        description="A change to it is a new version of that mapping, made in its workbench."
        action={
          <Button asChild size="sm" data-testid="mapping-existing">
            <Link href={`/ingestion/mappings/${state.existing.id}${importId ? `?import=${importId}` : ''}`}>Open {state.existing.code}</Link>
          </Button>
        }
      />
    )
  return <CreateForm ready={state} importId={importId} />
}

/** Create a mapping for a source contract (FE-10.1), optionally from an import's profile. */
export default function NewMappingPage() {
  return (
    <>
      <PageHeader title="New mapping" breadcrumbs={CRUMBS} />
      <div className="px-6 pb-6">
        {!MAPPING_UI_ENABLED ? (
          <MappingsPlanned />
        ) : (
          <RequirePermission permission="mapping:propose">
            <Suspense fallback={<LoadingBlock rows={6} />}>
              <NewMapping />
            </Suspense>
          </RequirePermission>
        )}
      </div>
    </>
  )
}
