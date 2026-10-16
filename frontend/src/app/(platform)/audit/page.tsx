'use client'

import { ScrollText } from 'lucide-react'
import { AuditTimeline } from '@/components/audit/AuditTimeline'
import { RequirePermission } from '@/components/auth/RequirePermission'
import { PageHeader } from '@/components/common/PageHeader'
import { PageSection } from '@/components/common/PageSection'

export default function AuditPage() {
  return (
    <>
      <PageHeader
        title="Audit"
        description="Append-only record of privileged actions, including denied requests. Reading it is audited too."
      />
      <div className="px-6 pb-6">
        <RequirePermission permission="audit:read">
          <PageSection icon={ScrollText} title="Audit trail" accent="primary">
            <AuditTimeline />
          </PageSection>
        </RequirePermission>
      </div>
    </>
  )
}
