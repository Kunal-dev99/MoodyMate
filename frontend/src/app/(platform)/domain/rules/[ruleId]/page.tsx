'use client'

import { useParams } from 'next/navigation'
import { Forbidden } from '@/components/auth/Forbidden'
import { RequirePermission } from '@/components/auth/RequirePermission'
import { PageHeader } from '@/components/common/PageHeader'
import { RuleView } from '@/components/registry/RuleView'

const CRUMBS = [
  { label: 'Financial Domain', href: '/domain' },
  { label: 'Rules', href: '/domain/rules' },
]

/** FE-16.1: one rule, its version history, parameters, diff and pinning runs (FE-16.2 actions mount inside). */
export default function RulePage() {
  const { ruleId } = useParams<{ ruleId: string }>()
  return (
    <RequirePermission
      permission="rule:read"
      fallback={
        <>
          <PageHeader title="Rule" breadcrumbs={CRUMBS} />
          <div className="px-6 pb-6">
            <Forbidden permission="rule:read" />
          </div>
        </>
      }
    >
      <RuleView key={ruleId} ruleId={ruleId} />
    </RequirePermission>
  )
}
