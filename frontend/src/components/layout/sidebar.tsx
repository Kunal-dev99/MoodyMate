'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useState, useEffect, type ComponentType } from 'react'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Separator } from '@/components/ui/separator'
import { Button } from '@/components/ui/button'
import { ThemeToggle } from '@/components/layout/ThemeToggle'
import { ChevronLeft, ChevronRight, LogOut } from 'lucide-react'
import { cn } from '@/lib/utils'

/**
 * Generic sidebar lifted from fp_reporting. To use in another app, pass
 * `mainNav` and (optionally) `adminNav` as the only required props. The shell,
 * theming, collapse behavior, active-rail indicator, and footer are reusable.
 *
 * Collapse state persists to localStorage under SIDEBAR_STORAGE_KEY so the
 * preference survives reloads without needing a global store.
 */
const SIDEBAR_STORAGE_KEY = 'fp_sidebar_open'

export interface NavItem {
  href: string
  label: string
  icon: ComponentType<{ className?: string }>
}

export interface SidebarUser {
  /** Two-line label in the footer. */
  name: string
  email?: string
  /** Initial used inside the avatar circle. Falls back to first char of name. */
  initial?: string
}

export interface SidebarProps {
  mainNav: NavItem[]
  adminNav?: NavItem[]
  /** Full brand name shown when expanded. */
  brandName?: string
  /** Tagline under the brand name (e.g. "Oracle Partner"). */
  brandTagline?: string
  /** Two-letter mark shown when collapsed. */
  brandShort?: string
  /** Link target of the brand mark. */
  brandHref?: string
  user?: SidebarUser
  onLogout?: () => void
}

function readSidebarOpen(): boolean {
  if (typeof window === 'undefined') return true
  try {
    const saved = window.localStorage.getItem(SIDEBAR_STORAGE_KEY)
    if (saved === '0') return false
    if (saved === '1') return true
  } catch {}
  return true
}

export function Sidebar({
  mainNav,
  adminNav,
  brandName = 'Brand',
  brandTagline,
  brandShort = 'BR',
  brandHref = '/',
  user,
  onLogout,
}: SidebarProps) {
  const pathname = usePathname() ?? ''
  const [sidebarOpen, setSidebarOpen] = useState<boolean>(true)

  useEffect(() => {
    setSidebarOpen(readSidebarOpen())
  }, [])

  const toggleSidebar = () => {
    setSidebarOpen((prev) => {
      const next = !prev
      try {
        window.localStorage.setItem(SIDEBAR_STORAGE_KEY, next ? '1' : '0')
      } catch {}
      return next
    })
  }

  const renderItem = (item: NavItem) => {
    const isActive = pathname === item.href || pathname.startsWith(item.href + '/')
    const Icon = item.icon
    return (
      <Link
        key={item.href}
        href={item.href}
        className={cn(
          'relative flex items-center gap-3 rounded-sm px-3 py-2 text-sm transition-colors',
          isActive
            ? 'bg-surface-2 text-primary font-medium'
            : 'text-muted-foreground hover:bg-surface-2 hover:text-foreground',
        )}
        title={!sidebarOpen ? item.label : undefined}
      >
        {isActive && (
          <span className="absolute left-0 top-1 bottom-1 w-[3px] rounded-r-sm bg-primary" />
        )}
        <Icon className={cn('h-[18px] w-[18px] flex-shrink-0', isActive && 'text-primary')} />
        {sidebarOpen && <span className="truncate">{item.label}</span>}
      </Link>
    )
  }

  return (
    <aside
      className={cn(
        'fixed left-0 top-0 z-40 h-screen border-r border-border bg-surface-1 transition-all duration-200',
        sidebarOpen ? 'w-64' : 'w-16',
      )}
    >
      <div className="flex h-full flex-col">
        {/* Logo */}
        <div className="flex h-16 items-center justify-between gap-2 px-3 border-b border-border">
          <Link href={brandHref} className="flex items-center min-w-0">
            {sidebarOpen ? (
              <div className="flex flex-col min-w-0 leading-tight">
                <span className="text-sm font-semibold tracking-tight text-foreground truncate">
                  {brandName}
                </span>
                {brandTagline && (
                  <span className="text-[10px] uppercase tracking-wider text-muted-foreground truncate">
                    {brandTagline}
                  </span>
                )}
              </div>
            ) : (
              <span className="text-sm font-semibold tracking-tight text-primary">{brandShort}</span>
            )}
          </Link>
          <Button
            variant="ghost"
            size="icon"
            onClick={toggleSidebar}
            className="h-7 w-7 shrink-0"
            aria-label={sidebarOpen ? 'Collapse sidebar' : 'Expand sidebar'}
          >
            {sidebarOpen ? <ChevronLeft className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
          </Button>
        </div>

        {/* Navigation */}
        <ScrollArea className="flex-1 py-4">
          {sidebarOpen && (
            <div className="px-4 mb-2">
              <span className="text-[10px] font-medium text-muted-foreground uppercase tracking-widest">
                Workspace
              </span>
            </div>
          )}
          <nav className="space-y-0.5 px-2">{mainNav.map(renderItem)}</nav>

          {adminNav && adminNav.length > 0 && (
            <>
              <Separator className="my-4 mx-2 bg-border" />
              {sidebarOpen && (
                <div className="px-4 mb-2">
                  <span className="text-[10px] font-medium text-muted-foreground uppercase tracking-widest">
                    Administration
                  </span>
                </div>
              )}
              <nav className="space-y-0.5 px-2">{adminNav.map(renderItem)}</nav>
            </>
          )}
        </ScrollArea>

        {/* User + theme toggle */}
        {user && (
          <div className="border-t border-border p-2">
            <div
              className={cn(
                'flex items-center gap-2 rounded-sm p-1.5 transition-colors hover:bg-surface-2',
                !sidebarOpen && 'justify-center',
              )}
            >
              <div className="h-7 w-7 rounded-full bg-primary text-primary-foreground flex items-center justify-center font-semibold text-xs flex-shrink-0">
                {(user.initial ?? user.name.charAt(0)).toUpperCase()}
              </div>
              {sidebarOpen && (
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-medium truncate">{user.name}</p>
                  {user.email && (
                    <p className="text-[10px] text-muted-foreground truncate">{user.email}</p>
                  )}
                </div>
              )}
              {sidebarOpen && (
                <>
                  <ThemeToggle />
                  {onLogout && (
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={onLogout}
                      className="h-7 w-7 flex-shrink-0"
                      aria-label="Log out"
                    >
                      <LogOut className="h-3.5 w-3.5" />
                    </Button>
                  )}
                </>
              )}
            </div>
          </div>
        )}
      </div>
    </aside>
  )
}

export { SIDEBAR_STORAGE_KEY }
