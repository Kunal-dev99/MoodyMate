'use client'

import { Forbidden } from '@/components/auth/Forbidden'
import { RequirePermission } from '@/components/auth/RequirePermission'
import { PageHeader } from '@/components/common/PageHeader'
import { RegistryTabs } from '@/components/registry/RegistryTabs'
import { RuleList } from '@/components/registry/RuleList'

/** FE-16.1: the rule registry. Cosmetic gate; the API enforces rule:read (D-04). */
export default function RulesPage() {
  return (
    <>
      <PageHeader
        title="Rules"
        description="Methodology rules, their parameter kinds and versions. Seeded values are illustrative and provisional; they change only through maker-checker approval."
      />
      <RegistryTabs />
      <div className="px-6 pb-6">
        <RequirePermission permission="rule:read" fallback={<Forbidden permission="rule:read" />}>
          <RuleList />
        </RequirePermission>
      </div>
    </>
  )
}
