'use client'

import Link from 'next/link'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { Building2, GitBranch, ListTree, Wallet } from 'lucide-react'
import { api, call } from '@/api/client'
import { Forbidden } from '@/components/auth/Forbidden'
import { EmptyState } from '@/components/common/EmptyState'
import { LoadFailed } from '@/components/common/LoadFailed'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { MaskedValue, isMasked } from '@/components/common/MaskedValue'
import { PageHeader } from '@/components/common/PageHeader'
import { PageSection } from '@/components/common/PageSection'
import { createLoader, type Loaded } from '@/components/common/load'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { RATINGS_UI_ENABLED } from '@/config/features'
import { CreditValueCell } from './CreditValueCell'
import { RatingsPanel } from './ratings/RatingsPanel'
import { CREDIT_CRUMBS, Field, ImportRowLink, Lineage, Mono, NotInVocabulary } from './CreditParts'
import { exposureHref, keyText, latestExposures, type CounterpartyDetail, type CounterpartyVersion } from './credit'

function Version({ v }: { v: CounterpartyVersion }) {
  return (
    <div data-testid="counterparty-version" data-version={v.version} data-version-id={v.version_id} data-import-id={v.import_id}>
      <PageSection
        icon={GitBranch}
        title={`Version ${v.version}`}
        description={`Projected ${new Date(v.projected_at).toLocaleString()}. Immutable: a later import of the same key adds a version.`}
        headerRight={<ImportRowLink importId={v.import_id} ordinal={v.source_ordinal} testId="version-import-link" />}
      >
        <div className="grid gap-6 lg:grid-cols-2">
          <dl className="grid grid-cols-[160px_1fr] gap-y-1 text-sm" data-testid="version-values">
            <Field label="Name">
              <CreditValueCell value={v.counterparty_name} testId="version-name" className="font-sans" />
            </Field>
            <Field label="Segment">
              <span className="inline-flex flex-wrap items-center gap-2">
                <CreditValueCell value={v.segment} testId="version-segment" />
                <NotInVocabulary show={v.segment !== null && !isMasked(v.segment) && !v.segment_known} />
              </span>
            </Field>
            <Field label="Country">
              <CreditValueCell value={v.country} testId="version-country" />
            </Field>
          </dl>
          <Lineage v={v} schema="counterparty" />
        </div>
      </PageSection>
    </div>
  )
}

function Exposures({ detail }: { detail: CounterpartyDetail }) {
  const rows = latestExposures(detail.exposure_versions)
  return (
    <PageSection
      icon={Wallet}
      title={`Credit exposures (${rows.length})`}
      description="Projected credit-exposure versions naming this counterparty's business key in your tenant; the latest version of each is shown. A calculation resolves the counterparty within its dataset snapshot (D-37 §4)."
    >
      {rows.length === 0 ? (
        <p className="text-sm text-muted-foreground" data-testid="no-exposures">
          No projected credit exposure names this counterparty yet.
        </p>
      ) : (
        <Table data-testid="counterparty-exposures">
          <TableHeader>
            <TableRow>
              <TableHead>Exposure</TableHead>
              <TableHead>Facility</TableHead>
              <TableHead className="text-right">Drawn</TableHead>
              <TableHead className="text-right">Undrawn</TableHead>
              <TableHead>Currency</TableHead>
              <TableHead className="text-right">DPD</TableHead>
              <TableHead>Default</TableHead>
              <TableHead>Maturity</TableHead>
              <TableHead>Lineage</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map(({ latest: x, count }) => (
              <TableRow key={x.credit_exposure_id} data-testid="counterparty-exposure" data-id={x.credit_exposure_id}>
                <TableCell>
                  <Link href={exposureHref(x.credit_exposure_id)} className="font-mono text-sm text-primary hover:underline" data-testid="counterparty-exposure-link">
                    {keyText(x.business_key, x.credit_exposure_id)}
                  </Link>
                  <div className="text-xs text-muted-foreground">
                    v{x.version} · {count} version{count === 1 ? '' : 's'}
                  </div>
                </TableCell>
                <TableCell className="text-sm">
                  <CreditValueCell value={x.facility_type} />
                </TableCell>
                <TableCell className="text-right text-sm">
                  <CreditValueCell value={x.drawn_amount} testId="counterparty-exposure-drawn" />
                </TableCell>
                <TableCell className="text-right text-sm">
                  <CreditValueCell value={x.undrawn_amount} />
                </TableCell>
                <TableCell className="text-sm">
                  <CreditValueCell value={x.currency} />
                </TableCell>
                <TableCell className="text-right text-sm">
                  <CreditValueCell value={x.days_past_due} />
                </TableCell>
                <TableCell className="text-sm">
                  <CreditValueCell value={x.default_flag} />
                </TableCell>
                <TableCell className="text-sm">
                  <CreditValueCell value={x.maturity_date} />
                </TableCell>
                <TableCell>
                  <ImportRowLink importId={x.import_id} ordinal={x.source_ordinal} testId="counterparty-exposure-import-link" />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </PageSection>
  )
}

const fetchCounterparty = (id: string) => (signal: AbortSignal) =>
  call(() => api.GET('/api/v1/counterparties/{counterparty_id}', { params: { path: { counterparty_id: id } }, signal }))

/** FE-19.1: one counterparty, its versions (oldest first, as the server lists them) with lineage, and the exposures naming it. */
export function CounterpartyView({ counterpartyId }: { counterpartyId: string }) {
  const [state, setState] = useState<Loaded<CounterpartyDetail>>({ kind: 'loading' })
  const loader = useMemo(() => createLoader(fetchCounterparty(counterpartyId), setState, 'Could not load the counterparty'), [counterpartyId])

  useEffect(() => {
    loader.load()
    return () => loader.abort()
  }, [loader])

  const wrap = (body: ReactNode) => (
    <>
      <PageHeader title="Counterparty" breadcrumbs={[...CREDIT_CRUMBS, { label: counterpartyId.slice(0, 8) }]} />
      <div className="px-6 pb-6">{body}</div>
    </>
  )

  if (state.kind === 'loading') return <LoadingBlock rows={6} />
  if (state.kind === 'forbidden') return wrap(<Forbidden permission="domain:read" correlationId={state.cid} />)
  if (state.kind === 'failed') return wrap(<LoadFailed icon={Building2} title="The counterparty could not be loaded" cid={state.cid} onRetry={loader.load} />)
  if (state.kind === 'not_found')
    return wrap(
      <EmptyState
        icon={Building2}
        title="Counterparty not found"
        description={`It does not exist or is not visible to you.${state.cid ? ` Correlation ID: ${state.cid}` : ''}`}
      />,
    )

  const detail = state.value
  const key = detail.business_key
  const label = keyText(key, detail.counterparty_id)
  return (
    <>
      <PageHeader
        title={`Counterparty ${label}`}
        description={`Source ${detail.source_code} · ${detail.versions.length} version${detail.versions.length === 1 ? '' : 's'}, oldest first. Values are masked by the classification of their pinned schema field (D-43).`}
        breadcrumbs={[...CREDIT_CRUMBS, { label }]}
      />
      <div className="space-y-4 px-6 pb-6">
        <PageSection icon={ListTree} title="Identity" description="Stable per tenant, source system and business key (D-37 §2).">
          <dl className="grid grid-cols-[200px_1fr] gap-y-1 text-sm" data-testid="counterparty-identity">
            <Field label="Counterparty ID" testId="counterparty-id">
              <Mono>{detail.counterparty_id}</Mono>
            </Field>
            <Field label="Business key" testId="counterparty-business-key">
              {isMasked(key) ? <MaskedValue value={key} /> : <span className="font-mono">{key}</span>}
            </Field>
            <Field label="Source">
              <Link href={`/ingestion/sources/${detail.source_id}`} className="font-mono text-primary hover:underline">
                {detail.source_code}
              </Link>
            </Field>
            <Field label="First seen">
              <Mono>{new Date(detail.created_at).toLocaleString()}</Mono>
            </Field>
          </dl>
        </PageSection>
        {/* FE-20.1: the Ratings panel, only while NEXT_PUBLIC_RATINGS_UI is on; off, the Phase 19 page exactly (D-02). */}
        {RATINGS_UI_ENABLED && <RatingsPanel counterpartyId={detail.counterparty_id} counterpartyLabel={label} ratings={detail.ratings} />}
        {detail.versions.map((v) => (
          <Version key={v.version_id} v={v} />
        ))}
        <Exposures detail={detail} />
      </div>
    </>
  )
}
