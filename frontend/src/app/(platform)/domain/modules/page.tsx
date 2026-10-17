'use client'

import { Forbidden } from '@/components/auth/Forbidden'
import { RequirePermission } from '@/components/auth/RequirePermission'
import { PageHeader } from '@/components/common/PageHeader'
import { ModuleRegistry } from '@/components/registry/ModuleRegistry'
import { RegistryTabs } from '@/components/registry/RegistryTabs'

/** FE-16.1: the calculation module registry, AVAILABLE and PLANNED apart. Cosmetic gate; the API enforces rule:read (D-04). */
export default function ModulesPage() {
  return (
    <>
      <PageHeader
        title="Modules"
        description="Calculation modules and their versions. Available ones are illustrative prototypes, not regulatory calculations; planned ones are extension points that never run."
      />
      <RegistryTabs />
      <div className="px-6 pb-6">
        <RequirePermission permission="rule:read" fallback={<Forbidden permission="rule:read" />}>
          <ModuleRegistry />
        </RequirePermission>
      </div>
    </>
  )
}
