'use client'

import type { ReactNode } from 'react'
import { usePathname } from 'next/navigation'
import { Forbidden } from '@/components/auth/Forbidden'
import { useCurrentSession } from '@/components/auth/SessionProvider'
import { PageHeader } from '@/components/common/PageHeader'
import { ROUTE_GATES_UI_ENABLED } from '@/config/features'
import { navEntryFor } from '@/config/navigation'

/**
 * FE-35.5: every (platform) route is gated by the permission of its nav entry (`navEntryFor`),
 * the one that hides the entry in the sidebar, /advanced/* included. Without it the route shows
 * the 403 state instead of its page; the page's own, finer gates (e.g. `calc:run` on New run)
 * apply below this one. Rendered once, by the (platform) layout, so a route added later is gated
 * too. Cosmetic only: the API enforces the same permission on every request (D-04).
 * NEXT_PUBLIC_ROUTE_GATES_UI=off renders the page as before (D-02).
 */
export function NavPermissionGate({ children }: { children: ReactNode }) {
  const pathname = usePathname()
  const { has } = useCurrentSession()
  if (!ROUTE_GATES_UI_ENABLED) return <>{children}</>
  const entry = navEntryFor(pathname ?? '')
  if (!entry?.permission || has(entry.permission)) return <>{children}</>
  return (
    <>
      <PageHeader title={entry.label} />
      <div className="px-6 pb-6" data-testid="route-gate" data-permission={entry.permission}>
        <Forbidden permission={entry.permission} />
      </div>
    </>
  )
}
