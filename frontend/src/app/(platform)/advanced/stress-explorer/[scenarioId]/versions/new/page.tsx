'use client'

import { useParams, useSearchParams } from 'next/navigation'
import { Suspense, useMemo } from 'react'
import { ScenarioEditor, type EditorMode } from '@/components/advanced/ScenarioEditor'
import { LoadingBlock } from '@/components/common/LoadingBlock'

function NewVersion() {
  const { scenarioId } = useParams<{ scenarioId: string }>()
  const raw = useSearchParams().get('parent')
  const parent = raw && /^[1-9]\d{0,6}$/.test(raw) ? parseInt(raw, 10) : null
  const mode = useMemo<EditorMode>(() => ({ kind: 'version', scenarioId, parent }), [scenarioId, parent])
  return <ScenarioEditor mode={mode} />
}

/** FE-24.2: a new DRAFT version, adapting `?parent=` when given (D-44 §7). */
export default function NewVersionPage() {
  return (
    <Suspense fallback={<LoadingBlock rows={4} />}>
      <NewVersion />
    </Suspense>
  )
}
