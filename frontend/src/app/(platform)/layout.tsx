import type { ReactNode } from 'react'
import { SessionProvider } from '@/components/auth/SessionProvider'
import { AppShell } from '@/components/layout/AppShell'

export default function PlatformLayout({ children }: { children: ReactNode }) {
  return (
    <SessionProvider>
      <AppShell>{children}</AppShell>
    </SessionProvider>
  )
}
