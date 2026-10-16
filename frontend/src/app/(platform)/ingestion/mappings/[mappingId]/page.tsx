'use client'

import Link from 'next/link'
import { useParams, useRouter, useSearchParams } from 'next/navigation'
import { Suspense, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { CheckCircle2, FlaskConical, Gavel, GitMerge, History, Save, Sparkles, TableProperties } from 'lucide-react'
import { api, call, type Schemas } from '@/api/client'
import { Forbidden } from '@/components/auth/Forbidden'
import { RequirePermission } from '@/components/auth/RequirePermission'
import { useCurrentSession } from '@/components/auth/SessionProvider'
import { EmptyState } from '@/components/common/EmptyState'
import { LoadFailed } from '@/components/common/LoadFailed'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { PageHeader } from '@/components/common/PageHeader'
import { PageSection } from '@/components/common/PageSection'
import { toastApiError } from '@/components/common/api-error-toast'
import { startLoad, type LoadFailure } from '@/components/common/load'
import { ImportStatusPill } from '@/components/ingestion/ImportStatusPill'
import { MappingGrid } from '@/components/ingestion/MappingGrid'
import { MappingStatusPill } from '@/components/ingestion/MappingStatusPill'
import { MappingsPlanned } from '@/components/ingestion/MappingsPlanned'
import { RefusalNotice } from '@/components/ingestion/RefusalNotice'
import {
  RequestScope,
  activeVersion,
  applySuggestions,
  buildRules,
  bySeverity,
  canDecideVersion,
  canSubmitVersion,
  draftFromVersion,
  isDirty,
  mappingErrorTitle,
  refusalOf,
  type DraftRule,
  type MappingDetail,
  type MappingValidation,
  type MappingVersion,
  type Refusal,
} from '@/components/ingestion/mappings'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Textarea } from '@/components/ui/textarea'
import { MAPPING_UI_ENABLED } from '@/config/features'
import { cn } from '@/lib/utils'

type State = { kind: 'loading' } | { kind: 'ready'; m: MappingDetail } | LoadFailure
type Profile = Schemas['ImportProfile']
type ImportSummary = Schemas['ImportSummary']
type SchemaField = Schemas['SchemaFieldOut']
/** What the selected import contributes: loading, its profile, or none (no import / no profile). */
type ProfileState = { kind: 'none' } | { kind: 'loading' } | { kind: 'ready'; profile: Profile }

const CRUMBS = [
  { label: 'Ingestion Workbench', href: '/ingestion' },
  { label: 'Mappings', href: '/ingestion?tab=mappings' },
]
const selectClass = 'h-9 max-w-full rounded-md border border-input bg-background px-2 text-sm'
const DISCARD = 'You have unsaved edits to this draft. Discard them?'

const OUTCOME_VARIANT = { OK: 'success', WARNINGS: 'warning', FAILED: 'destructive' } as const
const SEVERITY_VARIANT = { BLOCKER: 'destructive', ERROR: 'destructive', WARNING: 'warning' } as const

function Field({ label, children, testId }: { label: string; children: ReactNode; testId?: string }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd data-testid={testId}>{children}</dd>
    </>
  )
}

/** The dry run's result (BE-10.4): outcome, issues (never values) and counts per rule. */
function ValidationResult({ v }: { v: MappingValidation }) {
  return (
    <div className="space-y-3" data-testid="mapping-validation" data-outcome={v.outcome} data-ok={String(v.ok)}>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Badge variant={OUTCOME_VARIANT[v.outcome]} data-testid="mapping-validation-outcome">
          {v.outcome}
        </Badge>
        <span className="font-mono text-xs">
          v{v.version} · {v.row_count} rows · {v.blocker_count} blockers · {v.error_count} errors · {v.warning_count} warnings
        </span>
        <span className="text-xs text-muted-foreground">
          {v.ok ? 'This version may be submitted for approval.' : 'Fix the blockers and errors, save a new version and validate again.'}
        </span>
      </div>
      {v.issues.length > 0 && (
        <ul className="space-y-1" data-testid="mapping-issues">
          {bySeverity(v.issues).map((i, n) => (
            <li key={n} className="flex flex-wrap items-center gap-2 text-sm" data-testid="mapping-issue" data-severity={i.severity} data-code={i.code} data-target={i.target_field ?? ''}>
              <Badge variant={SEVERITY_VARIANT[i.severity]}>{i.severity}</Badge>
              <span className="font-mono text-xs text-muted-foreground">{i.code}</span>
              <span>{i.message}</span>
              {i.count != null && <span className="font-mono text-xs text-muted-foreground">({i.count} rows)</span>}
              {i.reasons && (
                <span className="font-mono text-xs text-muted-foreground">
                  {Object.entries(i.reasons)
                    .map(([k, c]) => `${k}: ${c}`)
                    .join(' · ')}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Rule</TableHead>
            <TableHead>Source column</TableHead>
            <TableHead>Target</TableHead>
            <TableHead>Transform</TableHead>
            <TableHead className="text-right">Rows</TableHead>
            <TableHead className="text-right">Mapped</TableHead>
            <TableHead className="text-right">Empty</TableHead>
            <TableHead className="text-right">Failed</TableHead>
            <TableHead>Failure reasons</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {v.rule_results.map((r) => (
            <TableRow key={r.position} data-testid="mapping-rule-result" data-failed={r.failed}>
              <TableCell className="font-mono text-xs">{r.position}</TableCell>
              <TableCell className="font-mono text-xs">{r.source_column ?? '—'}</TableCell>
              <TableCell className="font-mono text-xs">
                {r.column_policy === 'PRESERVE' ? <span className="text-muted-foreground">preserved</span> : `${r.target_field} ${r.target_type ? `(${r.target_type})` : ''}`}
              </TableCell>
              <TableCell className="font-mono text-xs">{r.transform}</TableCell>
              <TableCell className="text-right font-mono text-xs">{r.rows}</TableCell>
              <TableCell className="text-right font-mono text-xs">{r.mapped}</TableCell>
              <TableCell className="text-right font-mono text-xs">{r.empty}</TableCell>
              <TableCell className={cn('text-right font-mono text-xs', r.failed > 0 && 'text-destructive')}>{r.failed}</TableCell>
              <TableCell className="font-mono text-xs text-muted-foreground">
                {Object.entries(r.failures)
                  .map(([k, c]) => `${k}: ${c}`)
                  .join(' · ') || '—'}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <p className="font-mono text-xs text-muted-foreground">
        Validation {v.id} · import {v.import_id} · content {v.mapping_sha256.slice(0, 12)}… · by {v.created_by} · {new Date(v.created_at).toLocaleString()}
      </p>
    </div>
  )
}

function Workbench({ mappingId }: { mappingId: string }) {
  const router = useRouter()
  const params = useSearchParams()
  const { me, has } = useCurrentSession()
  const mayPropose = has('mapping:propose')
  const [state, setState] = useState<State>({ kind: 'loading' })
  const request = useRef<AbortController | null>(null)
  // Requests tied to the selected import and version; a switch cancels them (FE-10.1).
  const scope = useRef(new RequestScope())

  const [imports, setImports] = useState<ImportSummary[] | null>(null)
  const [fields, setFields] = useState<Record<string, SchemaField[]> | null>(null)
  const [profile, setProfile] = useState<ProfileState>({ kind: 'none' })
  const [rows, setRows] = useState<DraftRule[]>([])
  const [baseline, setBaseline] = useState<DraftRule[]>([])
  const [summary, setSummary] = useState('')
  const [validation, setValidation] = useState<MappingValidation | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [refusal, setRefusal] = useState<Refusal | null>(null)
  const [comment, setComment] = useState('')

  const load = useCallback(() => {
    request.current = startLoad(
      (signal) => call(() => api.GET('/api/v1/mappings/{mapping_id}', { params: { path: { mapping_id: mappingId } }, signal })),
      { onReady: (m) => setState({ kind: 'ready', m }), onFailure: setState, failureTitle: 'Could not load the mapping' },
      request.current,
    )
  }, [mappingId])

  useEffect(() => {
    setState({ kind: 'loading' })
    load()
    return () => request.current?.abort()
  }, [load])

  const m = state.kind === 'ready' ? state.m : null
  const latest = m ? m.versions[m.versions.length - 1] : undefined
  const requested = Number(params.get('version'))
  const version: MappingVersion | undefined = m?.versions.find((v) => v.version === requested) ?? latest
  const importId = params.get('import')
  const selectedImport = imports?.find((i) => i.id === importId) ?? null

  // The source's imports under this mapping's contract (the only ones a dry run accepts).
  const sourceId = m?.source_id
  const contractId = m?.source_contract_id
  const schemaId = m?.schema_id
  useEffect(() => {
    if (!sourceId) return
    const controller = new AbortController()
    call(() => api.GET('/api/v1/imports', { params: { query: { source_id: sourceId, limit: 100 } }, signal: controller.signal }))
      .then((page) => !controller.signal.aborted && setImports(page.items.filter((i) => i.source_contract_id === contractId)))
      .catch((err: unknown) => {
        if (controller.signal.aborted) return
        setImports([])
        toastApiError(err, 'Could not load the imports')
      })
    return () => controller.abort()
  }, [sourceId, contractId])

  // The target schema's versions, for the target fields of the pinned one. Best effort:
  // without schema:read the target is typed instead (the API still checks it).
  const canReadSchema = has('schema:read')
  useEffect(() => {
    if (!schemaId || !canReadSchema) return
    const controller = new AbortController()
    call(() => api.GET('/api/v1/schemas/{schema_id}/versions', { params: { path: { schema_id: schemaId } }, signal: controller.signal }))
      .then((list) => !controller.signal.aborted && setFields(Object.fromEntries(list.items.map((v) => [v.id, v.fields]))))
      .catch(() => undefined)
    return () => controller.abort()
  }, [schemaId, canReadSchema])

  // Switching the import or the version cancels everything in flight for the previous
  // pair and clears its validation result, which no longer applies.
  const versionId = version?.id
  useEffect(() => {
    scope.current.switch()
    setValidation(null)
    setRefusal(null)
    if (!importId) {
      setProfile({ kind: 'none' })
      return
    }
    setProfile({ kind: 'loading' })
    scope.current.run(
      (signal) => call(() => api.GET('/api/v1/imports/{import_id}/profile', { params: { path: { import_id: importId } }, signal })),
      (p) => setProfile({ kind: 'ready', profile: p }),
      () => setProfile({ kind: 'none' }),
    )
  }, [importId, versionId])
  useEffect(() => () => scope.current.switch(), [])

  // The draft starts from the selected version's rules plus the import's uncovered columns.
  const columnsKey = profile.kind === 'ready' ? profile.profile.columns.map((c) => c.name).join('\u0000') : profile.kind
  const versionRules = version?.rules
  useEffect(() => {
    if (!versionRules || profile.kind === 'loading') return
    const columns = columnsKey && profile.kind === 'ready' ? columnsKey.split('\u0000') : []
    const fresh = draftFromVersion(versionRules, columns)
    setRows(fresh)
    setBaseline(fresh)
    // Only a new version or another import's columns reset the draft (not a reload).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [versionId, columnsKey])

  const dirty = isDirty(rows, baseline)
  const built = useMemo(() => buildRules(rows), [rows])

  // Leaving the page with unsaved edits asks first.
  useEffect(() => {
    if (!dirty) return
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault()
      e.returnValue = ''
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  /** Moves to another version or import; unsaved edits prompt before they are discarded. */
  const select = (next: { version?: number; import?: string | null }, force = false) => {
    if (!force && dirty && !window.confirm(DISCARD)) return false
    const q = new URLSearchParams(params.toString())
    if (next.version !== undefined) q.set('version', String(next.version))
    if (next.import !== undefined) {
      if (next.import) q.set('import', next.import)
      else q.delete('import')
    }
    router.replace(`/ingestion/mappings/${mappingId}?${q.toString()}`, { scroll: false })
    return true
  }

  if (state.kind === 'forbidden') return <Forbidden permission="mapping:read" correlationId={state.cid} />
  if (state.kind === 'failed')
    return (
      <LoadFailed
        icon={GitMerge}
        title="The mapping could not be loaded"
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
        icon={GitMerge}
        title="Mapping not found"
        description={`It does not exist or is not visible to you.${state.cid ? ` Correlation ID: ${state.cid}` : ''}`}
      />
    )
  if (!m || !version) return <LoadingBlock rows={6} />

  const active = activeVersion(m)
  const mine = version.author_user_id === me.user_id
  const pinnedFields = fields?.[version.schema_version_id] ?? null
  const profiledTypes = profile.kind === 'ready' ? Object.fromEntries(profile.profile.columns.map((c) => [c.name, c.suggested_type])) : null
  const suggestions = profile.kind === 'ready' ? Object.fromEntries(profile.profile.columns.map((c) => [c.name, c.suggested_target_field])) : null
  const targeted = new Set(rows.filter((r) => r.policy === 'MAP').map((r) => r.target_field))
  const unmappedRequired = (pinnedFields ?? []).filter((f) => f.required && !targeted.has(f.name)).map((f) => f.name)
  const schemaMismatch = selectedImport && selectedImport.schema_version_id !== version.schema_version_id
  const submitAllowed = canSubmitVersion(version, me.user_id)
  const decisionAllowed = canDecideVersion(version, me.user_id, has('mapping:approve'))
  const path = { mapping_id: m.id, version: version.version }

  /** Runs one write; the server's answer (including 403/409/422) is authoritative. */
  const act = async <T,>(label: string, fallback: string, run: () => Promise<T>, after: (value: T) => void) => {
    setBusy(label)
    setRefusal(null)
    try {
      after(await run())
    } catch (err) {
      toastApiError(err, mappingErrorTitle(err, fallback))
      setRefusal(refusalOf(err, fallback))
      load()
    } finally {
      setBusy(null)
    }
  }

  const saveDraft = () =>
    act(
      'save',
      'Could not save the draft',
      () => call(() => api.POST('/api/v1/mappings/{mapping_id}/versions', { params: { path: { mapping_id: m.id } }, body: { rules: built.rules, change_summary: summary.trim() || null } })),
      (saved) => {
        setSummary('')
        load()
        select({ version: saved.version }, true)
      },
    )

  const validate = () => {
    if (!importId) return
    setBusy('validate')
    setRefusal(null)
    setValidation(null)
    // Runs in the current scope: switching the import or version drops a late answer.
    scope.current.run(
      (signal) =>
        call(() =>
          api.POST('/api/v1/imports/{import_id}/mappings/validate', {
            params: { path: { import_id: importId } },
            body: { mapping_version_id: version.id },
            signal,
          }),
        ),
      (v) => {
        setValidation(v)
        setBusy(null)
        load()
      },
      (err) => {
        toastApiError(err, mappingErrorTitle(err, 'The dry run failed'))
        setRefusal(refusalOf(err, 'The dry run failed'))
        setBusy(null)
      },
    )
  }

  return (
    <>
      <PageHeader
        title={m.name}
        description={m.description ?? undefined}
        breadcrumbs={[...CRUMBS, { label: m.code }]}
        actions={
          <div className="flex items-center gap-2">
            {active ? (
              <Badge variant="success" data-testid="mapping-active-version" data-version={active.version}>
                Active v{active.version}
              </Badge>
            ) : (
              <Badge variant="secondary" data-testid="mapping-active-version" data-version="">
                No active version
              </Badge>
            )}
          </div>
        }
      />
      <div className="space-y-4 px-6 pb-6">
        <PageSection icon={GitMerge} title="Mapping">
          <dl className="grid grid-cols-[200px_1fr] gap-y-1 text-sm" data-testid="mapping-summary">
            <Field label="Code">
              <span className="font-mono">{m.code}</span>
            </Field>
            <Field label="Source">
              <Link href={`/ingestion/sources/${m.source_id}`} className="font-mono text-primary hover:underline">
                {m.source_code}
              </Link>{' '}
              <span className="text-muted-foreground">contract v{m.contract_version ?? '—'}</span>
            </Field>
            <Field label="Target schema">
              <span className="font-mono">{m.schema_code}</span>
            </Field>
            <Field label="Created">
              <span className="font-mono text-xs">
                {m.created_by} · {new Date(m.created_at).toLocaleString()}
              </span>
            </Field>
          </dl>
        </PageSection>

        <PageSection icon={History} title="Versions" description="Oldest first is history; a change is always a new version, never an edit.">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Version</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Schema version</TableHead>
                <TableHead>Rules</TableHead>
                <TableHead>Author</TableHead>
                <TableHead>Last dry run</TableHead>
                <TableHead>Decision</TableHead>
                <TableHead>Content SHA-256</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {[...m.versions].reverse().map((v) => (
                <TableRow
                  key={v.id}
                  data-testid="mapping-version-row"
                  data-version={v.version}
                  data-status={v.status}
                  className={cn(v.id === version.id && 'bg-surface-2')}
                >
                  <TableCell>
                    <button
                      type="button"
                      className="font-mono text-sm text-primary hover:underline"
                      onClick={() => v.id !== version.id && select({ version: v.version })}
                      data-testid="mapping-version-select"
                    >
                      v{v.version}
                    </button>
                    {v.id === version.id && <span className="ml-1 text-xs text-muted-foreground">(open)</span>}
                  </TableCell>
                  <TableCell>
                    <MappingStatusPill status={v.status} />
                  </TableCell>
                  <TableCell className="font-mono text-xs">{v.schema_version != null ? `v${v.schema_version}` : '—'}</TableCell>
                  <TableCell className="font-mono text-xs">{v.rules.length}</TableCell>
                  <TableCell className="font-mono text-xs">
                    {v.author} {v.author_user_id === me.user_id && <span className="font-sans text-muted-foreground">(you)</span>}
                    <div className="font-sans text-muted-foreground">{new Date(v.created_at).toLocaleString()}</div>
                    {v.change_summary && <div className="font-sans text-foreground">“{v.change_summary}”</div>}
                  </TableCell>
                  <TableCell className="text-xs">
                    {v.last_validation ? (
                      <Badge variant={OUTCOME_VARIANT[v.last_validation.outcome]}>{v.last_validation.outcome}</Badge>
                    ) : (
                      <span className="text-muted-foreground">none</span>
                    )}
                  </TableCell>
                  <TableCell className="text-xs">
                    {v.decided_by ? (
                      <>
                        <span className="font-mono">{v.decided_by}</span>
                        <div className="text-muted-foreground">{v.decided_at && new Date(v.decided_at).toLocaleString()}</div>
                        {v.decision_comment && <div>“{v.decision_comment}”</div>}
                      </>
                    ) : (
                      <span className="text-muted-foreground">—</span>
                    )}
                  </TableCell>
                  <TableCell className="font-mono text-xs" title={v.content_sha256}>
                    {v.content_sha256.slice(0, 12)}…
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </PageSection>

        <PageSection
          icon={TableProperties}
          title="Rules"
          description="Edits change a local draft only. Saving creates a new Draft version; existing versions are never edited."
          headerRight={
            <div className="flex items-center gap-2" data-testid="mapping-version-badge" data-version={version.version} data-status={version.status}>
              <Badge variant="outline" className="font-mono">
                v{version.version}
              </Badge>
              <MappingStatusPill status={version.status} />
              {dirty && (
                <Badge variant="warning" data-testid="mapping-dirty">
                  Unsaved edits
                </Badge>
              )}
            </div>
          }
        >
          <div className="mb-3 flex flex-wrap items-end gap-3">
            <div className="grid gap-1">
              <label htmlFor="mapping-import" className="text-xs text-muted-foreground">
                Import to validate against
              </label>
              <select
                id="mapping-import"
                className={selectClass}
                data-testid="mapping-import"
                value={importId ?? ''}
                onChange={(e) => select({ import: e.target.value || null })}
              >
                <option value="">{imports === null ? 'Loading imports…' : imports.length === 0 ? 'No import of this contract' : 'Choose an import…'}</option>
                {importId && !selectedImport && <option value={importId}>{importId.slice(0, 8)}</option>}
                {(imports ?? []).map((i) => (
                  <option key={i.id} value={i.id}>
                    {i.id.slice(0, 8)} · {i.status} · {i.artifact.original_filename ?? 'unnamed'} · {new Date(i.created_at).toLocaleString()}
                  </option>
                ))}
              </select>
            </div>
            {selectedImport && (
              <span className="flex items-center gap-2 text-xs text-muted-foreground">
                <Link href={`/ingestion/imports/${selectedImport.id}`} className="font-mono text-primary hover:underline">
                  {selectedImport.id.slice(0, 8)}
                </Link>
                <ImportStatusPill status={selectedImport.status} />
                {selectedImport.row_count} rows · schema {selectedImport.schema_code} v{selectedImport.schema_version}
              </span>
            )}
            {mayPropose && suggestions && (
              <Button variant="outline" size="sm" onClick={() => setRows(applySuggestions(rows, suggestions))} data-testid="mapping-apply-suggestions">
                <Sparkles className="mr-1 h-3.5 w-3.5" />
                Map unmapped columns to suggested targets
              </Button>
            )}
          </div>
          <dl className="mb-3 grid grid-cols-[200px_1fr] gap-y-1 text-xs">
            <Field label="Pinned schema version">
              <span className="font-mono">
                {m.schema_code} v{version.schema_version ?? '—'}
              </span>{' '}
              <span className="font-mono text-muted-foreground">{version.schema_version_id}</span>
            </Field>
            <Field label="Content SHA-256">
              <span className="break-all font-mono">{version.content_sha256}</span>
            </Field>
          </dl>
          {schemaMismatch && (
            <p className="mb-2 text-sm text-warning" data-testid="mapping-schema-mismatch">
              This import pins another schema version than v{version.version}; the dry run will refuse it.
            </p>
          )}
          {!pinnedFields && <p className="mb-2 text-xs text-muted-foreground">The target fields could not be read (schema:read); type them instead.</p>}
          {unmappedRequired.length > 0 && (
            <p className="mb-2 text-sm text-muted-foreground" data-testid="mapping-unmapped-required">
              Required fields not mapped in this draft: <span className="font-mono">{unmappedRequired.join(', ')}</span>. The dry run reports each as a BLOCKER.
            </p>
          )}
          {profile.kind === 'loading' ? (
            <LoadingBlock rows={4} />
          ) : (
            <MappingGrid rows={rows} onChange={setRows} fields={pinnedFields} profiledTypes={profiledTypes} editable={mayPropose} />
          )}
          {mayPropose && (
            <div className="mt-3 space-y-2">
              {dirty && built.problems.length > 0 && (
                <ul className="list-disc pl-5 text-sm text-destructive" data-testid="mapping-draft-problems">
                  {built.problems.map((p) => (
                    <li key={p}>{p}</li>
                  ))}
                </ul>
              )}
              <div className="flex flex-wrap items-center gap-2">
                <Input className="max-w-md" placeholder="Change summary (optional)" value={summary} onChange={(e) => setSummary(e.target.value)} maxLength={1000} aria-label="Change summary" />
                <Button onClick={saveDraft} disabled={!dirty || built.problems.length > 0 || busy !== null} data-testid="mapping-save-draft">
                  <Save className="mr-1 h-3.5 w-3.5" />
                  Save as new draft version
                </Button>
                <Button variant="outline" disabled={!dirty || busy !== null} onClick={() => window.confirm(DISCARD) && setRows(baseline)} data-testid="mapping-discard">
                  Discard edits
                </Button>
              </div>
            </div>
          )}
        </PageSection>

        <PageSection
          icon={FlaskConical}
          title="Validate against this import"
          description="A dry run of the open version over every row of the import: counts and issues only, no values. Nothing is written to the import."
          headerRight={
            mayPropose ? (
              <Button
                size="sm"
                onClick={validate}
                disabled={!importId || dirty || busy !== null}
                title={dirty ? 'Save the draft first: a dry run checks a saved version.' : !importId ? 'Choose an import first.' : undefined}
                data-testid="mapping-validate"
              >
                {busy === 'validate' ? 'Validating…' : `Validate v${version.version} against this import`}
              </Button>
            ) : undefined
          }
        >
          {!mayPropose && <p className="text-sm text-muted-foreground">A dry run needs the mapping:propose permission.</p>}
          {mayPropose && dirty && <p className="mb-2 text-sm text-muted-foreground">Save the draft as a new version first: a dry run checks a saved version.</p>}
          {validation ? (
            <ValidationResult v={validation} />
          ) : (
            mayPropose && (
              <p className="text-sm text-muted-foreground" data-testid="mapping-validation-none">
                {version.last_validation
                  ? `The latest dry run of v${version.version} was ${version.last_validation.outcome} (import ${version.last_validation.import_id.slice(0, 8)}). Run it again to see the details.`
                  : `v${version.version} has no dry run yet.`}
              </p>
            )
          )}
        </PageSection>

        {refusal && <RefusalNotice refusal={refusal} />}

        {version.status === 'DRAFT' && mine && (
          <PageSection icon={CheckCircle2} title="Submit for approval">
            <div className="flex flex-wrap items-center gap-2">
              <Input className="max-w-md" placeholder="Comment (optional)" value={comment} onChange={(e) => setComment(e.target.value)} maxLength={2000} aria-label="Submit comment" />
              <Button
                data-testid="mapping-submit"
                disabled={!submitAllowed || busy !== null || dirty}
                title={submitAllowed ? undefined : 'Run a passing dry run of this version first'}
                onClick={() =>
                  act(
                    'submit',
                    'Could not submit',
                    () => call(() => api.POST('/api/v1/mappings/{mapping_id}/versions/{version}/submit', { params: { path }, body: { comment: comment.trim() || null } })),
                    () => {
                      setComment('')
                      load()
                    },
                  )
                }
              >
                Submit v{version.version} for approval
              </Button>
            </div>
            {!submitAllowed && (
              <p className="mt-2 text-xs text-muted-foreground" data-testid="mapping-submit-locked">
                Submit unlocks once the latest dry run of this version is ok (OK or WARNINGS).
              </p>
            )}
          </PageSection>
        )}

        {version.status === 'IN_REVIEW' && (
          <PageSection icon={Gavel} title="Approver decision" accent="warning">
            {decisionAllowed ? (
              <div className="space-y-2" data-testid="mapping-decision-panel">
                <p className="text-sm text-muted-foreground">
                  Approving activates v{version.version} for new imports of {m.source_code}
                  {active ? ` and deprecates v${active.version}` : ''}. Its latest dry run was {version.last_validation?.outcome ?? 'not recorded'}.
                </p>
                <Textarea placeholder="Comment (required to reject)" value={comment} onChange={(e) => setComment(e.target.value)} maxLength={2000} aria-label="Decision comment" />
                <div className="flex gap-2">
                  <Button
                    data-testid="mapping-approve"
                    disabled={busy !== null}
                    onClick={() =>
                      act(
                        'approve',
                        'Approval failed',
                        () => call(() => api.POST('/api/v1/mappings/{mapping_id}/versions/{version}/approval', { params: { path }, body: { decision: 'APPROVE', comment: comment.trim() || null } })),
                        () => {
                          setComment('')
                          load()
                        },
                      )
                    }
                  >
                    Approve and activate v{version.version}
                  </Button>
                  <Button
                    variant="destructive"
                    data-testid="mapping-reject"
                    disabled={busy !== null || !comment.trim()}
                    onClick={() =>
                      act(
                        'reject',
                        'Rejection failed',
                        () => call(() => api.POST('/api/v1/mappings/{mapping_id}/versions/{version}/approval', { params: { path }, body: { decision: 'REJECT', comment: comment.trim() } })),
                        () => {
                          setComment('')
                          load()
                        },
                      )
                    }
                  >
                    Reject
                  </Button>
                </div>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground" data-testid="mapping-awaiting-approver">
                {mine ? 'Waiting for an approver. You cannot approve your own mapping.' : 'Waiting for a user with mapping approval rights.'}
              </p>
            )}
          </PageSection>
        )}
      </div>
    </>
  )
}

/** The mapping workbench (FE-10.1) with its versions and approval (FE-10.2). */
export default function MappingPage() {
  const { mappingId } = useParams<{ mappingId: string }>()
  if (!MAPPING_UI_ENABLED)
    return (
      <>
        <PageHeader title="Mapping" breadcrumbs={CRUMBS} />
        <div className="px-6 pb-6">
          <MappingsPlanned />
        </div>
      </>
    )
  return (
    <RequirePermission
      permission="mapping:read"
      fallback={
        <>
          <PageHeader title="Mapping" breadcrumbs={CRUMBS} />
          <div className="px-6 pb-6">
            <Forbidden permission="mapping:read" />
          </div>
        </>
      }
    >
      <Suspense fallback={<LoadingBlock rows={6} />}>
        <Workbench mappingId={mappingId} />
      </Suspense>
    </RequirePermission>
  )
}
