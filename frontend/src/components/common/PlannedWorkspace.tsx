'use client'

import { CalendarClock } from 'lucide-react'
import { PageHeader } from '@/components/common/PageHeader'
import { PageSection } from '@/components/common/PageSection'
import { EmptyState } from '@/components/common/EmptyState'
import { Badge } from '@/components/ui/badge'
import { findWorkspace } from '@/config/navigation'

/** Honest placeholder for a workspace that a later delivery-plan phase builds. */
export function PlannedWorkspace({ slug }: { slug: string }) {
  // Looked up client-side: icon components cannot cross the server/client boundary.
  const workspace = findWorkspace(slug)
  if (!workspace?.planned) return null
  return (
    <>
      <PageHeader
        title={workspace.label}
        description={workspace.description}
        actions={<Badge variant="secondary">Planned</Badge>}
      />
      <div className="px-6 pb-6">
        <PageSection icon={workspace.icon} title="Not available yet">
          <EmptyState
            icon={CalendarClock}
            title="This workspace is planned, not built"
            description={
              <>
                Scheduled for <strong>{workspace.planned.phase}</strong> (
                {workspace.planned.workstream}). See docs/DELIVERY_PLAN.md.
              </>
            }
          />
        </PageSection>
      </div>
    </>
  )
}
