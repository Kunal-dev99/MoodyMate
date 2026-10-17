'use client'

import { Fragment, useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { ChevronDown, ChevronRight, ClipboardCheck, FileSearch, ListChecks, PlayCircle, Sigma } from 'lucide-react'
import { api, call } from '@/api/client'
import { Forbidden } from '@/components/auth/Forbidden'
import { RequirePermission } from '@/components/auth/RequirePermission'
import { EmptyState } from '@/components/common/EmptyState'
import { LoadFailed } from '@/components/common/LoadFailed'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { PageSection } from '@/components/common/PageSection'
import { toastApiError } from '@/components/common/api-error-toast'
import { startLoad, type LoadFailure } from '@/components/common/load'
import { ImportStatusPill } from '@/components/ingestion/ImportStatusPill'
import { MapImportLink } from '@/components/ingestion/MapImportLink'
import { SeverityPill } from '@/components/ingestion/QuarantineStatusPill'
import { RefusalNotice } from '@/components/ingestion/RefusalNotice'
import type { ImportDetail } from '@/components/ingestion/imports'
import type { Refusal } from '@/components/ingestion/mappings'
import {
  ISSUE_CODES,
  SEVERITIES,
  canValidate,
  outcomeTotal,
  validationRefusal,
  type ImportManifest,
  type IssuePage,
  type ValidationCode,
  type ValidationSeverity,
} from '@/components/ingestion/validation'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { MAPPING_UI_ENABLED } from '@/config/features'
import { cn } from '@/lib/utils'

/*
 * FE-11.1: the import's validation dashboard. "Validate" runs once (D-21); the counts and
 * the manifest are the server's (never computed here, only checked to add up), and the
 * issues carry no values (BE-11.3).
 */

const PAGE = 50
const selectClass = 'h-9 rounded-md border border-input bg-background px-2 text-sm'

type Filters = { severity: ValidationSeverity | ''; code: ValidationCode | ''; field: string }
type IssuesState = { kind: 'loading' } | { kind: 'ready'; page: IssuePage } | LoadFailure

function Field({ label, children, testId }: { label: string; children: ReactNode; testId?: string }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd data-testid={testId}>{children}</dd>
    </>
  )
}

const Mono = ({ children }: { children: ReactNode }) => <span className="break-all font-mono text-xs">{children}</span>

function Count({ label, value, testId, tone }: { label: string; value: number; testId: string; tone?: string }) {
  return (
    <div className="rounded-md border border-border bg-surface-2 px-4 py-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={cn('font-mono text-2xl font-semibold', tone)} data-testid={testId}>
        {value}
      </div>
    </div>
  )
}

function Counts({ manifest }: { manifest: ImportManifest }) {
  const q = manifest.quality
  const total = outcomeTotal(q)
  return (
    <PageSection icon={Sigma} title="Row outcomes" description="As reported by the server: every source row has exactly one outcome (D-20).">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4" data-testid="validation-counts">
        <Count label="Accepted" value={q.accepted_records} testId="count-accepted" />
        <Count label="Quarantined" value={q.quarantined_records} testId="count-quarantined" tone={q.quarantined_records ? 'text-[hsl(var(--warning))]' : undefined} />
        <Count label="Rejected" value={q.rejected_records} testId="count-rejected" tone={q.rejected_records ? 'text-destructive' : undefined} />
        <Count label="Warnings (issues)" value={q.warnings} testId="count-warnings" />
      </div>
      <p
        className={cn('mt-3 font-mono text-sm', total.addsUp ? 'text-muted-foreground' : 'text-destructive')}
        data-testid="count-sum"
        data-adds-up={total.addsUp}
      >
        {q.accepted_records} + {q.quarantined_records} + {q.rejected_records} = {total.sum}{' '}
        {total.addsUp ? '=' : '≠'} {total.rowCount} rows
        {!total.addsUp && ' (the counts do not add up to the row count: report this with the manifest ID)'}
      </p>
      <p className="mt-1 text-xs text-muted-foreground">Warnings count WARNING-severity issues (file and row level), not rows.</p>
    </PageSection>
  )
}

function ManifestPanel({ manifest }: { manifest: ImportManifest }) {
  const m = manifest
  return (
    <PageSection icon={ClipboardCheck} title="Manifest" description="Written once at validation and never changed: what a calculation run pins (D-22).">
      <dl className="grid grid-cols-[220px_1fr] gap-y-1 text-sm" data-testid="validation-manifest">
        <Field label="Manifest ID" testId="manifest-id">
          <Mono>{m.manifest_id}</Mono>
        </Field>
        <Field label="Manifest SHA-256">
          <Mono>{m.manifest_sha256}</Mono>
        </Field>
        <Field label="Import ID">
          <Mono>{m.import_id}</Mono>
        </Field>
        <Field label="Outcome">
          <ImportStatusPill status={m.status} />
        </Field>
        <Field label="Validated">
          <Mono>
            {m.validated_by} · {new Date(m.validated_at).toLocaleString()}
          </Mono>
        </Field>
        <Field label="Artifact">
          <Mono>{m.artifact.sha256}</Mono>
          <span className="text-muted-foreground"> · {m.artifact.classification}</span>
        </Field>
        <Field label="Source contract" testId="manifest-contract">
          <span className="font-mono">
            {m.source_code} v{m.source_contract_version}
          </span>{' '}
          <span className="font-mono text-xs text-muted-foreground">{m.source_contract_id}</span>
        </Field>
        <Field label="Schema version" testId="manifest-schema">
          <span className="font-mono">
            {m.schema_code} v{m.schema_version}
          </span>{' '}
          <span className="font-mono text-xs text-muted-foreground">{m.schema_version_id}</span>
        </Field>
        <Field label="Mapping version" testId="manifest-mapping">
          <span className="font-mono">
            {m.mapping_code} v{m.mapping_version}
          </span>{' '}
          <span className="font-mono text-xs text-muted-foreground">{m.mapping_version_id}</span>
        </Field>
        <Field label="Validation profile" testId="manifest-profile">
          <span className="font-mono">
            {m.validation_profile.code} v{m.validation_profile.version}
          </span>
          {m.validation_profile.provisional && (
            <Badge variant="warning" className="ml-2">
              Provisional
            </Badge>
          )}
          <span className="text-muted-foreground"> · validator v{m.validator_version}</span>
        </Field>
        <Field label="Canonical SHA-256">
          <Mono>{m.canonical_sha256}</Mono>
        </Field>
      </dl>
    </PageSection>
  )
}

function Issues({ importId }: { importId: string }) {
  const [filters, setFilters] = useState<Filters>({ severity: '', code: '', field: '' })
  const [fieldText, setFieldText] = useState('')
  const [offset, setOffset] = useState(0)
  const [open, setOpen] = useState<string | null>(null)
  const [state, setState] = useState<IssuesState>({ kind: 'loading' })
  const request = useRef<AbortController | null>(null)

  const load = useCallback(() => {
    const query = {
      limit: PAGE,
      offset,
      ...(filters.severity ? { severity: filters.severity } : {}),
      ...(filters.code ? { code: filters.code } : {}),
      ...(filters.field ? { field: filters.field } : {}),
    }
    request.current = startLoad(
      (signal) => call(() => api.GET('/api/v1/imports/{import_id}/issues', { params: { path: { import_id: importId }, query }, signal })),
      { onReady: (page) => setState({ kind: 'ready', page }), onFailure: setState, failureTitle: 'Could not load the issues' },
      request.current,
    )
  }, [importId, filters, offset])

  useEffect(() => {
    setState({ kind: 'loading' })
    load()
    return () => request.current?.abort()
  }, [load])

  const change = (patch: Partial<Filters>) => {
    setOffset(0)
    setFilters((f) => ({ ...f, ...patch }))
  }
  const applyField = (e: FormEvent) => {
    e.preventDefault()
    change({ field: fieldText.trim() })
  }

  let body: ReactNode
  if (state.kind === 'loading') body = <LoadingBlock rows={4} />
  else if (state.kind === 'forbidden') body = <Forbidden permission="import:read" correlationId={state.cid} />
  else if (state.kind === 'failed')
    body = (
      <LoadFailed
        icon={ListChecks}
        title="The issues could not be loaded"
        cid={state.cid}
        onRetry={() => {
          setState({ kind: 'loading' })
          load()
        }}
      />
    )
  else if (state.kind === 'not_found')
    body = <EmptyState icon={ListChecks} title="The issues could not be listed" description={`The request was refused.${state.cid ? ` Correlation ID: ${state.cid}` : ''}`} />
  else {
    const { page } = state
    const last = Math.min(page.offset + page.items.length, page.total)
    body =
      page.items.length === 0 ? (
        <EmptyState icon={ListChecks} title="No issues" description={filters.severity || filters.code || filters.field ? 'No issue matches these filters.' : 'Validation raised no issues.'} />
      ) : (
        <>
          <Table data-testid="issues-table" data-total={page.total}>
            <TableHeader>
              <TableRow>
                <TableHead className="w-8" />
                <TableHead>Row</TableHead>
                <TableHead>Severity</TableHead>
                <TableHead>Code</TableHead>
                <TableHead>Field</TableHead>
                <TableHead>Source column</TableHead>
                <TableHead>Disposition</TableHead>
                <TableHead>Message</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {page.items.map((i) => (
                <Fragment key={i.id}>
                  <TableRow data-testid="issue-row" data-code={i.code} data-severity={i.severity} data-row={i.row ?? ''} data-field={i.field ?? ''}>
                    <TableCell>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-6 w-6 p-0"
                        aria-label={open === i.id ? 'Hide detail' : 'Show detail'}
                        aria-expanded={open === i.id}
                        onClick={() => setOpen(open === i.id ? null : i.id)}
                        data-testid="issue-expand"
                      >
                        {open === i.id ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                      </Button>
                    </TableCell>
                    <TableCell className="font-mono text-sm">{i.row ?? <span className="text-muted-foreground">file</span>}</TableCell>
                    <TableCell>
                      <SeverityPill severity={i.severity} />
                    </TableCell>
                    <TableCell className="font-mono text-xs" data-testid="issue-code">
                      {i.code}
                    </TableCell>
                    <TableCell className="font-mono text-xs">{i.field ?? '—'}</TableCell>
                    <TableCell className="font-mono text-xs">{i.source_column ?? '—'}</TableCell>
                    <TableCell className="font-mono text-xs">{i.disposition}</TableCell>
                    <TableCell className="text-sm">{i.message}</TableCell>
                  </TableRow>
                  {open === i.id && (
                    <TableRow data-testid="issue-detail">
                      <TableCell />
                      <TableCell colSpan={7}>
                        <dl className="grid grid-cols-[200px_1fr] gap-y-1 text-xs">
                          <Field label="Issue ID">
                            <Mono>{i.id}</Mono>
                          </Field>
                          <Field label="Sequence">
                            <Mono>{i.sequence}</Mono>
                          </Field>
                          <Field label="Source record">
                            <Mono>{i.source_record_id ?? '— (file-level issue)'}</Mono>
                          </Field>
                          <Field label="Schema field">
                            <Mono>{i.field ?? '—'}</Mono>
                          </Field>
                          <Field label="Source column">
                            <Mono>{i.source_column ?? '—'}</Mono>
                          </Field>
                          <Field label="Mapping rule">
                            <Mono>{i.rule_position != null ? `#${i.rule_position}` : '—'}</Mono>
                          </Field>
                          <Field label="Validation profile">
                            <Mono>{i.validation_profile_id}</Mono>
                          </Field>
                        </dl>
                        <p className="mt-2 text-xs text-muted-foreground">Issues never carry the value itself; held rows are in the Quarantine tab, masked by classification.</p>
                      </TableCell>
                    </TableRow>
                  )}
                </Fragment>
              ))}
            </TableBody>
          </Table>
          <div className="mt-3 flex items-center justify-between text-sm">
            <span className="text-muted-foreground" data-testid="issues-range">
              {page.offset + 1}–{last} of {page.total}
            </span>
            <div className="flex gap-2">
              <Button size="sm" variant="outline" disabled={page.offset === 0} onClick={() => setOffset(Math.max(0, page.offset - PAGE))} data-testid="issues-prev">
                Previous
              </Button>
              <Button size="sm" variant="outline" disabled={last >= page.total} onClick={() => setOffset(page.offset + PAGE)} data-testid="issues-next">
                Next
              </Button>
            </div>
          </div>
        </>
      )
  }

  const counts = state.kind === 'ready' ? state.page.severity_counts : null
  return (
    <PageSection icon={ListChecks} title="Issues" description="File-level issues first, then by row. Filters are applied by the server.">
      {counts && (
        <div className="mb-3 flex flex-wrap gap-2 text-xs" data-testid="issue-severity-counts">
          {SEVERITIES.map((s) => (
            <span key={s} className="flex items-center gap-1" data-severity={s} data-count={counts[s] ?? 0}>
              <SeverityPill severity={s} /> <span className="font-mono">{counts[s] ?? 0}</span>
            </span>
          ))}
        </div>
      )}
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <select aria-label="Severity" className={selectClass} value={filters.severity} onChange={(e) => change({ severity: e.target.value as Filters['severity'] })} data-testid="issues-severity">
          <option value="">All severities</option>
          {SEVERITIES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <select aria-label="Code" className={selectClass} value={filters.code} onChange={(e) => change({ code: e.target.value as Filters['code'] })} data-testid="issues-code">
          <option value="">All codes</option>
          {ISSUE_CODES.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        <form onSubmit={applyField} className="flex items-center gap-2">
          <Input aria-label="Field" placeholder="Field or column (exact)" className="h-9 w-56 font-mono" value={fieldText} maxLength={200} onChange={(e) => setFieldText(e.target.value)} data-testid="issues-field" />
          <Button type="submit" size="sm" variant="outline" data-testid="issues-field-apply">
            Apply
          </Button>
        </form>
      </div>
      {body}
    </PageSection>
  )
}

/** The Validation tab: the Validate action until the server has validated, then counts, manifest and issues. */
export function ImportValidationTab({ job, onChanged }: { job: ImportDetail; onChanged: (job: ImportDetail | null) => void }) {
  const [busy, setBusy] = useState(false)
  const [refusal, setRefusal] = useState<Refusal | null>(null)
  const manifest = job.manifest ?? null

  const validate = async () => {
    setBusy(true)
    setRefusal(null)
    try {
      const next = await call(() => api.POST('/api/v1/imports/{import_id}/validate', { params: { path: { import_id: job.id } } }))
      onChanged(next)
    } catch (err) {
      const r = validationRefusal(err, 'Could not validate the import')
      toastApiError(err, r.title)
      setRefusal(r)
      // The server's record may have moved (e.g. to MAPPING_REQUIRED): reload it, never guess.
      onChanged(null)
    } finally {
      setBusy(false)
    }
  }

  const header = (
    <PageSection
      icon={FileSearch}
      title="Validation"
      description="Applies the import's pinned schema version and the contract's ACTIVE mapping version, once (D-21). A re-run is a new import."
      headerRight={
        canValidate(job.status, manifest) ? (
          <RequirePermission permission="import:write" fallback={null}>
            <Button size="sm" onClick={validate} disabled={busy} data-testid="import-validate">
              <PlayCircle className="mr-2 h-4 w-4" />
              {busy ? 'Validating…' : 'Validate'}
            </Button>
          </RequirePermission>
        ) : undefined
      }
    >
      <dl className="grid grid-cols-[200px_1fr] gap-y-1 text-sm">
        <Field label="Import ID">
          <Mono>{job.id}</Mono>
        </Field>
        <Field label="Status">
          <ImportStatusPill status={job.status} />
        </Field>
        <Field label="Rows read" testId="validation-row-count">
          <span className="font-mono">{job.row_count}</span>
        </Field>
        <Field label="Mapping version">
          <Mono>{job.mapping_version_id ?? 'none yet (pinned when validated)'}</Mono>
        </Field>
      </dl>
      {refusal && (
        <div className="mt-3 space-y-2">
          <RefusalNotice refusal={refusal} />
          {MAPPING_UI_ENABLED && refusal.code === 'mapping_required' && (
            <MapImportLink importId={job.id} sourceId={job.source_id} contractId={job.source_contract_id} />
          )}
        </div>
      )}
      {!manifest && !refusal && (
        <p className="mt-3 text-sm text-muted-foreground" data-testid="validation-not-run">
          {canValidate(job.status, manifest)
            ? 'Not validated yet. Validation needs an ACTIVE mapping version for this source contract.'
            : 'This import has no validation result.'}
        </p>
      )}
    </PageSection>
  )

  return (
    <div className="space-y-4" data-testid="validation-tab">
      {header}
      {manifest && (
        <>
          <Counts manifest={manifest} />
          <ManifestPanel manifest={manifest} />
          <Issues importId={job.id} />
        </>
      )}
    </div>
  )
}
