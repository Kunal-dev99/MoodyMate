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
  if (typeof value === 'object') return <code className="font-mono text-xs">{JSON.stringify(value)}</code>
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
          {new Date(event.occurred_at).toLocaleString()}
        </span>
      </div>
      <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <span>
          actor <span className="font-mono text-foreground">{event.actor}</span>
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
  const [offset, setOffset] = useState(0)
  const [page, setPage] = useState<Page | null>(null)
  const [forbidden, setForbidden] = useState<string | null | false>(false)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const result = await call(() =>
        api.GET('/api/v1/audit-events', {
          params: {
            query: {
              limit: PAGE_SIZE,
              offset,
              resource_type: resourceType,
              resource_id: resourceId,
              action: applied.action || undefined,
              actor: applied.actor || undefined,
              outcome: applied.outcome || undefined,
            },
          },
        }),
      )
      setPage(result)
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) setForbidden(err.correlationId)
      else toastApiError(err, 'Could not load the audit trail')
    } finally {
      setLoading(false)
    }
  }, [applied, offset, resourceId, resourceType])

  useEffect(() => {
    void load()
  }, [load])

  if (forbidden !== false) return <Forbidden permission="audit:read" correlationId={forbidden} />

  const apply = () => {
    setOffset(0)
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
          <div className="flex items-center justify-between text-xs text-muted-foreground">
            <span>
              {page.offset + 1}–{page.offset + page.items.length} of {page.total}
            </span>
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={offset === 0 || loading}
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              >
                <ChevronLeft className="h-3.5 w-3.5" />
                Newer
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={offset + PAGE_SIZE >= page.total || loading}
                onClick={() => setOffset(offset + PAGE_SIZE)}
              >
                Older
                <ChevronRight className="h-3.5 w-3.5" />
              </Button>
            </div>
          </div>
        </>
      ) : null}
    </div>
  )
}
