'use client'

import type { ReactNode } from 'react'
import { Header } from '@/components/layout/header'
import { Sidebar } from '@/components/layout/sidebar'
import { ThemeToggle } from '@/components/layout/ThemeToggle'
import type { NavItem } from '@/components/layout/sidebar'
import { adminWorkspaces, mainWorkspaces, type Workspace } from '@/config/navigation'

const toNav = ({ href, label, icon }: Workspace): NavItem => ({ href, label, icon })

/**
 * Authenticated app shell (FE-1.2), wired per the design bundle's "App shell" example.
 * The user footer, logout and permission-aware nav arrive with identity in Phase 2.
 */
export function AppShell({ children }: { children: ReactNode }) {
  return (
    <>
      <Sidebar
        mainNav={mainWorkspaces.map(toNav)}
        adminNav={adminWorkspaces.map(toNav)}
        brandName="MoodyMate"
        brandTagline="Financial Risk Platform"
        brandShort="MM"
        brandHref="/dashboard"
      />
      <div className="ml-64 transition-all duration-300">
        <Header
          title="Financial Risk Platform"
          logoSrc="/brand/logo.png"
          logoAlt="Fusion Practices"
          actions={<ThemeToggle />}
        />
        <main>{children}</main>
      </div>
    </>
  )
}
