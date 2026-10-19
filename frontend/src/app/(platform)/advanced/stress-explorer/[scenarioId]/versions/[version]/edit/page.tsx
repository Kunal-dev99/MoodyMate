'use client'

import { useParams } from 'next/navigation'
import { useMemo } from 'react'
import { ScenarioEditor, type EditorMode } from '@/components/advanced/ScenarioEditor'
import { EmptyState } from '@/components/common/EmptyState'
import { FileWarning } from 'lucide-react'

/** FE-24.2: replace a DRAFT's shocks (the author only; the server answers 409 `not_author` otherwise). */
export default function EditVersionPage() {
  const { scenarioId, version } = useParams<{ scenarioId: string; version: string }>()
  const number = /^[1-9]\d{0,6}$/.test(version) ? parseInt(version, 10) : null
  const mode = useMemo<EditorMode | null>(() => (number === null ? null : { kind: 'edit', scenarioId, version: number }), [scenarioId, number])
  if (!mode) return <EmptyState icon={FileWarning} title="Not a version number" className="m-6" />
  return <ScenarioEditor mode={mode} />
}
