'use client'

import { CalendarClock } from 'lucide-react'
import { PageHeader } from '@/components/common/PageHeader'
import { PageSection } from '@/components/common/PageSection'
import { EmptyState } from '@/components/common/EmptyState'
import { Badge } from '@/components/ui/badge'
import { Forbidden } from '@/components/auth/Forbidden'
import { useCurrentSession } from '@/components/auth/SessionProvider'
import { findWorkspace } from '@/config/navigation'

/** Honest placeholder for a workspace that a later delivery-plan phase builds. */
export function PlannedWorkspace({ slug }: { slug: string }) {
  // Looked up client-side: icon components cannot cross the server/client boundary.
  const workspace = findWorkspace(slug)
  const { has } = useCurrentSession()
  if (!workspace?.planned) return null
  if (workspace.permission && !has(workspace.permission)) {
    return (
      <>
        <PageHeader title={workspace.label} />
        <div className="px-6 pb-6">
          <Forbidden permission={workspace.permission} />
        </div>
      </>
    )
  }
  return (
    <>
      <PageHeader
        title={workspace.label}
        description={workspace.description}
        actions={<Badge variant="secondary">Planned</Badge>}
      />
      <div className="px-6 pb-6">
        {/* The delivery phase stays in config/navigation.ts (`planned`); users see product copy (P-12). */}
        <PageSection icon={workspace.icon} title="Not available yet">
          <EmptyState
            icon={CalendarClock}
            title="This workspace is planned, not built"
            description="Planned for a later release."
          />
        </PageSection>
      </div>
    </>
  )
}
