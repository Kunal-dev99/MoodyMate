'use client'

import Link from 'next/link'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { BookOpen, ChevronLeft, ChevronRight, Landmark } from 'lucide-react'
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
import { ShownValue } from './ShownValue'
import {
  NO_FILTERS,
  catalogueQuery,
  instrumentHref,
  vocabularyEntry,
  type CatalogueFilters,
  type InstrumentPage,
  type InstrumentType,
} from './domain'
import { formatTimestamp } from '@/lib/format'

const PAGE_SIZE = 50
const selectClass = 'h-9 rounded-md border border-input bg-background px-2 text-sm'

type State = { kind: 'loading' } | { kind: 'ready'; page: InstrumentPage } | LoadFailure
type SourceOption = Pick<Schemas['SourceSummary'], 'id' | 'code' | 'name'>

/** The badge on a seeded PROVISIONAL vocabulary value (D-11, D-29 §5). */
export function ProvisionalBadge({ className }: { className?: string }) {
  return (
    <Badge variant="warning" className={className} data-testid="provisional-badge">
      Provisional / illustrative
    </Badge>
  )
}

function filtersFrom(params: URLSearchParams): CatalogueFilters {
  return {
    instrument_type: params.get('instrument_type') ?? '',
    currency: params.get('currency') ?? '',
    source_id: params.get('source_id') ?? '',
    import_id: params.get('import_id') ?? '',
  }
}

/** The seeded instrument type vocabulary, each value labelled provisional when it is. */
function Vocabulary({ types }: { types: InstrumentType[] }) {
  return (
    <PageSection
      icon={BookOpen}
      title="Instrument type vocabulary"
      description="Seeded by the platform until the client defines its own types. An instrument of another type is still projected, and modules warn on it."
    >
      {types.length === 0 ? (
        <p className="text-sm text-muted-foreground">No instrument types are seeded for your tenant.</p>
      ) : (
        <ul className="flex flex-wrap gap-2" data-testid="instrument-type-vocabulary">
          {types.map((t) => (
            <li
              key={t.code}
              className="flex items-center gap-2 rounded-md border border-border bg-surface-2 px-2 py-1 text-sm"
              data-testid="instrument-type"
              data-code={t.code}
              data-provisional={t.provisional}
            >
              <span className="font-mono">{t.code}</span>
              <span className="text-muted-foreground">{t.name}</span>
              {t.provisional && <ProvisionalBadge />}
            </li>
          ))}
        </ul>
      )}
    </PageSection>
  )
}

/**
 * FE-14.1: the instrument catalogue (GET /instruments), filtered by the server on type,
 * currency, source and import. Filters live in the URL, so the import page can link to the
 * instruments one import projected (`?import_id=`).
 */
export function InstrumentCatalogue() {
  const router = useRouter()
  const pathname = usePathname()
  const params = useSearchParams()
  const { has } = useCurrentSession()
  const applied = useMemo(() => filtersFrom(new URLSearchParams(params.toString())), [params])
  const [draft, setDraft] = useState<CatalogueFilters>(applied)
  const { query, problems } = useMemo(() => catalogueQuery(applied), [applied])
  // The page offset belongs to one filter set: new filters start at the first page.
  const [paging, setPaging] = useState<{ query: typeof query; offset: number }>({ query, offset: 0 })
  const offset = paging.query === query ? paging.offset : 0
  const setOffset = (next: number) => setPaging({ query, offset: next })
  // FE-33.1: the server's next_cursor, bound to these filters (NEXT_PUBLIC_LIST_PAGING_UI=off: offsets).
  const cursorPaging = useCursorPaging(JSON.stringify(query))
  const cursor = LIST_PAGING_UI_ENABLED ? cursorPaging.cursor : null
  const [state, setState] = useState<State>({ kind: 'loading' })
  const [sources, setSources] = useState<SourceOption[] | null>(null)
  const [sourcesNote, setSourcesNote] = useState<Truncation | null>(null)
  const request = useRef<AbortController | null>(null)
  const canReadSources = has('source:read')

  useEffect(() => {
    setDraft(applied)
  }, [applied])

  const load = useCallback(() => {
    // Another page or filter cancels the previous request; a late answer never lands.
    request.current = startLoad(
      (signal) =>
        call(() =>
          api.GET('/api/v1/instruments', {
            params: { query: { limit: PAGE_SIZE, ...(LIST_PAGING_UI_ENABLED ? cursorQuery(cursor) : { offset }), ...query } },
            signal,
          }),
        ),
      { onReady: (page) => setState({ kind: 'ready', page }), onFailure: setState, failureTitle: 'Could not load instruments' },
      request.current,
    )
  }, [query, offset, cursor])

  useEffect(() => {
    setState({ kind: 'loading' })
    load()
    return () => request.current?.abort()
  }, [load])

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

  const apply = (next: CatalogueFilters) => {
    const search = new URLSearchParams()
    for (const [key, value] of Object.entries(next)) if (value.trim()) search.set(key, value.trim())
    const qs = search.toString()
    router.replace(`${pathname}${qs ? `?${qs}` : ''}`, { scroll: false })
  }
  const submit = (e: FormEvent) => {
    e.preventDefault()
    apply(draft)
  }
  const filtered = Object.values(applied).some((v) => v.trim() !== '')
  const types = state.kind === 'ready' ? state.page.instrument_types : []

  const filters = (
    <form onSubmit={submit} className="mb-3 flex flex-wrap items-end gap-2" data-testid="catalogue-filters">
      <label className="flex flex-col gap-1 text-xs text-muted-foreground">
        Type
        <select
          aria-label="Instrument type"
          className={selectClass}
          value={draft.instrument_type}
          onChange={(e) => apply({ ...draft, instrument_type: e.target.value })}
          data-testid="filter-type"
        >
          <option value="">All types</option>
          {types.map((t) => (
            <option key={t.code} value={t.code}>
              {t.code}
              {t.provisional ? ' (provisional)' : ''}
            </option>
          ))}
          {draft.instrument_type && !types.some((t) => t.code === draft.instrument_type) && (
            <option value={draft.instrument_type}>{draft.instrument_type}</option>
          )}
        </select>
      </label>
      <label className="flex flex-col gap-1 text-xs text-muted-foreground">
        Currency
        <Input
          aria-label="Currency"
          placeholder="e.g. EUR"
          className="h-9 w-24 font-mono uppercase placeholder:normal-case"
          maxLength={3}
          value={draft.currency}
          onChange={(e) => setDraft({ ...draft, currency: e.target.value })}
          aria-invalid={problems.includes('currency')}
          data-testid="filter-currency"
        />
      </label>
      {canReadSources && sources && (
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          Source
          <select
            aria-label="Source"
            className={selectClass}
            value={draft.source_id}
            onChange={(e) => apply({ ...draft, source_id: e.target.value })}
            data-testid="filter-source"
          >
            <option value="">All sources</option>
            {sources.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name} ({s.code})
              </option>
            ))}
          </select>
          {/* Q18: the options are one capped page of sources; say so when the server cut it. */}
          <TruncationNote note={sourcesNote} testId="filter-source-truncated" />
        </label>
      )}
      <label className="flex flex-col gap-1 text-xs text-muted-foreground">
        Import ID
        <Input
          aria-label="Import ID"
          placeholder="Import UUID"
          className="h-9 w-80 font-mono"
          maxLength={36}
          value={draft.import_id}
          onChange={(e) => setDraft({ ...draft, import_id: e.target.value })}
          aria-invalid={problems.includes('import_id')}
          data-testid="filter-import"
        />
      </label>
      <Button type="submit" size="sm" variant="outline" data-testid="filter-apply">
        Apply
      </Button>
      {filtered && (
        <Button type="button" size="sm" variant="ghost" onClick={() => apply(NO_FILTERS)} data-testid="filter-clear">
          Clear
        </Button>
      )}
      {problems.length > 0 && (
        <p className="w-full text-xs text-destructive" data-testid="filter-problems">
          Not applied: {problems.map((p) => (p === 'currency' ? 'currency (three letters, ISO 4217)' : p === 'import_id' ? 'import ID (a UUID)' : 'source')).join(', ')}.
        </p>
      )}
    </form>
  )

  let body
  if (state.kind === 'loading') body = <LoadingBlock rows={4} />
  else if (state.kind === 'forbidden') body = <Forbidden permission="domain:read" correlationId={state.cid} />
  else if (state.kind === 'failed')
    body = (
      <LoadFailed
        icon={Landmark}
        title="Instruments could not be loaded"
        cid={state.cid}
        onRetry={() => {
          setState({ kind: 'loading' })
          load()
        }}
      />
    )
  else if (state.kind === 'not_found')
    body = cursorRefusedBody(state, cursorPaging, 'instrument-pager') ?? (
      <EmptyState
        icon={Landmark}
        title="Instruments could not be listed"
        description={`The request was refused.${state.cid ? ` Correlation ID: ${state.cid}` : ''}`}
      />
    )
  else {
    const { page } = state
    body =
      page.items.length === 0 ? (
        <EmptyState
          icon={Landmark}
          title={filtered ? 'No instrument matches these filters' : 'No instruments yet'}
          description={
            filtered
              ? 'A filter matches an instrument when any of its versions matches.'
              : 'A data steward projects a validated instrument import from its import page ("Project to domain").'
          }
        />
      ) : (
        <>
          <p className="mb-2 text-xs text-muted-foreground" data-testid="instrument-showing">
            Showing {page.offset + 1}–{page.offset + page.items.length} of {page.total}
          </p>
          <Table data-testid="instrument-table" data-total={page.total}>
            <TableHeader>
              <TableRow>
                <TableHead>Instrument</TableHead>
                <TableHead>Type (latest)</TableHead>
                <TableHead>Currency (latest)</TableHead>
                <TableHead>Source</TableHead>
                <TableHead className="text-right">Versions</TableHead>
                <TableHead>First seen</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {page.items.map((i) => {
                const typeCode = isMasked(i.instrument_type) ? null : i.instrument_type
                const vocab = vocabularyEntry(page.instrument_types, typeCode)
                return (
                  <TableRow key={i.instrument_id} data-testid="instrument-row" data-id={i.instrument_id}>
                    <TableCell>
                      <Link href={instrumentHref(i.instrument_id)} className="font-mono text-sm text-primary hover:underline" data-testid="instrument-link">
                        {isMasked(i.business_key) ? `${i.instrument_id.slice(0, 8)}…` : i.business_key}
                      </Link>
                      {isMasked(i.business_key) && (
                        <span className="ml-2">
                          <MaskedValue value={i.business_key} />
                        </span>
                      )}
                    </TableCell>
                    <TableCell className="text-sm">
                      <ShownValue value={i.instrument_type} />
                      {/* "Provisional / illustrative" is shown once, in the section header (P-6). */}
                      {typeCode !== null && !vocab && (
                        <Badge variant="outline" className="ml-2" data-testid="type-unknown">
                          Not in vocabulary
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell className="text-sm">
                      <ShownValue value={i.currency} />
                    </TableCell>
                    <TableCell className="font-mono text-sm">{i.source_code}</TableCell>
                    <TableCell className="text-right font-mono text-sm">
                      v{i.latest_version}
                      <div className="text-xs text-muted-foreground">
                        {i.version_count} version{i.version_count === 1 ? '' : 's'}
                      </div>
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">{formatTimestamp(i.created_at)}</TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
          {LIST_PAGING_UI_ENABLED && <ListPager page={page} paging={cursorPaging} testId="instrument-pager" />}
          {!LIST_PAGING_UI_ENABLED && page.total > PAGE_SIZE && (
            <div className="mt-3 flex items-center justify-between text-xs text-muted-foreground">
              <span data-testid="instrument-range">
                {page.offset + 1}–{page.offset + page.items.length} of {page.total}
              </span>
              <div className="flex gap-2">
                <Button variant="outline" size="sm" disabled={offset === 0} onClick={() => setOffset(offset > PAGE_SIZE ? offset - PAGE_SIZE : 0)}>
                  <ChevronLeft className="h-3.5 w-3.5" />
                  Previous
                </Button>
                <Button variant="outline" size="sm" disabled={offset + PAGE_SIZE >= page.total} onClick={() => setOffset(offset + PAGE_SIZE)}>
                  Next
                  <ChevronRight className="h-3.5 w-3.5" />
                </Button>
              </div>
            </div>
          )}
        </>
      )
  }

  // D-29 §3: 'current' is not defined; a calculation pins versions through a dataset snapshot.
  // P-6: one "Provisional / illustrative" pill for the page, when any listed type is provisional.
  const anyProvisional =
    state.kind === 'ready' &&
    state.page.items.some((i) => !isMasked(i.instrument_type) && vocabularyEntry(state.page.instrument_types, i.instrument_type)?.provisional)
  return (
    <div className="space-y-4">
      <PageSection
        icon={Landmark}
        title={state.kind === 'ready' ? `${state.page.total} instrument${state.page.total === 1 ? '' : 's'}` : 'Instruments'}
        description="One row per instrument (tenant, source and business key). 'Current' is not defined: a calculation pins versions through a dataset snapshot. Values are masked by classification."
        headerRight={anyProvisional ? <ProvisionalBadge /> : undefined}
      >
        {filters}
        {body}
      </PageSection>
      {state.kind === 'ready' && <Vocabulary types={state.page.instrument_types} />}
    </div>
  )
}
