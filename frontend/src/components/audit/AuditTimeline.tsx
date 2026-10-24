'use client'

import { useCallback, useEffect, useState } from 'react'
import { ChevronLeft, ChevronRight, ScrollText, Search } from 'lucide-react'
import { api, call, ApiError, type Schemas } from '@/api/client'
import { Forbidden } from '@/components/auth/Forbidden'
import { EmptyState } from '@/components/common/EmptyState'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { MaskedValue, isMasked } from '@/components/common/MaskedValue'
import { toastApiError } from '@/components/common/api-error-toast'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { LIST_PAGING_UI_ENABLED } from '@/config/features'
import { pageSummary } from '@/lib/paging'
import { cursorsFor, keysetNext, pageView, type Paging } from './paging'
import { formatTimestamp } from '@/lib/format'
import { ActorLabel } from '@/components/common/ActorLabel'

type Page = Schemas['AuditEventPage']
type Event = Schemas['AuditEventOut']

const PAGE_SIZE = 20

interface Filters {
  action: string
  actor: string
  outcome: '' | 'SUCCESS' | 'DENIED' | 'FAILURE'
}

function DetailValue({ value }: { value: unknown }) {
  if (isMasked(value)) return <MaskedValue value={value} />
  if (value === null || value === undefined) return <span className="text-muted-foreground">—</span>
  if (Array.isArray(value))
    return (
      <span className="inline-flex flex-wrap gap-1">
        {value.map((v, i) => (
          <DetailValue key={i} value={v} />
        ))}
      </span>
    )
  if (typeof value === 'object')
    // Walk nested objects so a masked marker inside one renders as MaskedValue, not JSON.
    return (
      <span className="inline-flex flex-wrap gap-x-3 gap-y-1">
        {Object.entries(value).map(([k, v]) => (
          <span key={k} className="inline-flex items-center gap-1">
            <span className="text-xs text-muted-foreground">{k}</span>
            <DetailValue value={v} />
          </span>
        ))}
      </span>
    )
  return <span className="font-mono text-xs">{String(value)}</span>
}

function EventRow({ event }: { event: Event }) {
  const details = Object.entries(event.details)
  return (
    <li className="border-b border-border py-3 last:border-0" data-testid="audit-event">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={event.outcome === 'SUCCESS' ? 'success' : event.outcome === 'DENIED' ? 'destructive' : 'warning'}>
          {event.outcome}
        </Badge>
        <span className="text-sm font-medium">{event.action}</span>
        <span className="text-xs text-muted-foreground">
          on {event.resource_type}
          {event.resource_id ? ` ${event.resource_id}` : ''}
        </span>
        <span className="ml-auto text-xs text-muted-foreground">
          {formatTimestamp(event.occurred_at)}
        </span>
      </div>
      <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <span>
          actor <ActorLabel actor={event.actor} className="text-foreground" />
        </span>
        <span>
          correlation <span className="font-mono text-foreground" data-testid="correlation-id">{event.correlation_id ?? '—'}</span>
        </span>
        {event.approval_ref && (
          <span>
            approval <span className="font-mono text-foreground">{event.approval_ref}</span>
          </span>
        )}
      </div>
      {details.length > 0 && (
        <dl className="mt-2 grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1">
          {details.map(([key, value]) => (
            <div key={key} className="contents">
              <dt className="text-xs text-muted-foreground">{key}</dt>
              <dd>
                <DetailValue value={value} />
              </dd>
            </div>
          ))}
        </dl>
      )}
    </li>
  )
}

/**
 * FE-33.1 (Q17): "Showing n of total" under the timeline, and its keyset controls. Older is
 * offered only while the page's `has_more` is true and sends `before` (the last shown event's id);
 * Newer goes back one page. Shown once the timeline spans more than one page.
 */
export function KeysetPager({
  page,
  depth,
  loading,
  onNewer,
  onOlder,
}: {
  page: { items: readonly Event[]; total: number; index: number; hasOlder: boolean }
  depth: number
  loading: boolean
  onNewer: () => void
  onOlder: (before: string) => void
}) {
  const next = keysetNext(page.items, page.hasOlder)
  const summary = pageSummary({ items: page.items, total: page.total, has_more: next.has_more, next_cursor: null }, page.index * PAGE_SIZE)
  return (
    <div className="flex items-center justify-between text-xs text-muted-foreground" data-testid="audit-pager" data-has-more={next.has_more}>
      <span data-testid="audit-pager-summary">{summary.text}</span>
      {(next.has_more || depth > 0) && (
        <div className="flex gap-2">
          <Button variant="outline" size="sm" disabled={depth === 0 || loading} onClick={onNewer} data-testid="audit-pager-newer">
            <ChevronLeft className="h-3.5 w-3.5" />
            Newer
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={!next.has_more || !next.before || loading}
            onClick={() => next.before && onOlder(next.before)}
            data-testid="audit-pager-older"
            data-before={next.before ?? undefined}
          >
            Older
            <ChevronRight className="h-3.5 w-3.5" />
          </Button>
        </div>
      )}
    </div>
  )
}

/**
 * Audit timeline (FE-3.1). Pass `resourceType`/`resourceId` to scope it to one resource
 * (e.g. a schema in Phase 4); otherwise it shows the tenant's whole trail with filters.
 * Server-side permission (`audit:read`) and masking decide what is shown.
 */
export function AuditTimeline({
  resourceType,
  resourceId,
  showFilters = true,
}: {
  resourceType?: string
  resourceId?: string
  showFilters?: boolean
}) {
  const [filters, setFilters] = useState<Filters>({ action: '', actor: '', outcome: '' })
  const [applied, setApplied] = useState<Filters>(filters)
  // Keyset paging (`before` = last id of the previous page): stable even though every
  // read appends an audit.read event at the top. Each entry is the cursor of one page.
  // Cursors are tied to the scope (resource + applied filters): navigating to another
  // resource or changing filters starts again at the newest page.
  const scope = JSON.stringify([resourceType ?? '', resourceId ?? '', applied])
  const [paging, setPaging] = useState<Paging>({ scope, cursors: [] })
  const cursors = cursorsFor(paging, scope)
  const before = cursors.at(-1)
  // The page on screen and its position, set together so the range label never runs ahead.
  const [page, setPage] = useState<(Page & { index: number; hasOlder: boolean }) | null>(null)
  const [forbidden, setForbidden] = useState<string | null | false>(false)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const result = await call(() =>
        api.GET('/api/v1/audit-events', {
          params: {
            query: {
              limit: PAGE_SIZE + 1, // one extra row says whether an older page exists
              before,
              resource_type: resourceType,
              resource_id: resourceId,
              action: applied.action || undefined,
              actor: applied.actor || undefined,
              outcome: applied.outcome || undefined,
            },
          },
        }),
      )
      const { shown, hasOlder } = pageView(result.items, PAGE_SIZE)
      setPage({ ...result, items: shown, index: cursors.length, hasOlder })
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) setForbidden(err.correlationId)
      else toastApiError(err, 'Could not load the audit trail')
    } finally {
      setLoading(false)
    }
  }, [applied, before, cursors.length, resourceId, resourceType])

  useEffect(() => {
    void load()
  }, [load])

  if (forbidden !== false) return <Forbidden permission="audit:read" correlationId={forbidden} />

  const apply = () => {
    setPaging({ scope: '', cursors: [] }) // Apply always returns to the newest page
    setApplied(filters)
  }

  return (
    <div className="space-y-3">
      {showFilters && (
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            apply()
          }}
        >
          <Input
            className="w-48"
            placeholder="Action (e.g. user.list)"
            value={filters.action}
            onChange={(e) => setFilters({ ...filters, action: e.target.value })}
            aria-label="Filter by action"
          />
          <Input
            className="w-64"
            placeholder="Actor (e.g. user:…)"
            value={filters.actor}
            onChange={(e) => setFilters({ ...filters, actor: e.target.value })}
            aria-label="Filter by actor"
          />
          <select
            className="h-9 rounded-md border border-input bg-background px-2 text-sm"
            value={filters.outcome}
            onChange={(e) => setFilters({ ...filters, outcome: e.target.value as Filters['outcome'] })}
            aria-label="Filter by outcome"
          >
            <option value="">All outcomes</option>
            <option value="SUCCESS">Success</option>
            <option value="DENIED">Denied</option>
            <option value="FAILURE">Failure</option>
          </select>
          <Button type="submit" size="sm">
            <Search className="mr-2 h-3.5 w-3.5" />
            Apply
          </Button>
        </form>
      )}

      {loading && !page ? (
        <LoadingBlock rows={6} />
      ) : page && page.items.length === 0 ? (
        <EmptyState icon={ScrollText} title="No audit events" description="Nothing matches these filters yet." />
      ) : page ? (
        <>
          <ul aria-busy={loading}>
            {page.items.map((e) => (
              <EventRow key={e.id} event={e} />
            ))}
          </ul>
          {LIST_PAGING_UI_ENABLED ? (
            <KeysetPager page={page} depth={cursors.length} loading={loading} onNewer={() => setPaging({ scope, cursors: cursors.slice(0, -1) })} onOlder={(before) => setPaging({ scope, cursors: [...cursors, before] })} />
          ) : (
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span>
                {page.index * PAGE_SIZE + 1}–{page.index * PAGE_SIZE + page.items.length} of {page.total}
              </span>
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={cursors.length === 0 || loading}
                  onClick={() => setPaging({ scope, cursors: cursors.slice(0, -1) })}
                >
                  <ChevronLeft className="h-3.5 w-3.5" />
                  Newer
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={!page.hasOlder || loading}
                  onClick={() => setPaging({ scope, cursors: [...cursors, page.items[page.items.length - 1].id] })}
                >
                  Older
                  <ChevronRight className="h-3.5 w-3.5" />
                </Button>
              </div>
            </div>
          )}
        </>
      ) : null}
    </div>
  )
}
