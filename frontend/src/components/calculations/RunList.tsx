'use client'

import Link from 'next/link'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Calculator, ChevronLeft, ChevronRight } from 'lucide-react'
import { api, call } from '@/api/client'
import { Forbidden } from '@/components/auth/Forbidden'
import { EmptyState } from '@/components/common/EmptyState'
import { LoadFailed } from '@/components/common/LoadFailed'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { PageSection } from '@/components/common/PageSection'
import { startLoad, type LoadFailure } from '@/components/common/load'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { RunStatusPill } from './RunStatusPill'
import { StressedRunBadge } from './StressedRunBadge'
import { ValidationStatusBadge } from './ValidationStatusBadge'
import { NO_RUN_FILTERS, RUN_STATUSES, RUN_STATUS_STYLE, fetchModules, runHref, runQuery, type RunFilters, type RunPage } from './calculations'
import { formatTimestamp } from '@/lib/format'
import { ActorLabel } from '@/components/common/ActorLabel'

const PAGE_SIZE = 50
const selectClass = 'h-9 rounded-md border border-input bg-background px-2 text-sm'

type State = { kind: 'loading' } | { kind: 'ready'; page: RunPage } | LoadFailure

function filtersFrom(params: URLSearchParams): RunFilters {
  return { module_code: params.get('module_code') ?? '', status: params.get('status') ?? '' }
}

const Short = ({ id, title }: { id: string; title: string }) => (
  <span className="font-mono text-xs" title={`${title}: ${id}`}>
    {id.slice(0, 8)}…
  </span>
)

/**
 * FE-15.1: the tenant's calculation runs (GET /calculation-runs, calc:read), newest first,
 * filtered by the server on module and status. Each row carries the run ID, the reporting
 * date and the pinned versions; the status is the server's.
 */
export function RunList() {
  const router = useRouter()
  const pathname = usePathname()
  const params = useSearchParams()
  const applied = useMemo(() => filtersFrom(new URLSearchParams(params.toString())), [params])
  const query = useMemo(() => runQuery(applied), [applied])
  // The page offset belongs to one filter set: new filters start at the first page.
  const [paging, setPaging] = useState<{ query: typeof query; offset: number }>({ query, offset: 0 })
  const offset = paging.query === query ? paging.offset : 0
  const setOffset = (next: number) => setPaging({ query, offset: next })
  const [state, setState] = useState<State>({ kind: 'loading' })
  const [moduleCodes, setModuleCodes] = useState<string[] | null>(null)
  const request = useRef<AbortController | null>(null)

  const load = useCallback(() => {
    request.current = startLoad(
      (signal) => call(() => api.GET('/api/v1/calculation-runs', { params: { query: { limit: PAGE_SIZE, offset, ...query } }, signal })),
      { onReady: (page) => setState({ kind: 'ready', page }), onFailure: setState, failureTitle: 'Could not load runs' },
      request.current,
    )
  }, [query, offset])

  useEffect(() => {
    setState({ kind: 'loading' })
    load()
    return () => request.current?.abort()
  }, [load])

  // The module filter's options (rule:read). Optional: without them the filter is a text box.
  useEffect(() => {
    const controller = new AbortController()
    fetchModules(controller.signal).then(
      (page) => {
        if (!controller.signal.aborted) setModuleCodes([...new Set(page.items.map((m) => m.code))])
      },
      () => {
        if (!controller.signal.aborted) setModuleCodes(null)
      },
    )
    return () => controller.abort()
  }, [])

  const apply = (next: RunFilters) => {
    const search = new URLSearchParams()
    for (const [key, value] of Object.entries(next)) if (value.trim()) search.set(key, value.trim())
    const qs = search.toString()
    router.replace(`${pathname}${qs ? `?${qs}` : ''}`, { scroll: false })
  }
  const filtered = Object.values(applied).some((v) => v.trim() !== '')

  const filters = (
    <div className="mb-3 flex flex-wrap items-end gap-2" data-testid="run-filters">
      <label className="flex flex-col gap-1 text-xs text-muted-foreground">
        Module
        <select
          aria-label="Module"
          className={selectClass}
          value={applied.module_code}
          onChange={(e) => apply({ ...applied, module_code: e.target.value })}
          data-testid="run-filter-module"
        >
          <option value="">All modules</option>
          {(moduleCodes ?? []).map((code) => (
            <option key={code} value={code}>
              {code}
            </option>
          ))}
          {applied.module_code && !(moduleCodes ?? []).includes(applied.module_code) && <option value={applied.module_code}>{applied.module_code}</option>}
        </select>
      </label>
      <label className="flex flex-col gap-1 text-xs text-muted-foreground">
        Status
        <select
          aria-label="Status"
          className={selectClass}
          value={applied.status}
          onChange={(e) => apply({ ...applied, status: e.target.value })}
          data-testid="run-filter-status"
        >
          <option value="">All statuses</option>
          {RUN_STATUSES.map((s) => (
            <option key={s} value={s}>
              {RUN_STATUS_STYLE[s].label}
            </option>
          ))}
        </select>
      </label>
      {filtered && (
        <Button type="button" size="sm" variant="ghost" onClick={() => apply(NO_RUN_FILTERS)} data-testid="run-filter-clear">
          Clear
        </Button>
      )}
    </div>
  )

  let body
  if (state.kind === 'loading') body = <LoadingBlock rows={4} />
  else if (state.kind === 'forbidden') body = <Forbidden permission="calc:read" correlationId={state.cid} />
  else if (state.kind === 'failed')
    body = (
      <LoadFailed
        icon={Calculator}
        title="Runs could not be loaded"
        cid={state.cid}
        onRetry={() => {
          setState({ kind: 'loading' })
          load()
        }}
      />
    )
  else if (state.kind === 'not_found')
    body = (
      <EmptyState
        icon={Calculator}
        title="Runs could not be listed"
        description={`The request was refused.${state.cid ? ` Correlation ID: ${state.cid}` : ''}`}
      />
    )
  else {
    const { page } = state
    body =
      page.items.length === 0 ? (
        <EmptyState
          icon={Calculator}
          title={filtered ? 'No run matches these filters' : 'No runs yet'}
          description={
            filtered ? 'Runs of any version of the module are listed.' : 'A risk analyst pins a dataset snapshot and submits a run ("New run").'
          }
        />
      ) : (
        <>
          <Table data-testid="run-table" data-total={page.total}>
            <TableHeader>
              <TableRow>
                <TableHead>Run</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Module</TableHead>
                <TableHead>Reporting date</TableHead>
                <TableHead>Pinned configuration</TableHead>
                <TableHead className="text-right">Warnings</TableHead>
                <TableHead>Started</TableHead>
                <TableHead>By</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {page.items.map((r) => (
                <TableRow key={r.run_id} data-testid="run-row" data-run-id={r.run_id} data-status={r.status}>
                  <TableCell>
                    <Link href={runHref(r.run_id)} className="font-mono text-xs text-primary hover:underline" data-testid="run-link">
                      {r.run_id}
                    </Link>
                    {r.illustrative && (
                      <div>
                        <Badge variant="warning" className="mt-1" data-testid="run-illustrative">
                          Illustrative · not regulatory
                        </Badge>
                      </div>
                    )}
                    {/* FE-30.1: the module version's validation status (D-56), as the server reports it. */}
                    <div>
                      <ValidationStatusBadge status={r.validation_status} className="mt-1" testId="run-validation-status" />
                    </div>
                    {/* FE-25.1: a stressed run's approval label and its baseline and scenario; nothing for other runs. */}
                    <StressedRunBadge scenario={r.scenario} compact className="mt-1" />
                  </TableCell>
                  <TableCell>
                    <RunStatusPill status={r.status} />
                  </TableCell>
                  <TableCell className="font-mono text-sm">
                    {r.module.code} v{r.module.version}
                  </TableCell>
                  <TableCell className="font-mono text-sm" data-testid="run-reporting-date">
                    {r.reporting_date}
                  </TableCell>
                  <TableCell className="space-y-0.5 text-xs">
                    <div>
                      <span className="text-muted-foreground">engine </span>
                      <span className="font-mono">{r.engine_version}</span>
                    </div>
                    <div>
                      <span className="text-muted-foreground">rule set </span>
                      <Short id={r.rule_set_id} title="Rule set" />
                    </div>
                    <div>
                      <span className="text-muted-foreground">assumptions </span>
                      <Short id={r.assumption_set_id} title="Assumption set" />
                    </div>
                    <div>
                      <span className="text-muted-foreground">snapshot </span>
                      <Short id={r.dataset_snapshot_id} title="Dataset snapshot" />
                    </div>
                  </TableCell>
                  <TableCell className="text-right font-mono text-sm">{r.warning_count}</TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {formatTimestamp(r.started_at)}
                    {r.ended_at && <div>ended {formatTimestamp(r.ended_at)}</div>}
                  </TableCell>
                  <TableCell className="text-xs">
                    <ActorLabel actor={r.created_by} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {page.total > PAGE_SIZE && (
            <div className="mt-3 flex items-center justify-between text-xs text-muted-foreground">
              <span data-testid="run-range">
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

  return (
    <PageSection
      icon={Calculator}
      title={state.kind === 'ready' ? `${state.page.total} run${state.page.total === 1 ? '' : 's'}` : 'Runs'}
      description="Newest first. A run is immutable: it ends once, in the status the server sets, and is never updated or deleted."
    >
      {filters}
      {body}
    </PageSection>
  )
}
