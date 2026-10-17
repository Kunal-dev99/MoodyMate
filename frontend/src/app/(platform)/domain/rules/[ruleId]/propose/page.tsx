'use client'

import { useParams } from 'next/navigation'
import { Forbidden } from '@/components/auth/Forbidden'
import { RequirePermission } from '@/components/auth/RequirePermission'
import { PageHeader } from '@/components/common/PageHeader'
import { RegistryGate } from '@/components/registry/RegistryGate'
import { ProposeRuleVersion } from '@/components/rules/ProposeRuleVersion'

const CRUMBS = [
  { label: 'Financial Domain', href: '/domain' },
  { label: 'Rules', href: '/domain/rules' },
  { label: 'Propose' },
]

/**
 * FE-16.2: propose a new version of a rule and submit it. A 404 while NEXT_PUBLIC_RULES_UI=off,
 * like every registry route (D-02). Cosmetic gate; the API enforces rule:propose (D-04).
 */
export default function ProposeRulePage() {
  const { ruleId } = useParams<{ ruleId: string }>()
  return (
    <RegistryGate>
      <RequirePermission
        permission="rule:propose"
        fallback={
          <>
            <PageHeader title="Propose a rule change" breadcrumbs={CRUMBS} />
            <div className="px-6 pb-6">
              <Forbidden permission="rule:propose" />
            </div>
          </>
        }
      >
        <ProposeRuleVersion key={ruleId} ruleId={ruleId} />
      </RequirePermission>
    </RegistryGate>
  )
}
