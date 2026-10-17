'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useCurrentSession } from '@/components/auth/SessionProvider'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { domainSections, type WorkspaceSection } from '@/config/navigation'

/** The section a /domain path belongs to: the instrument catalogue unless a registry section matches. */
export function activeSection(pathname: string, sections: readonly WorkspaceSection[]): string | undefined {
  const registry = sections.find((s) => s.href !== '/domain' && (pathname === s.href || pathname.startsWith(`${s.href}/`)))
  return registry?.slug ?? sections.find((s) => s.href === '/domain')?.slug
}

/**
 * FE-16.1: the Financial Domain's pages as tabs (instruments, rules, modules, assumption sets),
 * each a route. Renders nothing while NEXT_PUBLIC_RULES_UI=off, so /domain is the Phase 15 page
 * (D-02). A tab without its permission is hidden (cosmetic; the API answers 403, D-04).
 */
export function RegistryTabs() {
  const pathname = usePathname() ?? ''
  const { has } = useCurrentSession()
  const sections = domainSections.filter((s) => has(s.permission))
  if (sections.length === 0) return null
  return (
    <Tabs value={activeSection(pathname, sections)} className="px-6 pb-4">
      <TabsList data-testid="domain-tabs">
        {sections.map((s) => (
          <TabsTrigger key={s.slug} value={s.slug} asChild>
            <Link href={s.href} data-testid={`domain-tab-${s.slug}`}>
              {s.label}
            </Link>
          </TabsTrigger>
        ))}
      </TabsList>
    </Tabs>
  )
}
