'use client'

import { useEffect, useState } from 'react'
import { Users } from 'lucide-react'
import { api, call, ApiError, type Schemas } from '@/api/client'
import { Forbidden } from '@/components/auth/Forbidden'
import { useCurrentSession } from '@/components/auth/SessionProvider'
import { PageHeader } from '@/components/common/PageHeader'
import { PageSection } from '@/components/common/PageSection'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { toastApiError } from '@/components/common/api-error-toast'
import { Badge } from '@/components/ui/badge'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'

type UserPage = Schemas['UserPage']
type State = { kind: 'loading' } | { kind: 'ready'; page: UserPage } | { kind: 'forbidden'; cid: string | null }

/**
 * Read-only users list (admin:read). The request is always sent: the backend decides.
 * A 403 from the server renders the 403 state even if the client thought it was allowed.
 */
export default function UsersPage() {
  const { has } = useCurrentSession()
  const [state, setState] = useState<State>({ kind: 'loading' })

  useEffect(() => {
    call(() => api.GET('/api/v1/users', { params: { query: { limit: 100 } } }))
      .then((page) => setState({ kind: 'ready', page }))
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 403) {
          setState({ kind: 'forbidden', cid: err.correlationId })
        } else {
          toastApiError(err, 'Could not load users')
        }
      })
  }, [])

  return (
    <>
      <PageHeader title="Users" description="Users in your tenant and their roles. Read-only." />
      <div className="px-6 pb-6">
        {state.kind === 'forbidden' || (!has('admin:read') && state.kind !== 'ready') ? (
          <Forbidden permission="admin:read" correlationId={state.kind === 'forbidden' ? state.cid : null} />
        ) : state.kind === 'loading' ? (
          <LoadingBlock rows={5} />
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
                    <TableCell className="text-muted-foreground">{u.email ?? '—'}</TableCell>
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
          </PageSection>
        )}
      </div>
    </>
  )
}
