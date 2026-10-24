import Link from 'next/link'
import { Masked } from '@/components/common/Masked'
import { Badge } from '@/components/ui/badge'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { formatTimestamp } from '@/lib/format'
import { ProvisionalBadge } from './InstrumentCatalogue'
import { ShownValue } from './ShownValue'
import { importHref, type InstrumentVersion } from './domain'
import {
  EFFECTIVE_FROM_NOT_WRITTEN,
  RATE_KIND_TEXT,
  TERMS_NOT_IN_CONTRACT,
  TERMS_NOT_WRITTEN,
  TERM_FIELDS,
  UNKNOWN_VALUE_TEXT,
  termsState,
  termsSummary,
  type TermsState,
} from './terms'

/*
 * FE-32.1 (D-61): the instrument detail's "Contract terms" panel and versions table, behind
 * NEXT_PUBLIC_INSTRUMENT_TERMS_UI. Read-only: every value is the server's exact text or its
 * masking marker; nothing here parses, converts or defaults a term.
 */

function UnknownBadge({ field }: { field: string }) {
  return (
    <Badge
      variant="outline"
      data-testid="term-unknown"
      data-field={field}
      title="Outside the seeded PROVISIONAL vocabulary: kept as given text and counted UNKNOWN_VALUE by the server."
    >
      {UNKNOWN_VALUE_TEXT}
    </Badge>
  )
}

function Recorded({ state }: { state: Extract<TermsState, { kind: 'recorded' }> }) {
  const t = state.terms
  const rateText = RATE_KIND_TEXT[state.rate]
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        {rateText && (
          <Badge variant="info" data-testid="terms-rate-kind">
            {rateText}
          </Badge>
        )}
        {t.provisional && <ProvisionalBadge />}
      </div>
      <dl className="grid grid-cols-[160px_1fr] gap-y-1 text-sm" data-testid="terms-values">
        {TERM_FIELDS.map((f) => {
          const value = t[f.key]
          const unknown = state.unknown.includes(f.key)
          const showUnit = 'rate' in f && f.rate && t.rate_unit && typeof value === 'string' && value !== ''
          return (
            <div key={f.key} className="contents" data-testid="term-field" data-field={f.key} data-unknown={unknown || undefined}>
              <dt className="text-muted-foreground">{f.label}</dt>
              <dd className="inline-flex flex-wrap items-center gap-2">
                <ShownValue value={value} testId={`term-${f.key}`} empty="Not stated" />
                {showUnit && <span className="text-muted-foreground">{t.rate_unit}</span>}
                {unknown && <UnknownBadge field={f.key} />}
              </dd>
            </div>
          )
        })}
      </dl>
      <p className="font-mono text-xs text-muted-foreground" title="Term version ID">
        Term version {t.term_version_id} · recorded {formatTimestamp(t.created_at)}
      </p>
    </div>
  )
}

/**
 * One version's typed rate terms: the recorded term row (rate type, index, spread, resets, cap,
 * floor, day count, payment frequency, lifecycle state) with PROVISIONAL badges and the server's
 * "not in vocabulary" fields; or why there is none (the pinned contract has no terms, or the
 * terms were inconsistent and the server wrote no term row).
 */
export function ContractTerms({ version }: { version: Pick<InstrumentVersion, 'terms' | 'terms_typed'> }) {
  const state = termsState(version)
  return (
    <div data-testid="contract-terms" data-state={state.kind} data-rate={state.kind === 'recorded' ? state.rate : undefined}>
      <h3 className="mb-2 mt-4 flex items-center gap-2 text-sm font-medium">
        Contract terms
        {state.kind !== 'not-in-contract' && <ProvisionalBadge />}
      </h3>
      {state.kind === 'recorded' && <Recorded state={state} />}
      {state.kind === 'not-in-contract' && (
        <p
          className="text-sm text-muted-foreground"
          data-testid="terms-empty"
          title="The pinned schema version is not the INSTRUMENT_RATE_TERMS contract (projector v3, D-61), so no rate terms are typed."
        >
          {TERMS_NOT_IN_CONTRACT}
        </p>
      )}
      {state.kind === 'not-written' && (
        <p
          className="text-sm text-muted-foreground"
          data-testid="terms-empty"
          title="TERMS_INCONSISTENT: FIXED with a reference index, FLOATING with no reset frequency, or a floor above the cap. The instrument version is recorded; its terms stay in the canonical record."
        >
          {TERMS_NOT_WRITTEN}
        </p>
      )}
    </div>
  )
}

/**
 * Every version of the instrument, oldest first as the server lists them, with its business
 * effective date ("Effective from", D-61 §5) beside the system time of its import ("Imported at").
 * The two are kept apart and neither is re-ordered: no "current" version is defined here.
 */
export function VersionsTable({ versions }: { versions: readonly InstrumentVersion[] }) {
  return (
    <Table data-testid="versions-table">
      <TableHeader>
        <TableRow>
          <TableHead>Version</TableHead>
          <TableHead>Effective from</TableHead>
          <TableHead>Imported at</TableHead>
          <TableHead>Import</TableHead>
          <TableHead>Rate terms</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {versions.map((v) => {
          const state = termsState(v)
          return (
            <TableRow key={v.version_id} data-testid="versions-row" data-version={v.version} data-version-id={v.version_id}>
              <TableCell className="font-mono text-sm">v{v.version}</TableCell>
              <TableCell className="text-sm">
                <ShownValue
                  value={v.effective_from}
                  testId="version-effective-from"
                  empty={state.kind === 'not-written' ? EFFECTIVE_FROM_NOT_WRITTEN : 'Not stated'}
                />
              </TableCell>
              <TableCell className="text-sm">
                <span className="font-mono text-xs" data-testid="version-imported-at" data-value={v.imported_at}>
                  {formatTimestamp(v.imported_at)}
                </span>
              </TableCell>
              <TableCell>
                <Link href={importHref(v.import_id)} className="font-mono text-xs text-primary hover:underline">
                  Import {v.import_id.slice(0, 8)} · row {v.source_ordinal}
                </Link>
              </TableCell>
              <TableCell className="text-sm" data-testid="version-terms-summary" data-state={state.kind}>
                {state.kind === 'recorded' && state.rate === 'masked' ? <Masked value={state.terms.rate_type} /> : termsSummary(state)}
              </TableCell>
            </TableRow>
          )
        })}
      </TableBody>
    </Table>
  )
}
