'use client'

import Link from 'next/link'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { GitBranch, ListTree, Wallet } from 'lucide-react'
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
import { CreditValueCell } from './CreditValueCell'
import { CREDIT_CRUMBS, Field, ImportRowLink, Lineage, Mono, NotInVocabulary } from './CreditParts'
import { EXPOSURE_PARAM_PREFIX, counterpartiesHref, keyText, type ExposureDetail, type ExposureVersion } from './credit'
import { formatTimestamp } from '@/lib/format'

/** The value with its currency when both are in clear (the currency is public, D-43 §1). */
function Amount({ value, currency, testId }: { value: ExposureVersion['drawn_amount']; currency: ExposureVersion['currency']; testId: string }) {
  return (
    <>
      <CreditValueCell value={value} testId={testId} money />
      {!isMasked(value) && value !== null && !isMasked(currency) && currency && <span className="text-muted-foreground"> {currency}</span>}
    </>
  )
}

function Version({ v }: { v: ExposureVersion }) {
  const key = v.counterparty_key
  return (
    <div data-testid="exposure-version" data-version={v.version} data-version-id={v.version_id} data-import-id={v.import_id}>
      <PageSection
        icon={GitBranch}
        title={`Version ${v.version}`}
        description={`Projected ${formatTimestamp(v.projected_at)}. Immutable: a later import of the same key adds a version.`}
        headerRight={<ImportRowLink importId={v.import_id} ordinal={v.source_ordinal} testId="version-import-link" />}
      >
        <div className="grid gap-6 lg:grid-cols-2">
          <dl className="grid grid-cols-[180px_1fr] gap-y-1 text-sm" data-testid="version-values">
            <Field label="Counterparty key">
              <CreditValueCell value={key} testId="version-counterparty-key" />
              {key !== null && !isMasked(key) && (
                <Link
                  href={`${counterpartiesHref}?${EXPOSURE_PARAM_PREFIX}counterparty_key=${encodeURIComponent(key)}`}
                  className="ml-2 text-xs text-primary hover:underline"
                  data-testid="version-counterparty-exposures"
                >
                  exposures naming it
                </Link>
              )}
            </Field>
            <Field label="Instrument key">
              <CreditValueCell value={v.instrument_key} empty="— (optional; not read by v1 modules)" />
            </Field>
            <Field label="As of date">
              <CreditValueCell value={v.as_of_date} testId="version-as-of" />
            </Field>
            <Field label="Currency">
              <CreditValueCell value={v.currency} testId="version-currency" />
            </Field>
            <Field label="Facility type">
              <span className="inline-flex flex-wrap items-center gap-2">
                <CreditValueCell value={v.facility_type} testId="version-facility" />
                <NotInVocabulary show={v.facility_type !== null && !isMasked(v.facility_type) && !v.facility_type_known} />
              </span>
            </Field>
            <Field label="Drawn amount" testId="version-drawn-field">
              <Amount value={v.drawn_amount} currency={v.currency} testId="version-drawn" />
            </Field>
            <Field label="Undrawn amount">
              <Amount value={v.undrawn_amount} currency={v.currency} testId="version-undrawn" />
            </Field>
            <Field label="Days past due">
              <CreditValueCell value={v.days_past_due} testId="version-dpd" />
            </Field>
            <Field label="Default flag">
              <span className="inline-flex items-center gap-2">
                <CreditValueCell value={v.default_flag} testId="version-default" />
                {v.default_flag === true && (
                  <Badge variant="destructive" data-testid="version-defaulted">
                    In default (as given)
                  </Badge>
                )}
              </span>
            </Field>
            <Field label="Origination date">
              <CreditValueCell value={v.origination_date} />
            </Field>
            <Field label="Origination grade">
              <span className="inline-flex flex-wrap items-center gap-2">
                <CreditValueCell value={v.origination_grade} testId="version-grade" />
                <NotInVocabulary show={v.origination_grade !== null && !isMasked(v.origination_grade) && !v.origination_grade_known} />
              </span>
            </Field>
            <Field label="Maturity date">
              <CreditValueCell value={v.maturity_date} testId="version-maturity" />
            </Field>
            <Field label="Effective interest rate">
              <CreditValueCell value={v.effective_interest_rate} testId="version-eir" />
              {v.effective_interest_rate_unit && !isMasked(v.effective_interest_rate) && v.effective_interest_rate !== null && (
                <span className="text-muted-foreground"> {v.effective_interest_rate_unit}</span>
              )}
            </Field>
            <Field label="Collateral type">
              <span className="inline-flex flex-wrap items-center gap-2">
                <CreditValueCell value={v.collateral_type} empty="— none" testId="version-collateral-type" />
                <NotInVocabulary show={v.collateral_type !== null && !isMasked(v.collateral_type) && !v.collateral_type_known} />
              </span>
            </Field>
            <Field label="Collateral value">
              <Amount value={v.collateral_value} currency={v.currency} testId="version-collateral-value" />
            </Field>
          </dl>
          <Lineage v={v} schema="credit_exposure" />
        </div>
      </PageSection>
    </div>
  )
}

const fetchExposure = (id: string) => (signal: AbortSignal) =>
  call(() => api.GET('/api/v1/credit-exposures/{exposure_id}', { params: { path: { exposure_id: id } }, signal }))

/** FE-19.1: one credit exposure and its versions (oldest first), each with its lineage to an import and source row. */
export function ExposureView({ exposureId }: { exposureId: string }) {
  const [state, setState] = useState<Loaded<ExposureDetail>>({ kind: 'loading' })
  const loader = useMemo(() => createLoader(fetchExposure(exposureId), setState, 'Could not load the credit exposure'), [exposureId])

  useEffect(() => {
    loader.load()
    return () => loader.abort()
  }, [loader])

  const wrap = (body: ReactNode) => (
    <>
      <PageHeader title="Credit exposure" breadcrumbs={[...CREDIT_CRUMBS, { label: exposureId.slice(0, 8) }]} />
      <div className="px-6 pb-6">{body}</div>
    </>
  )

  if (state.kind === 'loading') return <LoadingBlock rows={6} />
  if (state.kind === 'forbidden') return wrap(<Forbidden permission="domain:read" correlationId={state.cid} />)
  if (state.kind === 'failed') return wrap(<LoadFailed icon={Wallet} title="The credit exposure could not be loaded" cid={state.cid} onRetry={loader.load} />)
  if (state.kind === 'not_found')
    return wrap(
      <EmptyState
        icon={Wallet}
        title="Credit exposure not found"
        description={`It does not exist or is not visible to you.${state.cid ? ` Correlation ID: ${state.cid}` : ''}`}
      />,
    )

  const detail = state.value
  const key = detail.business_key
  const label = keyText(key, detail.credit_exposure_id)
  return (
    <>
      <PageHeader
        title={`Credit exposure ${label}`}
        description={`Source ${detail.source_code} · ${detail.versions.length} version${detail.versions.length === 1 ? '' : 's'}, oldest first. Amounts are exact decimal text; drawn, undrawn and collateral amounts are confidential and masked by your clearance.`}
        breadcrumbs={[...CREDIT_CRUMBS, { label }]}
      />
      <div className="space-y-4 px-6 pb-6">
        <PageSection icon={ListTree} title="Identity" description="Stable per tenant, source system and business key.">
          <dl className="grid grid-cols-[200px_1fr] gap-y-1 text-sm" data-testid="exposure-identity">
            <Field label="Exposure ID" testId="exposure-id">
              <Mono>{detail.credit_exposure_id}</Mono>
            </Field>
            <Field label="Business key" testId="exposure-business-key">
              {isMasked(key) ? <MaskedValue value={key} /> : <span className="font-mono">{key}</span>}
            </Field>
            <Field label="Source">
              <Link href={`/ingestion/sources/${detail.source_id}`} className="font-mono text-primary hover:underline">
                {detail.source_code}
              </Link>
            </Field>
            <Field label="First seen">
              <Mono>{formatTimestamp(detail.created_at)}</Mono>
            </Field>
          </dl>
        </PageSection>
        {detail.versions.map((v) => (
          <Version key={v.version_id} v={v} />
        ))}
      </div>
    </>
  )
}
