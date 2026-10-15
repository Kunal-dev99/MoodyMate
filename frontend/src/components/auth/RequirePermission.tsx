'use client'

import type { ReactNode } from 'react'
import { useCurrentSession, type Permission } from '@/components/auth/SessionProvider'
import { Forbidden } from '@/components/auth/Forbidden'

/**
 * Renders children only when the signed-in user holds `permission`; otherwise the
 * 403 state (or `fallback`, e.g. `null` to hide an action). Cosmetic only: the backend
 * enforces the same permission on every request (D-04).
 */
export function RequirePermission({
  permission,
  children,
  fallback,
}: {
  permission: Permission
  children: ReactNode
  fallback?: ReactNode
}) {
  const { has } = useCurrentSession()
  if (has(permission)) return <>{children}</>
  return fallback === undefined ? <Forbidden permission={permission} /> : <>{fallback}</>
}
