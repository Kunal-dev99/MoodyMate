'use client'

import { useEffect, useMemo, useState } from 'react'
import { createLoader, type Loaded } from '@/components/common/load'
import type { RunDetail } from '@/components/calculations/calculations'
import { fetchComparison, type ScenarioComparison } from './comparison'
import { fetchBaseline } from './scenario-runs'

const noFetch = () => new Promise<never>(() => undefined)

/**
 * FE-25.2 (split out of ComparisonView.tsx by FE-35.2, unchanged): loads one scenario run's
 * comparison (calc:read), then its baseline run for the pinned configuration versions. Each
 * answer is the server's, including 403 and 404; `reload` retries the comparison.
 */
export function useComparison(scenarioId: string, scenarioRunId: string): { state: Loaded<ScenarioComparison>; baseline: Loaded<RunDetail>; reload: () => void } {
  const [state, setState] = useState<Loaded<ScenarioComparison>>({ kind: 'loading' })
  const [baseline, setBaseline] = useState<Loaded<RunDetail>>({ kind: 'loading' })
  const loader = useMemo(() => createLoader(fetchComparison(scenarioId, scenarioRunId), setState, 'Could not load the comparison'), [scenarioId, scenarioRunId])
  useEffect(() => {
    loader.load()
    return loader.abort
  }, [loader])
  const baselineId = state.kind === 'ready' ? state.value.baseline_run_id : null
  const baselineLoader = useMemo(() => createLoader(baselineId ? fetchBaseline(baselineId) : noFetch, setBaseline, 'Could not load the baseline run'), [baselineId])
  useEffect(() => {
    if (!baselineId) return
    baselineLoader.load()
    return baselineLoader.abort
  }, [baselineId, baselineLoader])
  return { state, baseline, reload: loader.load }
}
