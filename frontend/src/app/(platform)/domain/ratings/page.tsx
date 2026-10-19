'use client'

import { Suspense } from 'react'
import { Forbidden } from '@/components/auth/Forbidden'
import { RequirePermission } from '@/components/auth/RequirePermission'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { PageHeader } from '@/components/common/PageHeader'
import { RatingsReviewQueue } from '@/components/credit/review/RatingsReviewQueue'
import { RegistryTabs } from '@/components/registry/RegistryTabs'

/** FE-20.2: the ratings review queue. Cosmetic gate; the API enforces domain:read and rating:approve (D-04). */
export default function RatingsPage() {
  return (
    <>
      <PageHeader
        title="Ratings"
        description="Obligor rating proposals waiting for a decision. An approver who is not the proposer approves or rejects each one with a comment (maker-checker, D-39). Grades are human judgements: nothing here suggests one."
      />
      <RegistryTabs />
      <div className="px-6 pb-6">
        <RequirePermission permission="domain:read" fallback={<Forbidden permission="domain:read" />}>
          <Suspense fallback={<LoadingBlock rows={4} />}>
            <RatingsReviewQueue />
          </Suspense>
        </RequirePermission>
      </div>
    </>
  )
}
