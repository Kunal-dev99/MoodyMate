'use client'

import Link from 'next/link'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { Building2, ChevronLeft, ChevronRight, Landmark, Wallet } from 'lucide-react'
import { api, call, type Schemas } from '@/api/client'
import { Forbidden } from '@/components/auth/Forbidden'
import { useCurrentSession } from '@/components/auth/SessionProvider'
import { EmptyState } from '@/components/common/EmptyState'
import { LoadFailed } from '@/components/common/LoadFailed'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { MaskedValue, isMasked } from '@/components/common/MaskedValue'
import { PageSection } from '@/components/common/PageSection'
import { startLoad, type LoadFailure } from '@/components/common/load'
import { ListPager, TruncationNote, cursorRefusedBody, useCursorPaging } from '@/components/common/ListPager'
import { LIST_PAGING_UI_ENABLED } from '@/config/features'
import { cursorQuery, truncationOf, type Truncation } from '@/lib/paging'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { CreditValueCell } from './CreditValueCell'
import { CreditVocabularies } from './CreditVocabularies'
import {
  EXPOSURE_PARAM_PREFIX,
  NO_COUNTERPARTY_FILTERS,
  NO_EXPOSURE_FILTERS,
  codesOf,
  counterpartyHref,
  counterpartyQuery,
  exposureHref,
  exposureQuery,
  importHref,
  keyText,
  problemText,
  type CounterpartyFilters,
  type CounterpartyPage,
  type ExposureFilters,
  type ExposurePage,
  type VocabularyEntry,
} from './credit'

const PAGE_SIZE = 50
const selectClass = 'h-9 rounded-md border border-input bg-background px-2 text-sm'

type Paged<P> = { kind: 'loading' } | { kind: 'ready'; page: P } | LoadFailure
type SourceOption = Pick<Schemas['SourceSummary'], 'id' | 'code' | 'name'>
type Vocab = { kind: 'loading' } | { kind: 'ready'; items: VocabularyEntry[] } | LoadFailure

function read<K extends string>(params: URLSearchParams, keys: readonly K[], prefix = ''): Record<K, string> {
  return Object.fromEntries(keys.map((k) => [k, params.get(`${prefix}${k}`) ?? ''])) as Record<K, string>
}

const COUNTERPARTY_KEYS = Object.keys(NO_COUNTERPARTY_FILTERS) as (keyof CounterpartyFilters)[]
const EXPOSURE_KEYS = Object.keys(NO_EXPOSURE_FILTERS) as (keyof ExposureFilters)[]

/** The URL filter state of one catalogue: applied values, a draft, and a writer that keeps the other catalogue's params. */
function useUrlFilters<K extends string>(keys: readonly K[], prefix: string) {
  const router = useRouter()
  const pathname = usePathname()
  const params = useSearchParams()
  const raw = params.toString()
  const applied = useMemo(() => read(new URLSearchParams(raw), keys, prefix), [raw, keys, prefix])
  const [draft, setDraft] = useState<Record<K, string>>(applied)
  useEffect(() => setDraft(applied), [applied])
  const apply = (next: Record<K, string>) => {
    const search = new URLSearchParams(raw)
    for (const k of keys) {
      const value = next[k].trim()
      if (value) search.set(`${prefix}${k}`, value)
      else search.delete(`${prefix}${k}`)
    }
    const qs = search.toString()
    router.replace(`${pathname}${qs ? `?${qs}` : ''}`, { scroll: false })
  }
  const filtered = keys.some((k) => applied[k].trim() !== '')
  return { applied, draft, setDraft, apply, filtered }
}

/**
 * A cancellable paged GET whose offset resets when the query changes. FE-33.1: the page is
 * selected by the server's next_cursor, bound to the query (NEXT_PUBLIC_LIST_PAGING_UI=off:
 * by offset, as before).
 */
function usePaged<P, Q>(
  query: Q,
  fetcher: (query: Q, page: { offset: number } | { cursor?: string }, signal: AbortSignal) => Promise<P>,
  failureTitle: string,
) {
  const [paging, setPaging] = useState<{ query: Q; offset: number }>({ query, offset: 0 })
  const offset = paging.query === query ? paging.offset : 0
  const cursorPaging = useCursorPaging(JSON.stringify(query))
  const cursor = LIST_PAGING_UI_ENABLED ? cursorPaging.cursor : null
  const [state, setState] = useState<Paged<P>>({ kind: 'loading' })
  const request = useRef<AbortController | null>(null)
  const fetchRef = useRef(fetcher)
  fetchRef.current = fetcher
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    setState({ kind: 'loading' })
    request.current = startLoad((signal) => fetchRef.current(query, LIST_PAGING_UI_ENABLED ? cursorQuery(cursor) : { offset }, signal), {
      onReady: (page) => setState({ kind: 'ready', page }),
      onFailure: setState,
      failureTitle,
    }, request.current)
    return () => request.current?.abort()
  }, [query, offset, cursor, attempt, failureTitle])
  return { state, setOffset: (next: number) => setPaging({ query, offset: next }), cursorPaging, retry: () => setAttempt((n) => n + 1) }
}

function Pager({ total, offset, shown, onOffset, testId }: { total: number; offset: number; shown: number; onOffset: (n: number) => void; testId: string }) {
  if (total <= PAGE_SIZE) return null
  return (
    <div className="mt-3 flex items-center justify-between text-xs text-muted-foreground">
      <span data-testid={testId}>
        {offset + 1}–{offset + shown} of {total}
      </span>
      <div className="flex gap-2">
        <Button variant="outline" size="sm" disabled={offset === 0} onClick={() => onOffset(offset > PAGE_SIZE ? offset - PAGE_SIZE : 0)}>
          <ChevronLeft className="h-3.5 w-3.5" />
          Previous
        </Button>
        <Button variant="outline" size="sm" disabled={offset + PAGE_SIZE >= total} onClick={() => onOffset(offset + PAGE_SIZE)}>
          Next
          <ChevronRight className="h-3.5 w-3.5" />
        </Button>
      </div>
    </div>
  )
}

/** The loading, refusal and failure states every catalogue shows the same way. */
function failureBody(state: LoadFailure, what: string, onRetry: () => void): ReactNode {
  if (state.kind === 'forbidden') return <Forbidden permission="domain:read" correlationId={state.cid} />
  if (state.kind === 'failed') return <LoadFailed icon={Landmark} title={`${what} could not be loaded`} cid={state.cid} onRetry={onRetry} />
  return <EmptyState icon={Landmark} title={`${what} could not be listed`} description={`The request was refused.${state.cid ? ` Correlation ID: ${state.cid}` : ''}`} />
}

const Label = ({ text, children }: { text: string; children: ReactNode }) => (
  <label className="flex flex-col gap-1 text-xs text-muted-foreground">
    {text}
    {children}
  </label>
)

/** A select of one vocabulary's codes, keeping a value given in the URL that is not in it. */
function VocabularySelect({
  label,
  any,
  entries,
  value,
  onChange,
  testId,
}: {
  label: string
  any: string
  entries: VocabularyEntry[]
  value: string
  onChange: (value: string) => void
  testId: string
}) {
  return (
    <select aria-label={label} className={selectClass} value={value} onChange={(e) => onChange(e.target.value)} data-testid={testId}>
      <option value="">{any}</option>
      {entries.map((e) => (
        <option key={e.code} value={e.code}>
          {e.code}
          {e.provisional ? ' (provisional)' : ''}
        </option>
      ))}
      {value && !entries.some((e) => e.code === value) && <option value={value}>{value}</option>}
    </select>
  )
}

function Counterparties({ vocab, sources, sourcesNote = null }: { vocab: VocabularyEntry[]; sources: SourceOption[] | null; sourcesNote?: Truncation | null }) {
  const { applied, draft, setDraft, apply, filtered } = useUrlFilters(COUNTERPARTY_KEYS, '')
  const { query, problems } = useMemo(() => counterpartyQuery(applied), [applied])
  const { state, setOffset, cursorPaging, retry } = usePaged(
    query,
    (q, at, signal): Promise<CounterpartyPage> =>
      call(() => api.GET('/api/v1/counterparties', { params: { query: { limit: PAGE_SIZE, ...at, ...q } }, signal })),
    'Could not load counterparties',
  )
  const submit = (e: FormEvent) => {
    e.preventDefault()
    apply(draft)
  }

  const filters = (
    <form onSubmit={submit} className="mb-3 flex flex-wrap items-end gap-2" data-testid="counterparty-filters">
      <Label text="Segment">
        <VocabularySelect
          label="Segment"
          any="All segments"
          entries={codesOf(vocab, 'credit_segment')}
          value={draft.segment}
          onChange={(segment) => apply({ ...draft, segment })}
          testId="cp-filter-segment"
        />
      </Label>
      <Label text="Country">
        <Input
          aria-label="Country"
          placeholder="e.g. DE"
          className="h-9 w-24 font-mono uppercase placeholder:normal-case"
          maxLength={2}
          value={draft.country}
          onChange={(e) => setDraft({ ...draft, country: e.target.value })}
          aria-invalid={problems.includes('country')}
          data-testid="cp-filter-country"
        />
      </Label>
      {sources && (
        <Label text="Source">
          <select
            aria-label="Source"
            className={selectClass}
            value={draft.source_id}
            onChange={(e) => apply({ ...draft, source_id: e.target.value })}
            data-testid="cp-filter-source"
          >
            <option value="">All sources</option>
            {sources.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name} ({s.code})
              </option>
            ))}
          </select>
          {/* Q18: the options are one capped page of sources; say so when the server cut it. */}
          <TruncationNote note={sourcesNote} testId="cp-filter-source-truncated" />
        </Label>
      )}
      <Label text="Import ID">
        <Input
          aria-label="Import ID"
          placeholder="Import UUID"
          className="h-9 w-80 font-mono"
          maxLength={36}
          value={draft.import_id}
          onChange={(e) => setDraft({ ...draft, import_id: e.target.value })}
          aria-invalid={problems.includes('import_id')}
          data-testid="cp-filter-import"
        />
      </Label>
      <Button type="submit" size="sm" variant="outline" data-testid="cp-filter-apply">
        Apply
      </Button>
      {filtered && (
        <Button type="button" size="sm" variant="ghost" onClick={() => apply(NO_COUNTERPARTY_FILTERS)} data-testid="cp-filter-clear">
          Clear
        </Button>
      )}
      {problems.length > 0 && (
        <p className="w-full text-xs text-destructive" data-testid="cp-filter-problems">
          Not applied: {problemText(problems)}.
        </p>
      )}
    </form>
  )

  let body: ReactNode
  if (state.kind === 'loading') body = <LoadingBlock rows={4} />
  else if (state.kind !== 'ready') body = cursorRefusedBody(state, cursorPaging, 'counterparty-pager') ?? failureBody(state, 'Counterparties', retry)
  else if (state.page.items.length === 0)
    body = (
      <EmptyState
        icon={Building2}
        title={filtered ? 'No counterparty matches these filters' : 'No counterparties yet'}
        description={
          filtered
            ? 'A filter matches a counterparty when any of its versions matches.'
            : 'A data steward projects a validated counterparty import from its import page ("Project to domain").'
        }
      />
    )
  else {
    const { page } = state
    body = (
      <>
        <Table data-testid="counterparty-table" data-total={page.total}>
          <TableHeader>
            <TableRow>
              <TableHead>Counterparty</TableHead>
              <TableHead>Name (latest)</TableHead>
              <TableHead>Segment (latest)</TableHead>
              <TableHead>Country (latest)</TableHead>
              <TableHead>Source</TableHead>
              <TableHead className="text-right">Versions</TableHead>
              <TableHead>Latest import</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {page.items.map((c) => (
              <TableRow key={c.counterparty_id} data-testid="counterparty-row" data-id={c.counterparty_id}>
                <TableCell>
                  <Link href={counterpartyHref(c.counterparty_id)} className="font-mono text-sm text-primary hover:underline" data-testid="counterparty-link">
                    {keyText(c.business_key, c.counterparty_id)}
                  </Link>
                  {isMasked(c.business_key) && (
                    <span className="ml-2">
                      <MaskedValue value={c.business_key} />
                    </span>
                  )}
                  <div className="font-mono text-xs text-muted-foreground">{c.counterparty_id}</div>
                </TableCell>
                <TableCell className="text-sm">
                  <CreditValueCell value={c.counterparty_name} testId="counterparty-name" className="font-sans" />
                </TableCell>
                <TableCell className="text-sm">
                  <CreditValueCell value={c.segment} />
                  {c.segment !== null && !isMasked(c.segment) && !c.segment_known && (
                    <Badge variant="outline" className="ml-2" data-testid="segment-unknown">
                      Not in vocabulary
                    </Badge>
                  )}
                </TableCell>
                <TableCell className="text-sm">
                  <CreditValueCell value={c.country} />
                </TableCell>
                <TableCell className="font-mono text-sm">{c.source_code}</TableCell>
                <TableCell className="text-right font-mono text-sm">
                  v{c.latest_version}
                  <div className="text-xs text-muted-foreground">
                    {c.version_count} version{c.version_count === 1 ? '' : 's'}
                  </div>
                </TableCell>
                <TableCell>
                  <Link href={importHref(c.latest_import_id)} className="font-mono text-xs text-primary hover:underline" data-testid="counterparty-latest-import">
                    {c.latest_import_id.slice(0, 8)}…
                  </Link>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        {LIST_PAGING_UI_ENABLED ? (
          <ListPager page={page} paging={cursorPaging} testId="counterparty-pager" />
        ) : (
          <Pager total={page.total} offset={page.offset} shown={page.items.length} onOffset={setOffset} testId="counterparty-range" />
        )}
      </>
    )
  }

  return (
    <PageSection
      icon={Building2}
      title={state.kind === 'ready' ? `${state.page.total} counterpart${state.page.total === 1 ? 'y' : 'ies'}` : 'Counterparties'}
      description="One row per counterparty (tenant, source and business key). 'Current' is not defined: a calculation pins versions through a dataset snapshot. Names are confidential and masked by your clearance."
    >
      {filters}
      {body}
    </PageSection>
  )
}

function Exposures({ vocab }: { vocab: VocabularyEntry[] }) {
  const { applied, draft, setDraft, apply, filtered } = useUrlFilters(EXPOSURE_KEYS, EXPOSURE_PARAM_PREFIX)
  const { query, problems } = useMemo(() => exposureQuery(applied), [applied])
  const { state, setOffset, cursorPaging, retry } = usePaged(
    query,
    (q, at, signal): Promise<ExposurePage> =>
      call(() => api.GET('/api/v1/credit-exposures', { params: { query: { limit: PAGE_SIZE, ...at, ...q } }, signal })),
    'Could not load credit exposures',
  )
  const submit = (e: FormEvent) => {
    e.preventDefault()
    apply(draft)
  }

  const filters = (
    <form onSubmit={submit} className="mb-3 flex flex-wrap items-end gap-2" data-testid="exposure-filters">
      <Label text="Currency">
        <Input
          aria-label="Currency"
          placeholder="e.g. EUR"
          className="h-9 w-24 font-mono uppercase placeholder:normal-case"
          maxLength={3}
          value={draft.currency}
          onChange={(e) => setDraft({ ...draft, currency: e.target.value })}
          aria-invalid={problems.includes('currency')}
          data-testid="exp-filter-currency"
        />
      </Label>
      <Label text="Facility type">
        <VocabularySelect
          label="Facility type"
          any="All facility types"
          entries={codesOf(vocab, 'facility_type')}
          value={draft.facility_type}
          onChange={(facility_type) => apply({ ...draft, facility_type })}
          testId="exp-filter-facility"
        />
      </Label>
      <Label text="Counterparty key">
        <Input
          aria-label="Counterparty key"
          placeholder="e.g. CP-0001"
          className="h-9 w-40 font-mono"
          maxLength={200}
          value={draft.counterparty_key}
          onChange={(e) => setDraft({ ...draft, counterparty_key: e.target.value })}
          data-testid="exp-filter-counterparty"
        />
      </Label>
      <Label text="Import ID">
        <Input
          aria-label="Exposure import ID"
          placeholder="Import UUID"
          className="h-9 w-80 font-mono"
          maxLength={36}
          value={draft.import_id}
          onChange={(e) => setDraft({ ...draft, import_id: e.target.value })}
          aria-invalid={problems.includes('import_id')}
          data-testid="exp-filter-import"
        />
      </Label>
      <Button type="submit" size="sm" variant="outline" data-testid="exp-filter-apply">
        Apply
      </Button>
      {filtered && (
        <Button type="button" size="sm" variant="ghost" onClick={() => apply(NO_EXPOSURE_FILTERS)} data-testid="exp-filter-clear">
          Clear
        </Button>
      )}
      {problems.length > 0 && (
        <p className="w-full text-xs text-destructive" data-testid="exp-filter-problems">
          Not applied: {problemText(problems)}.
        </p>
      )}
    </form>
  )

  let body: ReactNode
  if (state.kind === 'loading') body = <LoadingBlock rows={4} />
  else if (state.kind !== 'ready') body = cursorRefusedBody(state, cursorPaging, 'exposure-pager') ?? failureBody(state, 'Credit exposures', retry)
  else if (state.page.items.length === 0)
    body = (
      <EmptyState
        icon={Wallet}
        title={filtered ? 'No credit exposure matches these filters' : 'No credit exposures yet'}
        description={
          filtered
            ? 'A filter matches an exposure when any of its versions matches.'
            : 'A data steward projects a validated credit-exposure import from its import page ("Project to domain").'
        }
      />
    )
  else {
    const { page } = state
    body = (
      <>
        <Table data-testid="exposure-table" data-total={page.total}>
          <TableHeader>
            <TableRow>
              <TableHead>Exposure</TableHead>
              <TableHead>Counterparty key</TableHead>
              <TableHead>As of</TableHead>
              <TableHead>Facility</TableHead>
              <TableHead className="text-right">Drawn</TableHead>
              <TableHead>Currency</TableHead>
              <TableHead className="text-right">DPD</TableHead>
              <TableHead>Default</TableHead>
              <TableHead className="text-right">Versions</TableHead>
              <TableHead>Latest import</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {page.items.map((x) => (
              <TableRow key={x.credit_exposure_id} data-testid="exposure-row" data-id={x.credit_exposure_id}>
                <TableCell>
                  <Link href={exposureHref(x.credit_exposure_id)} className="font-mono text-sm text-primary hover:underline" data-testid="exposure-link">
                    {keyText(x.business_key, x.credit_exposure_id)}
                  </Link>
                  {isMasked(x.business_key) && (
                    <span className="ml-2">
                      <MaskedValue value={x.business_key} />
                    </span>
                  )}
                </TableCell>
                <TableCell className="text-sm">
                  <CreditValueCell value={x.counterparty_key} />
                </TableCell>
                <TableCell className="text-sm">
                  <CreditValueCell value={x.as_of_date} />
                </TableCell>
                <TableCell className="text-sm">
                  <CreditValueCell value={x.facility_type} />
                </TableCell>
                <TableCell className="text-right text-sm">
                  <CreditValueCell value={x.drawn_amount} testId="exposure-drawn" money />
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
                <TableCell className="text-right font-mono text-sm">
                  v{x.latest_version}
                  <div className="text-xs text-muted-foreground">
                    {x.version_count} version{x.version_count === 1 ? '' : 's'}
                  </div>
                </TableCell>
                <TableCell>
                  <Link href={importHref(x.latest_import_id)} className="font-mono text-xs text-primary hover:underline" data-testid="exposure-latest-import">
                    {x.latest_import_id.slice(0, 8)}…
                  </Link>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        {LIST_PAGING_UI_ENABLED ? (
          <ListPager page={page} paging={cursorPaging} testId="exposure-pager" />
        ) : (
          <Pager total={page.total} offset={page.offset} shown={page.items.length} onOffset={setOffset} testId="exposure-range" />
        )}
      </>
    )
  }

  return (
    <PageSection
      icon={Wallet}
      title={state.kind === 'ready' ? `${state.page.total} credit exposure${state.page.total === 1 ? '' : 's'}` : 'Credit exposures'}
      description="One row per exposure (tenant, source and business key), latest version shown. Amounts are exact decimal text in the exposure's currency; drawn, undrawn and collateral amounts are confidential and masked by your clearance. An exposure's counterparty is resolved within a dataset snapshot, not here."
    >
      {filters}
      {body}
    </PageSection>
  )
}

/**
 * FE-19.1: the Counterparties tab of the Financial Domain: the counterparty catalogue
 * (GET /counterparties), the credit-exposure catalogue (GET /credit-exposures), each filtered
 * by the server with its filters in the URL, and the seeded credit vocabularies.
 */
export function CounterpartyCatalogue() {
  const { has } = useCurrentSession()
  const canReadSources = has('source:read')
  const [vocab, setVocab] = useState<Vocab>({ kind: 'loading' })
  const [sources, setSources] = useState<SourceOption[] | null>(null)
  const [sourcesNote, setSourcesNote] = useState<Truncation | null>(null)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    const controller = startLoad(
      (signal) => call(() => api.GET('/api/v1/credit-vocabularies', { signal })),
      { onReady: (list) => setVocab({ kind: 'ready', items: list.items }), onFailure: setVocab, failureTitle: 'Could not load the credit vocabularies' },
    )
    return () => controller.abort()
  }, [attempt])

  // The source filter's options (source:read). Optional: without them the filter is hidden.
  useEffect(() => {
    if (!canReadSources) return
    const controller = new AbortController()
    call(() => api.GET('/api/v1/sources', { params: { query: { limit: 200 } }, signal: controller.signal })).then(
      (page) => {
        if (controller.signal.aborted) return
        setSources(page.items.map(({ id, code, name }) => ({ id, code, name })))
        setSourcesNote(truncationOf(page, 'sources'))
      },
      () => {
        if (!controller.signal.aborted) setSources(null)
      },
    )
    return () => controller.abort()
  }, [canReadSources])

  const items = vocab.kind === 'ready' ? vocab.items : []
  return (
    <div className="space-y-4">
      <Counterparties vocab={items} sources={canReadSources ? sources : null} sourcesNote={sourcesNote} />
      <Exposures vocab={items} />
      {vocab.kind === 'loading' ? (
        <LoadingBlock rows={2} />
      ) : vocab.kind === 'ready' ? (
        <CreditVocabularies items={vocab.items} />
      ) : (
        <PageSection icon={Landmark} title="Credit vocabularies">
          {failureBody(vocab, 'The credit vocabularies', () => {
            setVocab({ kind: 'loading' })
            setAttempt((n) => n + 1)
          })}
        </PageSection>
      )}
    </div>
  )
}
