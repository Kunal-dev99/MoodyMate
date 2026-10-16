'use client'

import Link from 'next/link'
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { BarChart3, FilePlus2, Info, TableProperties } from 'lucide-react'
import { api, call } from '@/api/client'
import { Forbidden } from '@/components/auth/Forbidden'
import { RequirePermission } from '@/components/auth/RequirePermission'
import { EmptyState } from '@/components/common/EmptyState'
import { LoadFailed } from '@/components/common/LoadFailed'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { MaskedValue } from '@/components/common/MaskedValue'
import { PageSection } from '@/components/common/PageSection'
import { startLoad, type LoadFailure } from '@/components/common/load'
import { ImportStatusPill } from '@/components/ingestion/ImportStatusPill'
import {
  contractFlag,
  nullRatePercent,
  nullRateText,
  otherCandidates,
  proposalHref,
  suggestedTypeLabel,
  targetSuggestion,
  valueDisplay,
  type ColumnProfile,
  type ImportProfile,
} from '@/components/ingestion/profile'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'

/*
 * FE-9.1: the import's field profile, read-only. This file only reads (GET); the one
 * schema action is a link to the existing Phase 5 "New proposal" page. A test greps this
 * file for mutating calls.
 */

type State = { kind: 'loading' } | { kind: 'ready'; profile: ImportProfile } | LoadFailure

function Value({ value }: { value: Parameters<typeof valueDisplay>[0] }) {
  const d = valueDisplay(value)
  if (d.kind === 'masked') return <MaskedValue value={d.value} />
  if (d.kind === 'empty') return <span className="text-muted-foreground">—</span>
  return <span className="break-all font-mono text-xs">{d.text}</span>
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd>{children}</dd>
    </>
  )
}

/** The Phase 5 "New proposal" link, with the import's target schema chosen (ImportProfile.schema_id). */
function ProposalLink({ schemaId }: { schemaId: string }) {
  return (
    <Button asChild size="sm" variant="outline">
      <Link href={proposalHref(schemaId)} data-testid="profile-new-proposal">
        <FilePlus2 className="mr-2 h-4 w-4" />
        New proposal
      </Link>
    </Button>
  )
}

function ColumnRow({ c }: { c: ColumnProfile }) {
  const flag = contractFlag(c)
  const pct = nullRatePercent(c.null_rate)
  const target = targetSuggestion(c)
  const others = otherCandidates(c)
  return (
    <TableRow data-testid="profile-column" data-column={c.name}>
      <TableCell className="font-mono text-xs text-muted-foreground">{c.position}</TableCell>
      <TableCell>
        <div className="font-mono text-sm">{c.name}</div>
        {flag && (
          <Badge variant="warning" className="mt-1" data-testid="profile-not-in-contract">
            {flag}
          </Badge>
        )}
        <div className="text-xs text-muted-foreground">{c.classification}</div>
      </TableCell>
      <TableCell>
        <Badge variant="info" data-testid="profile-suggested-type">
          {suggestedTypeLabel(c)}
        </Badge>
        {others.length > 0 && <div className="mt-1 font-mono text-xs text-muted-foreground">also fits: {others.join(', ')}</div>}
      </TableCell>
      <TableCell className="w-40">
        {pct === null ? (
          <span className="text-muted-foreground">—</span>
        ) : (
          <Progress value={pct} className="h-2" aria-label={`Null rate ${nullRateText(c.null_rate)}`} data-testid="profile-null-bar" />
        )}
        <div className="mt-1 font-mono text-xs" data-testid="profile-null-rate">
          {nullRateText(c.null_rate)} <span className="text-muted-foreground">({c.null_count} empty)</span>
        </div>
      </TableCell>
      <TableCell className="text-right font-mono text-sm">
        {c.distinct_count}
        {c.distinct_capped && '+'}
      </TableCell>
      <TableCell className="text-xs">
        <div>
          <span className="text-muted-foreground">min </span>
          <Value value={c.min} />
        </div>
        <div>
          <span className="text-muted-foreground">max </span>
          <Value value={c.max} />
        </div>
        {c.max_length !== null && <div className="text-muted-foreground">max length {c.max_length}</div>}
        {c.max_precision !== null && (
          <div className="text-muted-foreground">
            precision {c.max_precision}, scale {c.max_scale ?? 0}
          </div>
        )}
      </TableCell>
      <TableCell className="text-xs">
        {c.top_values.length === 0 ? (
          <span className="text-muted-foreground">—</span>
        ) : (
          <ul className="space-y-0.5" data-testid="profile-top-values">
            {c.top_values.map((t, i) => (
              <li key={i}>
                <Value value={t.value} />
                {t.truncated && <span className="text-muted-foreground">…</span>}
                <span className="text-muted-foreground"> × {t.count}</span>
              </li>
            ))}
            {!c.top_values_exact && <li className="text-muted-foreground">approximate (distinct cap reached)</li>}
          </ul>
        )}
      </TableCell>
      <TableCell>
        {target ? (
          <Badge variant="outline" data-testid="profile-target-suggestion">
            {target}
          </Badge>
        ) : (
          <span className="text-xs text-muted-foreground">No match</span>
        )}
      </TableCell>
    </TableRow>
  )
}

function ProfileView({ profile: p }: { profile: ImportProfile }) {
  const flagged = p.columns.filter((c) => !c.in_contract).length
  const flags = Object.entries(p.summary.flag_counts)
  return (
    <div className="space-y-4">
      <PageSection icon={BarChart3} title="Profile" description="Observed when the file was received. Read-only.">
        <dl className="grid grid-cols-[200px_1fr] gap-y-1 text-sm" data-testid="profile-summary">
          <Field label="Import ID">
            <span className="font-mono text-xs">{p.import_id}</span>
          </Field>
          <Field label="Import status">
            <ImportStatusPill status={p.import_status} />
          </Field>
          <Field label="Profile snapshot">
            <span className="font-mono text-xs">{p.snapshot_id}</span>
            <span className="text-muted-foreground"> · profiler v{p.profiler_version}</span>
          </Field>
          <Field label="Profiled">
            <span className="font-mono text-xs">{new Date(p.profiled_at).toLocaleString()}</span>
          </Field>
          <Field label="Source contract">
            <span className="font-mono">v{p.contract_version}</span>{' '}
            <span className="font-mono text-xs text-muted-foreground">{p.source_contract_id}</span>
          </Field>
          <Field label="Schema version">
            <span className="font-mono">
              {p.schema_code} v{p.schema_version}
            </span>{' '}
            <span className="font-mono text-xs text-muted-foreground">{p.schema_version_id}</span>
          </Field>
          <Field label="Rows / columns">
            <span className="font-mono">
              {p.row_count} / {p.column_count}
            </span>
          </Field>
          <Field label="Row shape">
            <span className="font-mono text-xs">
              {p.summary.blank_rows} blank · {p.summary.rows_with_missing_cells} with missing cells ·{' '}
              {p.summary.rows_with_extra_cells} with extra cells
            </span>
          </Field>
          {flags.length > 0 && (
            <Field label="Row flags">
              <span className="font-mono text-xs">{flags.map(([k, v]) => `${k}: ${v}`).join(' · ')}</span>
            </Field>
          )}
          <Field label="Content SHA-256">
            <span className="break-all font-mono text-xs">{p.content_sha256}</span>
          </Field>
        </dl>
      </PageSection>

      <PageSection
        icon={TableProperties}
        title="Columns"
        description="Types, contract matches and target fields are suggestions only: nothing is changed from here."
        actions={
          <RequirePermission permission="schema:propose" fallback={null}>
            <ProposalLink schemaId={p.schema_id} />
          </RequirePermission>
        }
      >
        <p className="mb-3 flex items-center gap-2 text-sm text-muted-foreground" data-testid="profile-flag-count">
          <Info className="h-4 w-4" />
          {flagged === 0
            ? 'Every column is expected by the source contract.'
            : `${flagged} column${flagged === 1 ? ' is' : 's are'} not in the source contract. A schema change goes through a proposal.`}
        </p>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>#</TableHead>
              <TableHead>Column</TableHead>
              <TableHead>Type</TableHead>
              <TableHead>Null rate</TableHead>
              <TableHead className="text-right">Distinct</TableHead>
              <TableHead>Range</TableHead>
              <TableHead>Top values</TableHead>
              <TableHead>Target field</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {p.columns.map((c) => (
              <ColumnRow key={c.position} c={c} />
            ))}
          </TableBody>
        </Table>
      </PageSection>
    </div>
  )
}

/** The Profile tab of an import (FE-9.1): GET /imports/{id}/profile, needs import:read. */
export function ImportProfileTab({ importId }: { importId: string }) {
  const [state, setState] = useState<State>({ kind: 'loading' })
  const request = useRef<AbortController | null>(null)

  const load = useCallback(() => {
    request.current = startLoad(
      (signal) =>
        call(() => api.GET('/api/v1/imports/{import_id}/profile', { params: { path: { import_id: importId } }, signal })),
      { onReady: (profile) => setState({ kind: 'ready', profile }), onFailure: setState, failureTitle: 'Could not load the profile' },
      request.current,
    )
  }, [importId])

  useEffect(() => {
    setState({ kind: 'loading' })
    load()
    return () => request.current?.abort()
  }, [load])

  if (state.kind === 'loading') return <LoadingBlock rows={6} />
  if (state.kind === 'forbidden') return <Forbidden permission="import:read" correlationId={state.cid} />
  if (state.kind === 'failed')
    return (
      <LoadFailed
        icon={BarChart3}
        title="The profile could not be loaded"
        cid={state.cid}
        onRetry={() => {
          setState({ kind: 'loading' })
          load()
        }}
      />
    )
  if (state.kind === 'not_found')
    return (
      <div data-testid="profile-empty">
        <EmptyState
          icon={BarChart3}
          title="No profile for this import"
          description={`The import was not profiled, or the profile is not visible to you.${state.cid ? ` Correlation ID: ${state.cid}` : ''}`}
        />
      </div>
    )
  return <ProfileView profile={state.profile} />
}
