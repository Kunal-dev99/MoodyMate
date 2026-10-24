'use client'

import { useEffect, useState } from 'react'
import { Users } from 'lucide-react'
import { api, call, ApiError, type Schemas } from '@/api/client'
import { Forbidden } from '@/components/auth/Forbidden'
import { useCurrentSession } from '@/components/auth/SessionProvider'
import { PageHeader } from '@/components/common/PageHeader'
import { PageSection } from '@/components/common/PageSection'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { MaskedValue, isMasked } from '@/components/common/MaskedValue'
import { toastApiError } from '@/components/common/api-error-toast'
import { CursorRefused, ListPager, useCursorPaging } from '@/components/common/ListPager'
import { LIST_PAGING_UI_ENABLED } from '@/config/features'
import { cursorQuery, isInvalidCursor } from '@/lib/paging'
import { Badge } from '@/components/ui/badge'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'

type UserPage = Schemas['UserPage']
type State =
  | { kind: 'loading' }
  | { kind: 'ready'; page: UserPage }
  | { kind: 'forbidden'; cid: string | null }
  /** Q19: the server refused the page's cursor (400 invalid_cursor). */
  | { kind: 'cursor_refused'; cid: string | null }

/**
 * Read-only users list (admin:read). Without admin:read the request is not sent at all (the
 * 403 state shows straight away); with it, the backend still decides, and a 403 from the server
 * renders the 403 state even if the client thought it was allowed (D-04).
 */
export default function UsersPage() {
  const { has } = useCurrentSession()
  const [state, setState] = useState<State>({ kind: 'loading' })

  const mayRead = has('admin:read')
  // FE-33.1: the server's next_cursor (NEXT_PUBLIC_LIST_PAGING_UI=off: the first 100 only).
  const cursorPaging = useCursorPaging('users')
  const cursor = LIST_PAGING_UI_ENABLED ? cursorPaging.cursor : null

  useEffect(() => {
    if (!mayRead) return
    call(() => api.GET('/api/v1/users', { params: { query: { limit: 100, ...cursorQuery(cursor) } } }))
      .then((page) => setState({ kind: 'ready', page }))
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 403) {
          setState({ kind: 'forbidden', cid: err.correlationId })
        } else if (LIST_PAGING_UI_ENABLED && isInvalidCursor(err)) {
          setState({ kind: 'cursor_refused', cid: err instanceof ApiError ? err.correlationId : null })
        } else {
          toastApiError(err, 'Could not load users')
        }
      })
  }, [mayRead, cursor])

  return (
    <>
      <PageHeader title="Users" description="Users in your tenant and their roles. Read-only." />
      <div className="px-6 pb-6">
        {state.kind === 'forbidden' || (!has('admin:read') && state.kind !== 'ready') ? (
          <Forbidden permission="admin:read" correlationId={state.kind === 'forbidden' ? state.cid : null} />
        ) : state.kind === 'loading' ? (
          <LoadingBlock rows={5} />
        ) : state.kind === 'cursor_refused' ? (
          <CursorRefused paging={cursorPaging} cid={state.cid} testId="user-pager" />
        ) : (
          <PageSection icon={Users} title={`${state.page.total} user${state.page.total === 1 ? '' : 's'}`}>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Email</TableHead>
                  <TableHead>Roles</TableHead>
                  <TableHead>Status</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {state.page.items.map((u) => (
                  <TableRow key={u.id}>
                    <TableCell className="font-medium">{u.display_name}</TableCell>
                    <TableCell className="text-muted-foreground">
                      {isMasked(u.email) ? <MaskedValue value={u.email} /> : (u.email ?? '—')}
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-1">
                        {u.roles.map((r) => (
                          <Badge key={r} variant="secondary" className="font-mono">
                            {r}
                          </Badge>
                        ))}
                      </div>
                    </TableCell>
                    <TableCell>
                      <Badge variant={u.status === 'ACTIVE' ? 'success' : 'secondary'}>{u.status}</Badge>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            {LIST_PAGING_UI_ENABLED && <ListPager page={state.page} paging={cursorPaging} testId="user-pager" />}
          </PageSection>
        )}
      </div>
    </>
  )
}
