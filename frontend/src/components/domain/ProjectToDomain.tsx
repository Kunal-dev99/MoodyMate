'use client'

import Link from 'next/link'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { Landmark, Sigma } from 'lucide-react'
import { api, call, callWithResponse } from '@/api/client'
import { Forbidden } from '@/components/auth/Forbidden'
import { RequirePermission } from '@/components/auth/RequirePermission'
import { LoadFailed } from '@/components/common/LoadFailed'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { PageSection } from '@/components/common/PageSection'
import { createLoader, type Loaded } from '@/components/common/load'
import { toastApiError } from '@/components/common/api-error-toast'
import { ImportStatusPill } from '@/components/ingestion/ImportStatusPill'
import { RefusalNotice } from '@/components/ingestion/RefusalNotice'
import type { ImportDetail } from '@/components/ingestion/imports'
import type { Refusal } from '@/components/ingestion/mappings'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import {
  browseLink,
  canProject,
  fieldCounts,
  isCreditSchemaCode,
  isNotProjected,
  originOf,
  projectionConserves,
  projectionRefusal,
  rowLabel,
  rowsLabel,
  type Projection,
  type ProjectionOrigin,
} from './domain'

/*
 * FE-14.2: "Project to domain" on the import page (D-29). Offered only for a validated import
 * of a supported schema, and only with domain:project; both are cosmetic, the server decides
 * (D-04). The summary is always the server's answer: read on load (GET, domain:read; 404
 * `projection_not_found` means not projected yet), or returned by the click (201 projected
 * now, 200 the existing projection, nothing written).
 * FE-19.2: the same action for the two Feature 04 codes, `counterparty` and `credit_exposure`
 * (D-37), taken from the OpenAPI enum; off with NEXT_PUBLIC_CREDIT_DOMAIN_UI (see domain.ts).
 */

function Field({ label, children, testId }: { label: string; children: ReactNode; testId?: string }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd data-testid={testId}>{children}</dd>
    </>
  )
}

const Mono = ({ children }: { children: ReactNode }) => <span className="break-all font-mono text-xs">{children}</span>

function Count({ label, value, testId }: { label: string; value: number; testId: string }) {
  return (
    <div className="rounded-md border border-border bg-surface-2 px-4 py-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="font-mono text-2xl font-semibold" data-testid={testId}>
        {value}
      </div>
    </div>
  )
}

function FieldCountList({ counts, testId, none }: { counts: Record<string, number>; testId: string; none: string }) {
  const rows = fieldCounts(counts)
  if (rows.length === 0)
    return (
      <span className="text-muted-foreground" data-testid={`${testId}-none`}>
        {none}
      </span>
    )
  return (
    <ul className="space-y-0.5">
      {rows.map(([field, count]) => (
        <li key={field} data-testid={testId} data-field={field} data-count={count}>
          <span className="font-mono">{field}</span>: <span className="font-mono">{count}</span>
        </li>
      ))}
    </ul>
  )
}

const ORIGIN_TEXT: Record<ProjectionOrigin, (p: Projection) => string> = {
  existing: (p) => `Projected ${new Date(p.projected_at).toLocaleString()} by ${p.projected_by}, as the server reports it.`,
  created: (p) => `Projected now: one ${rowLabel(p.schema_code)} per accepted canonical record.`,
  replayed: () => 'Already projected: the server returned the existing projection. Nothing new was written.',
}

export function ProjectionSummaryView({ projection, origin }: { projection: Projection; origin: ProjectionOrigin }) {
  const p = projection
  const s = p.summary
  const conserves = projectionConserves(p)
  const browse = browseLink(p)
  return (
    <div className="space-y-4" data-testid="projection-summary" data-projection-id={p.projection_id} data-origin={origin}>
      <p className="rounded-md border border-border bg-surface-2 px-3 py-2 text-sm" data-testid="projection-outcome">
        {ORIGIN_TEXT[origin](p)}
      </p>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Count label="Accepted (manifest)" value={p.accepted_count} testId="projection-accepted" />
        <Count label={`Projected ${rowsLabel(p.schema_code)}`} value={p.projected_count} testId="projection-projected" />
        <Count label="New identities" value={s.new_identities} testId="projection-new-identities" />
        <Count label="Attribute values" value={s.attribute_values} testId="projection-attribute-values" />
      </div>
      <p
        className={cn('font-mono text-sm', conserves ? 'text-muted-foreground' : 'text-destructive')}
        data-testid="projection-conserves"
        data-conserves={conserves}
      >
        {p.projected_count} {conserves ? '=' : '≠'} {p.accepted_count} accepted
        {!conserves && ' (the projection does not conserve rows: report this with the projection ID)'}
      </p>
      <dl className="grid grid-cols-[220px_1fr] gap-y-1 text-sm">
        <Field label="Projection ID" testId="projection-id">
          <Mono>{p.projection_id}</Mono>
        </Field>
        <Field label="Import status">
          <ImportStatusPill status={p.import_status} />
          {p.import_status === 'QUARANTINED' && <span className="text-muted-foreground"> · accepted rows only</span>}
        </Field>
        <Field label="Schema version" testId="projection-schema">
          <span className="font-mono">
            {p.schema_code} v{p.schema_version}
          </span>{' '}
          <span className="font-mono text-xs text-muted-foreground">{p.schema_version_id}</span>
          {isCreditSchemaCode(p.schema_code) && (
            <div className="text-xs text-muted-foreground" data-testid="projection-credit-schema">
              Seeded illustrative credit schema (D-37), provisional.
            </div>
          )}
        </Field>
        <Field label="Projector version">
          <span className="font-mono">v{p.projector_version}</span>
          <span className="text-muted-foreground"> · frozen field projection spec (D-29 §4)</span>
        </Field>
        <Field label="Records read" testId="projection-records">
          <span className="font-mono">{s.records}</span>
        </Field>
        <Field label="Rows projected" testId="projection-rows">
          <span className="font-mono">{s.rows_projected}</span>
        </Field>
        <Field label="TYPE_NOT_PROJECTED" testId="projection-type-not-projected">
          <FieldCountList counts={s.type_not_projected} testId="type-not-projected" none="none" />
          <div className="text-xs text-muted-foreground">Values whose pinned field type differs from the typed column; they stay in the canonical record.</div>
        </Field>
        <Field label="UNKNOWN_VALUE" testId="projection-unknown-values">
          <FieldCountList counts={s.unknown_values} testId="unknown-value" none="none" />
          <div className="text-xs text-muted-foreground">Values outside a seeded provisional vocabulary; still projected.</div>
        </Field>
        <Field label="Canonical-only fields" testId="projection-canonical-only">
          {s.canonical_only_fields.length === 0 ? (
            <span className="text-muted-foreground">none</span>
          ) : (
            <span className="font-mono text-xs">{s.canonical_only_fields.join(', ')}</span>
          )}
          <div className="text-xs text-muted-foreground">No typed column: reached through each row&apos;s canonical record (lineage).</div>
        </Field>
        <Field label="Projected">
          <Mono>
            {p.projected_by} · {new Date(p.projected_at).toLocaleString()}
          </Mono>
        </Field>
      </dl>
      {browse && (
        <Link href={browse.href} className="text-sm text-primary hover:underline" data-testid="projection-browse">
          {browse.label}
        </Link>
      )}
    </div>
  )
}

/** GET /imports/{id}/projection; null while the import is not projected (404 `projection_not_found`). */
export const fetchProjection = (importId: string) => (signal: AbortSignal) =>
  call(() => api.GET('/api/v1/imports/{import_id}/projection', { params: { path: { import_id: importId } }, signal })).catch(
    (err: unknown) => {
      if (isNotProjected(err)) return null
      throw err
    },
  )

type Shown = { projection: Projection; origin: ProjectionOrigin }

function ProjectionPanel({ job }: { job: ImportDetail }) {
  const [state, setState] = useState<Loaded<Projection | null>>({ kind: 'loading' })
  const [shown, setShown] = useState<Shown | null>(null)
  const [busy, setBusy] = useState(false)
  const [refusal, setRefusal] = useState<Refusal | null>(null)
  const loader = useMemo(() => createLoader(fetchProjection(job.id), setState, 'Could not load the projection'), [job.id])

  useEffect(() => {
    loader.load()
    return () => loader.abort()
  }, [loader])

  const project = async () => {
    setBusy(true)
    setRefusal(null)
    try {
      const { data, response } = await callWithResponse(() =>
        api.POST('/api/v1/imports/{import_id}/projection', { params: { path: { import_id: job.id } } }),
      )
      setShown({ projection: data, origin: originOf(response.status) })
    } catch (err) {
      const r = projectionRefusal(err)
      toastApiError(err, r.title)
      setRefusal(r)
    } finally {
      setBusy(false)
    }
  }

  // What this click returned wins over what was read on load; both are the server's answer.
  const current: Shown | null = shown ?? (state.kind === 'ready' && state.value ? { projection: state.value, origin: 'existing' } : null)

  let body: ReactNode
  if (current) body = <ProjectionSummaryView projection={current.projection} origin={current.origin} />
  else if (state.kind === 'loading') body = <LoadingBlock rows={2} />
  else if (state.kind === 'forbidden') body = <Forbidden permission="domain:read" correlationId={state.cid} />
  else if (state.kind === 'failed') body = <LoadFailed icon={Landmark} title="The projection could not be loaded" cid={state.cid} onRetry={loader.load} />
  else if (state.kind === 'not_found')
    body = (
      <p className="text-sm text-muted-foreground" data-testid="projection-unavailable">
        The projection could not be read (HTTP {state.status ?? '—'}).{state.cid ? ` Correlation ID: ${state.cid}` : ''}
      </p>
    )
  else
    body = !refusal && (
      <p className="text-sm text-muted-foreground" data-testid="projection-not-projected">
        Not projected yet.
      </p>
    )

  return (
    <PageSection
      icon={Landmark}
      title="Financial Domain projection"
      description="One immutable domain row per accepted canonical record, with lineage to it (D-29). Projecting again returns the same projection and writes nothing."
      headerRight={
        <RequirePermission permission="domain:project" fallback={null}>
          <Button size="sm" onClick={project} disabled={busy || state.kind === 'loading'} data-testid="domain-project">
            <Sigma className="mr-2 h-4 w-4" />
            {busy ? 'Projecting…' : 'Project to domain'}
          </Button>
        </RequirePermission>
      }
    >
      <div className="space-y-3" data-testid="projection-panel">
        {refusal && <RefusalNotice refusal={refusal} />}
        {body}
      </div>
    </PageSection>
  )
}

/**
 * The section on the import page: the existing projection for any role with domain:read, and
 * the "Project to domain" action only with domain:project. Nothing when the import is not
 * projectable. Both gates are cosmetic; the server decides (D-04).
 */
export function ProjectToDomain({ job }: { job: ImportDetail }) {
  if (!canProject(job)) return null
  return (
    <RequirePermission permission="domain:read" fallback={null}>
      <ProjectionPanel key={job.id} job={job} />
    </RequirePermission>
  )
}
