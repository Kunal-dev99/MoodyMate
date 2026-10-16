'use client'

import { useRouter, useSearchParams } from 'next/navigation'
import { Suspense, useCallback, useEffect, useRef, useState } from 'react'
import { Check, Database, FileUp, ShieldCheck } from 'lucide-react'
import { api, callWithResponse, dataClassOf, multipart, type DataClass } from '@/api/client'
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
import { ImportsPlanned } from '@/components/ingestion/ImportsTab'
import {
  ACCEPT,
  IMPORT_MAX_BYTES,
  attestationRequired,
  emptyImportDraft,
  formatBytes,
  importErrorTitle,
  importProblems,
  isReplay,
  newIdempotencyKey,
  sizeProblem,
  toImportFields,
  type CreateImportBody,
  type ImportDraft,
} from '@/components/ingestion/imports'
import type { Source } from '@/components/ingestion/sources'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { IMPORTS_UI_ENABLED } from '@/config/features'
import { cn } from '@/lib/utils'

const SOURCE_PAGE = 200
const selectClass = 'h-9 rounded-md border border-input bg-background px-2 text-sm'

type Loaded = { sources: Source[]; total: number; dataClass: DataClass | null }
type State = { kind: 'loading' } | ({ kind: 'ready' } & Loaded) | LoadFailure

const STEPS = ['Choose a source', 'Upload', 'Status'] as const

function Steps({ current }: { current: number }) {
  return (
    <ol className="mb-4 flex flex-wrap items-center gap-2 text-sm" aria-label="Import steps" data-testid="import-steps">
      {STEPS.map((label, i) => (
        <li
          key={label}
          className={cn('flex items-center gap-2', i === current ? 'font-medium text-foreground' : 'text-muted-foreground')}
          aria-current={i === current ? 'step' : undefined}
        >
          <span
            className={cn(
              'flex h-5 w-5 items-center justify-center rounded-full border text-xs',
              i < current && 'border-primary bg-primary text-primary-foreground',
              i === current && 'border-primary text-primary',
            )}
          >
            {i < current ? <Check className="h-3 w-3" /> : i + 1}
          </span>
          {label}
          {i < STEPS.length - 1 && <span className="text-muted-foreground/50">›</span>}
        </li>
      ))}
    </ol>
  )
}

/** Step 1: the source, with the contract and format the upload will be held to. */
function ChooseSource({
  sources,
  total,
  sourceId,
  onChoose,
  onContinue,
}: {
  sources: Source[]
  total: number
  sourceId: string
  onChoose: (id: string) => void
  onContinue: () => void
}) {
  const source = sources.find((s) => s.id === sourceId)
  const contract = source?.current_contract
  return (
    <PageSection icon={Database} title="1. Choose a source" description="The file is held to the source's profile and its current contract.">
      <div className="grid gap-3 md:grid-cols-[200px_1fr]">
        <label className="text-sm font-medium" htmlFor="import-source">
          Source
        </label>
        <select id="import-source" className={selectClass} value={sourceId} onChange={(e) => onChoose(e.target.value)}>
          <option value="">Choose a source…</option>
          {sources.map((s) => (
            <option key={s.id} value={s.id} disabled={!s.current_contract}>
              {s.name} ({s.code}){s.current_contract ? '' : ' — no contract'}
            </option>
          ))}
        </select>
        {source && contract && (
          <>
            <span className="text-sm text-muted-foreground">Format</span>
            <span className="font-mono text-sm" data-testid="import-source-format">
              {source.profile.format}
            </span>
            <span className="text-sm text-muted-foreground">Contract</span>
            <span className="flex items-center gap-2 text-sm">
              <span className="font-mono">v{contract.version}</span>
              <ContractStatusPill status={contract.status} />
              <span className="text-muted-foreground">
                → <span className="font-mono">{contract.schema_code}</span>
              </span>
            </span>
            <span className="text-sm text-muted-foreground">Classification</span>
            <span className="text-sm">{source.profile.default_classification}</span>
          </>
        )}
      </div>
      {total > sources.length && (
        <p className="mt-2 text-xs text-muted-foreground">
          Showing the first {sources.length} of {total} sources.
        </p>
      )}
      <div className="mt-4 flex justify-end">
        <Button onClick={onContinue} disabled={!contract} data-testid="import-continue">
          Continue
        </Button>
      </div>
    </PageSection>
  )
}

function Wizard() {
  const router = useRouter()
  const params = useSearchParams()
  const [state, setState] = useState<State>({ kind: 'loading' })
  const [step, setStep] = useState(0)
  const [draft, setDraftState] = useState<ImportDraft>(() => emptyImportDraft(params.get('source') ?? ''))
  // One Idempotency-Key per upload attempt (D-21): kept for a retry of the same attempt,
  // dropped as soon as the form changes, so a different upload never reuses it.
  const [attemptKey, setAttemptKey] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const request = useRef<AbortController | null>(null)

  const setDraft = (next: ImportDraft) => {
    setAttemptKey(null)
    setDraftState(next)
  }

  const load = useCallback(() => {
    // X-Data-Class comes with the answer: it decides whether the attestation is asked for.
    request.current = startLoad(
      async (signal) => {
        const { data, response } = await callWithResponse(() =>
          api.GET('/api/v1/sources', { params: { query: { limit: SOURCE_PAGE, offset: 0 } }, signal }),
        )
        return { sources: data.items, total: data.total, dataClass: dataClassOf(response) }
      },
      { onReady: (loaded) => setState({ kind: 'ready', ...loaded }), onFailure: setState, failureTitle: 'Could not load sources' },
      request.current,
    )
  }, [])

  useEffect(() => {
    setState({ kind: 'loading' })
    load()
    return () => request.current?.abort()
  }, [load])

  if (state.kind === 'forbidden') return <Forbidden permission="source:read" correlationId={state.cid} />
  if (state.kind === 'loading') return <LoadingBlock rows={4} />
  if (state.kind === 'failed')
    return (
      <LoadFailed
        icon={Database}
        title="Sources could not be loaded"
        cid={state.cid}
        onRetry={() => {
          setState({ kind: 'loading' })
          load()
        }}
      />
    )
  if (state.kind === 'not_found')
    return (
      <EmptyState
        icon={Database}
        title="Sources could not be listed"
        description={`The request was refused.${state.cid ? ` Correlation ID: ${state.cid}` : ''}`}
      />
    )

  const { sources, total, dataClass } = state
  if (sources.length === 0)
    return <EmptyState icon={Database} title="No sources registered" description="Register a source before uploading a file for it." />

  const source = sources.find((s) => s.id === draft.sourceId)
  const contract = source?.current_contract

  if (step === 0 || !source || !contract)
    return (
      <>
        <Steps current={0} />
        <ChooseSource
          sources={sources}
          total={total}
          sourceId={draft.sourceId}
          onChoose={(id) => setDraft({ ...emptyImportDraft(id) })}
          onContinue={() => setStep(1)}
        />
      </>
    )

  const problems = importProblems(draft, dataClass)
  const fileProblem = draft.file ? sizeProblem(draft.file.size) : null
  const askAttestation = attestationRequired(dataClass)

  const upload = async () => {
    if (!draft.file || problems.length > 0) return
    const key = attemptKey ?? newIdempotencyKey()
    setAttemptKey(key)
    setUploading(true)
    try {
      const { data, response } = await callWithResponse(() =>
        api.POST('/api/v1/imports', {
          params: { header: { 'Idempotency-Key': key } },
          ...multipart<CreateImportBody>(toImportFields(draft, draft.file as File, dataClass)),
        }),
      )
      router.push(`/ingestion/imports/${data.id}${isReplay(response) ? '?replayed=1' : ''}`)
    } catch (err) {
      setUploading(false)
      toastApiError(err, importErrorTitle(err))
    }
  }

  return (
    <>
      <Steps current={1} />
      <div className="space-y-4">
        <PageSection
          icon={FileUp}
          title="2. Upload"
          description={`For ${source.name} (${source.code}): a ${source.profile.format} file, at most ${formatBytes(IMPORT_MAX_BYTES)}.`}
          headerRight={
            <Button variant="ghost" size="sm" onClick={() => setStep(0)} disabled={uploading}>
              Change source
            </Button>
          }
        >
          <div className="grid gap-3 md:grid-cols-[200px_1fr]">
            <label className="text-sm font-medium" htmlFor="import-file">
              File
            </label>
            <div className="space-y-1">
              <Input
                id="import-file"
                type="file"
                accept={ACCEPT[source.profile.format]}
                onChange={(e) => setDraft({ ...draft, file: e.target.files?.[0] ?? null })}
                data-testid="import-file"
              />
              {draft.file && (
                <p className={cn('text-xs', fileProblem ? 'text-destructive' : 'text-muted-foreground')} data-testid="import-file-size">
                  {fileProblem ?? `${draft.file.name} · ${formatBytes(draft.file.size)}`}
                </p>
              )}
            </div>
            <label className="text-sm font-medium" htmlFor="expected-rows">
              Expected row count
            </label>
            <Input
              id="expected-rows"
              inputMode="numeric"
              className="max-w-[200px] font-mono"
              placeholder="Optional"
              value={draft.expectedRowCount}
              onChange={(e) => setDraft({ ...draft, expectedRowCount: e.target.value })}
            />
            {contract.control_total_columns.map((column) => (
              <ControlTotal
                key={column}
                column={column}
                value={draft.controlTotals[column] ?? ''}
                onChange={(v) => setDraft({ ...draft, controlTotals: { ...draft.controlTotals, [column]: v } })}
              />
            ))}
          </div>
        </PageSection>

        {askAttestation && (
          <PageSection
            icon={ShieldCheck}
            title="Synthetic-data attestation"
            description={
              dataClass === 'synthetic'
                ? 'This environment holds synthetic data only (X-Data-Class: synthetic).'
                : 'The environment did not state its data class, so the attestation is required.'
            }
            accent="warning"
          >
            <div className="flex items-start gap-2">
              <Checkbox
                id="synthetic-attested"
                checked={draft.attested}
                onCheckedChange={(v) => setDraft({ ...draft, attested: v === true })}
                data-testid="synthetic-attested"
              />
              <label htmlFor="synthetic-attested" className="text-sm leading-tight">
                I confirm this file holds synthetic data only and no client data. The confirmation is recorded with the import.
              </label>
            </div>
          </PageSection>
        )}

        {problems.length > 0 && (
          <ul className="list-disc pl-5 text-sm text-destructive" data-testid="import-problems">
            {problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        )}
        <div className="flex items-center justify-end gap-2">
          {dataClass === 'synthetic' && <Badge variant="warning">Synthetic environment</Badge>}
          <Button variant="outline" onClick={() => router.push('/ingestion?tab=imports')} disabled={uploading}>
            Cancel
          </Button>
          <Button onClick={upload} disabled={problems.length > 0 || uploading} data-testid="import-upload">
            {uploading ? 'Uploading…' : 'Upload'}
          </Button>
        </div>
      </div>
    </>
  )
}

function ControlTotal({ column, value, onChange }: { column: string; value: string; onChange: (v: string) => void }) {
  const id = `control-total-${column}`
  return (
    <>
      <label className="text-sm font-medium" htmlFor={id}>
        Control total <span className="font-mono">{column}</span>
      </label>
      <Input id={id} inputMode="decimal" className="max-w-[260px] font-mono" placeholder="Optional, e.g. 1234.50" value={value} onChange={(e) => onChange(e.target.value)} />
    </>
  )
}

/** Import wizard (FE-8.1): choose a source, upload, then the import's status page. */
export default function NewImportPage() {
  return (
    <>
      <PageHeader
        title="New import"
        description="Upload a file for a registered source. The raw file is kept unchanged and every row is recorded."
        breadcrumbs={[
          { label: 'Ingestion Workbench', href: '/ingestion' },
          { label: 'Imports', href: '/ingestion?tab=imports' },
          { label: 'New' },
        ]}
      />
      <div className="px-6 pb-6">
        {!IMPORTS_UI_ENABLED ? (
          <ImportsPlanned />
        ) : (
          <RequirePermission permission="import:write">
            <Suspense fallback={<LoadingBlock rows={4} />}>
              <Wizard />
            </Suspense>
          </RequirePermission>
        )}
      </div>
    </>
  )
}
