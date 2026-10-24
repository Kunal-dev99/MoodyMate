import type { ReactNode } from 'react'
import { NavPermissionGate } from '@/components/auth/NavPermissionGate'
import { SessionProvider } from '@/components/auth/SessionProvider'
import { AppShell } from '@/components/layout/AppShell'

export default function PlatformLayout({ children }: { children: ReactNode }) {
  return (
    <SessionProvider>
      <AppShell>
        {/* FE-35.5: every route below is gated by its nav entry's permission (cosmetic; D-04). */}
        <NavPermissionGate>{children}</NavPermissionGate>
      </AppShell>
    </SessionProvider>
  )
}
