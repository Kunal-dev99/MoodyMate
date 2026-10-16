'use client'

import type { ReactNode } from 'react'
import { useCurrentSession } from '@/components/auth/SessionProvider'
import { EnvironmentBanner } from '@/components/layout/EnvironmentBanner'
import { Header } from '@/components/layout/header'
import { Sidebar, type NavItem } from '@/components/layout/sidebar'
import { ThemeToggle } from '@/components/layout/ThemeToggle'
import { adminWorkspaces, mainWorkspaces, type Workspace } from '@/config/navigation'

const toNav = ({ href, label, icon }: Workspace): NavItem => ({ href, label, icon })

/**
 * Authenticated app shell (FE-1.2), wired per the design bundle's "App shell" example.
 * Nav items the user lacks the permission for are hidden (FE-2.2). That is cosmetic:
 * the pages show a 403 state and the backend enforces the permission (D-04).
 */
export function AppShell({ children }: { children: ReactNode }) {
  const { me, has, signOut, authConfig } = useCurrentSession()
  const visible = (w: Workspace) => !w.permission || has(w.permission)

  return (
    <>
      <Sidebar
        mainNav={mainWorkspaces.filter(visible).map(toNav)}
        adminNav={adminWorkspaces.filter(visible).map(toNav)}
        brandName="MoodyMate"
        brandTagline="Financial Risk Platform"
        brandShort="MM"
        brandHref="/dashboard"
        user={{ name: me.display_name, email: me.email ?? undefined }}
        onLogout={() => void signOut()}
      />
      <div className="ml-64 transition-all duration-300">
        {/* One sticky block, so the banner never covers the header when the page scrolls. */}
        <div className="sticky top-0 z-40">
          <EnvironmentBanner environment={authConfig.environment} />
          <Header
            title="Financial Risk Platform"
            logoSrc="/brand/logo.png"
            logoAlt="Fusion Practices"
            actions={<ThemeToggle />}
          />
        </div>
        <main>{children}</main>
      </div>
    </>
  )
}
