'use client'

import { ScenarioEditor, type EditorMode } from '@/components/advanced/ScenarioEditor'

const MODE: EditorMode = { kind: 'create' }

/** FE-24.2: a new scenario and its first DRAFT version. The editor checks scenario:propose (cosmetic; D-04). */
export default function NewScenarioPage() {
  return <ScenarioEditor mode={MODE} />
}
