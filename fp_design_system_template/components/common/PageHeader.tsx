'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { ChevronRight, Home } from 'lucide-react'
import { ReactNode } from 'react'
import { cn } from '@/lib/utils'

export interface Breadcrumb {
  label: string
  href?: string
}

interface PageHeaderProps {
  title: string
  description?: string
  actions?: ReactNode
  breadcrumbs?: Breadcrumb[]
  /** Hide auto-generated breadcrumbs */
  hideBreadcrumbs?: boolean
  className?: string
}

const ROUTE_LABELS: Record<string, string> = {
  dashboard: 'Dashboard',
  reports: 'Reports',
  requirements: 'Requirements',
  matching: 'Matching Engine',
  'sql-builder': 'SQL Builder',
  catalogue: 'Catalogue',
  security: 'Security',
  configurations: 'Configurations',
  users: 'Users',
  settings: 'Settings',
  profile: 'Profile',
  standard: 'Standard',
  fp: 'FP',
  customer: 'Customer',
  unified: 'Unified',
  new: 'New',
  run: 'Run',
  testing: 'Testing',
}

function autoBreadcrumbs(pathname: string): Breadcrumb[] {
  const segments = pathname.split('/').filter(Boolean)
  const crumbs: Breadcrumb[] = []
  let href = ''
  segments.forEach((seg, idx) => {
    href += `/${seg}`
    const isLast = idx === segments.length - 1
    // Skip numeric IDs in auto-breadcrumbs
    if (/^\d+$/.test(seg) || /^[a-f0-9-]{20,}$/i.test(seg)) {
      return
    }
    const label = ROUTE_LABELS[seg] || seg.charAt(0).toUpperCase() + seg.slice(1)
    crumbs.push({ label, href: isLast ? undefined : href })
  })
  return crumbs
}

export function PageHeader({
  title,
  description,
  actions,
  breadcrumbs,
  hideBreadcrumbs,
  className,
}: PageHeaderProps) {
  const pathname = usePathname()
  const crumbs = breadcrumbs ?? (hideBreadcrumbs ? [] : autoBreadcrumbs(pathname))

  return (
    <div className={cn('px-6 pt-5 pb-4', className)}>
      {crumbs.length > 0 && (
        <nav aria-label="Breadcrumb" className="mb-2 flex items-center gap-1 text-xs text-muted-foreground">
          <Link
            href="/dashboard"
            className="flex items-center gap-1 hover:text-foreground transition-colors"
            aria-label="Home"
          >
            <Home className="h-3.5 w-3.5" />
          </Link>
          {crumbs.map((crumb, i) => (
            <span key={i} className="flex items-center gap-1">
              <ChevronRight className="h-3.5 w-3.5 text-muted-foreground/50" />
              {crumb.href ? (
                <Link
                  href={crumb.href}
                  className="hover:text-foreground transition-colors"
                >
                  {crumb.label}
                </Link>
              ) : (
                <span className="text-foreground">{crumb.label}</span>
              )}
            </span>
          ))}
        </nav>
      )}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 flex-1">
          <h1 className="text-page-title text-foreground truncate">{title}</h1>
          {description && (
            <p className="text-helper mt-1 max-w-2xl">{description}</p>
          )}
        </div>
        {actions && (
          <div className="flex items-center gap-2 shrink-0">
            {actions}
          </div>
        )}
      </div>
    </div>
  )
}
