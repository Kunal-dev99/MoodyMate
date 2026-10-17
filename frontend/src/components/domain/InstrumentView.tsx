'use client'

import Link from 'next/link'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { CalendarClock, GitBranch, Landmark, ListTree } from 'lucide-react'
import { api, call } from '@/api/client'
import { Forbidden } from '@/components/auth/Forbidden'
import { EmptyState } from '@/components/common/EmptyState'
import { LoadFailed } from '@/components/common/LoadFailed'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { MaskedValue, isMasked } from '@/components/common/MaskedValue'
import { PageHeader } from '@/components/common/PageHeader'
import { PageSection } from '@/components/common/PageSection'
import { createLoader, type Loaded } from '@/components/common/load'
import { Badge } from '@/components/ui/badge'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { ProvisionalBadge } from './InstrumentCatalogue'
import { ShownValue } from './ShownValue'
import {
  cashFlowsOf,
  importHref,
  vocabularyEntry,
  type CashFlow,
  type InstrumentDetail,
  type InstrumentType,
  type InstrumentVersion,
} from './domain'

type Ready = { detail: InstrumentDetail; types: InstrumentType[] }

const CRUMBS = [{ label: 'Financial Domain', href: '/domain' }]

function Field({ label, children, testId }: { label: string; children: ReactNode; testId?: string }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd data-testid={testId}>{children}</dd>
    </>
  )
}

const Mono = ({ children }: { children: ReactNode }) => <span className="break-all font-mono text-xs">{children}</span>

/** GET /instruments/{id}, plus the seeded type vocabulary (GET /instruments, one row) for its badges. */
const fetchInstrument = (instrumentId: string) => (signal: AbortSignal) =>
  Promise.all([
    call(() => api.GET('/api/v1/instruments/{instrument_id}', { params: { path: { instrument_id: instrumentId } }, signal })),
    call(() => api.GET('/api/v1/instruments', { params: { query: { limit: 1 } }, signal })),
  ]).then(([detail, page]): Ready => ({ detail, types: page.instrument_types }))

function TypeValue({ version, types }: { version: InstrumentVersion; types: InstrumentType[] }) {
  const code = isMasked(version.instrument_type) ? null : version.instrument_type
  const vocab = vocabularyEntry(types, code)
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <ShownValue value={version.instrument_type} testId="version-type" />
      {version.instrument_type_known && vocab?.provisional && <ProvisionalBadge />}
      {code !== null && !version.instrument_type_known && (
        <Badge variant="outline" data-testid="type-unknown">
          Not in vocabulary: modules warn on it
        </Badge>
      )}
    </span>
  )
}

function Version({ version, types }: { version: InstrumentVersion; types: InstrumentType[] }) {
  const v = version
  return (
    <div data-testid="instrument-version" data-version={v.version} data-version-id={v.version_id} data-import-id={v.import_id}>
      <PageSection
        icon={GitBranch}
        title={`Version ${v.version}`}
        description={`Projected ${new Date(v.projected_at).toLocaleString()}. Immutable: a later import of the same key adds a version.`}
        headerRight={
          <Link href={importHref(v.import_id)} className="font-mono text-xs text-primary hover:underline" data-testid="version-import-link" data-import-id={v.import_id}>
            Import {v.import_id.slice(0, 8)} · row {v.source_ordinal}
          </Link>
        }
      >
        <div>
          <div className="grid gap-6 lg:grid-cols-2">
            <dl className="grid grid-cols-[160px_1fr] gap-y-1 text-sm" data-testid="version-values">
              <Field label="Instrument type">
                <TypeValue version={v} types={types} />
              </Field>
              <Field label="Currency">
                <ShownValue value={v.currency} testId="version-currency" />
              </Field>
              <Field label="Notional" testId="version-notional-field">
                <ShownValue value={v.notional} testId="version-notional" />
                {!isMasked(v.notional) && v.notional !== null && !isMasked(v.currency) && v.currency && (
                  <span className="text-muted-foreground"> {v.currency}</span>
                )}
              </Field>
              <Field label="Interest rate">
                <ShownValue value={v.interest_rate} testId="version-interest-rate" />
                {v.interest_rate_unit && !isMasked(v.interest_rate) && v.interest_rate !== null && (
                  <span className="text-muted-foreground"> {v.interest_rate_unit}</span>
                )}
              </Field>
              <Field label="Maturity date">
                <ShownValue
                  value={v.maturity_date}
                  testId="version-maturity-date"
                  empty="— not projected (empty, or the pinned field is free text: TYPE_NOT_PROJECTED). The source value stays in the canonical record."
                />
              </Field>
            </dl>
            <dl className="grid grid-cols-[160px_1fr] gap-y-1 text-sm" data-testid="version-lineage">
              <Field label="Import" testId="version-import">
                <Link href={importHref(v.import_id)} className="font-mono text-xs text-primary hover:underline">
                  {v.import_id}
                </Link>
              </Field>
              <Field label="Source row ordinal" testId="version-source-ordinal">
                <span className="font-mono">{v.source_ordinal}</span>
              </Field>
              <Field label="Canonical record" testId="version-canonical-record">
                <Mono>{v.canonical_record_id}</Mono>
              </Field>
              <Field label="Source record">
                <Mono>{v.source_record_id}</Mono>
              </Field>
              <Field label="Schema version">
                <span className="font-mono">{v.schema_version != null ? `instrument v${v.schema_version}` : '—'}</span>{' '}
                <span className="font-mono text-xs text-muted-foreground">{v.schema_version_id}</span>
              </Field>
              <Field label="Projection">
                <Mono>{v.projection_id}</Mono>
              </Field>
              <Field label="Payload SHA-256">
                <Mono>{v.payload_sha256}</Mono>
              </Field>
              <Field label="Version ID">
                <Mono>{v.version_id}</Mono>
              </Field>
            </dl>
          </div>
  
          <h3 className="mb-2 mt-4 text-sm font-medium">Attributes</h3>
          {v.attributes.length === 0 ? (
            <p className="text-sm text-muted-foreground" data-testid="version-no-attributes">
              No extension attributes in this version.
            </p>
          ) : (
            <Table data-testid="version-attributes">
              <TableHeader>
                <TableRow>
                  <TableHead>Attribute</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Classification</TableHead>
                  <TableHead>Value</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {v.attributes.map((a) => (
                  <TableRow key={a.code} data-testid="version-attribute" data-code={a.code}>
                    <TableCell className="font-mono text-sm">{a.code}</TableCell>
                    <TableCell className="font-mono text-xs">{a.data_type}</TableCell>
                    <TableCell className="text-xs">{a.classification}</TableCell>
                    <TableCell className="text-sm">
                      <ShownValue value={a.value} testId="attribute-value" />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </div>
      </PageSection>
    </div>
  )
}

function CashFlows({ flows }: { flows: CashFlow[] }) {
  return (
    <PageSection
      icon={CalendarClock}
      title={`Cash flows (${flows.length})`}
      description="Projected cash-flow events naming this instrument's business key in your tenant. Type and origin come from seeded vocabularies."
      headerRight={<ProvisionalBadge />}
    >
      {flows.length === 0 ? (
        <p className="text-sm text-muted-foreground" data-testid="no-cash-flows">
          No projected cash-flow event names this instrument yet.
        </p>
      ) : (
        <Table data-testid="cash-flows">
          <TableHeader>
            <TableRow>
              <TableHead>Due date</TableHead>
              <TableHead className="text-right">Amount</TableHead>
              <TableHead>Currency</TableHead>
              <TableHead>Type</TableHead>
              <TableHead>Origin</TableHead>
              <TableHead>Lineage</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {flows.map((c) => (
              <TableRow key={c.cashflow_event_id} data-testid="cash-flow" data-id={c.cashflow_event_id}>
                <TableCell className="text-sm">
                  <ShownValue value={c.due_date} />
                </TableCell>
                <TableCell className="text-right text-sm">
                  <ShownValue value={c.amount} testId="cash-flow-amount" />
                </TableCell>
                <TableCell className="text-sm">
                  <ShownValue value={c.currency} />
                </TableCell>
                <TableCell className="text-sm">
                  <ShownValue value={c.cashflow_type} />
                </TableCell>
                <TableCell className="text-sm">
                  <ShownValue value={c.origin} />
                </TableCell>
                <TableCell>
                  <Link href={importHref(c.import_id)} className="font-mono text-xs text-primary hover:underline" data-testid="cash-flow-import-link">
                    Import {c.import_id.slice(0, 8)} · row {c.source_ordinal}
                  </Link>
                  <div className="font-mono text-xs text-muted-foreground" title="Canonical record">
                    {c.canonical_record_id}
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </PageSection>
  )
}

/** FE-14.1: one instrument, its versions (oldest first, as the server lists them) with attributes and lineage, and its cash flows. */
export function InstrumentView({ instrumentId }: { instrumentId: string }) {
  const [state, setState] = useState<Loaded<Ready>>({ kind: 'loading' })
  const loader = useMemo(() => createLoader(fetchInstrument(instrumentId), setState, 'Could not load the instrument'), [instrumentId])

  useEffect(() => {
    loader.load()
    return () => loader.abort()
  }, [loader])

  const wrap = (body: ReactNode) => (
    <>
      <PageHeader title="Instrument" breadcrumbs={[...CRUMBS, { label: instrumentId.slice(0, 8) }]} />
      <div className="px-6 pb-6">{body}</div>
    </>
  )

  if (state.kind === 'loading') return <LoadingBlock rows={6} />
  if (state.kind === 'forbidden') return wrap(<Forbidden permission="domain:read" correlationId={state.cid} />)
  if (state.kind === 'failed') return wrap(<LoadFailed icon={Landmark} title="The instrument could not be loaded" cid={state.cid} onRetry={loader.load} />)
  if (state.kind === 'not_found')
    return wrap(
      <EmptyState
        icon={Landmark}
        title="Instrument not found"
        description={`It does not exist or is not visible to you.${state.cid ? ` Correlation ID: ${state.cid}` : ''}`}
      />,
    )

  const { detail, types } = state.value
  const key = detail.business_key
  const title = isMasked(key) ? `Instrument ${detail.instrument_id.slice(0, 8)}` : `Instrument ${key}`
  return (
    <>
      <PageHeader
        title={title}
        description={`Source ${detail.source_code} · ${detail.versions.length} version${detail.versions.length === 1 ? '' : 's'}, oldest first. Values are masked by the classification of their pinned schema field.`}
        breadcrumbs={[...CRUMBS, { label: isMasked(key) ? detail.instrument_id.slice(0, 8) : key }]}
      />
      <div className="space-y-4 px-6 pb-6">
        <PageSection icon={ListTree} title="Identity" description="Stable per tenant, source system and business key (D-29 §3).">
          <dl className="grid grid-cols-[200px_1fr] gap-y-1 text-sm" data-testid="instrument-identity">
            <Field label="Instrument ID" testId="instrument-id">
              <Mono>{detail.instrument_id}</Mono>
            </Field>
            <Field label="Business key" testId="instrument-business-key">
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
        {detail.versions.map((v) => (
          <Version key={v.version_id} version={v} types={types} />
        ))}
        <CashFlows flows={cashFlowsOf(detail)} />
      </div>
    </>
  )
}
