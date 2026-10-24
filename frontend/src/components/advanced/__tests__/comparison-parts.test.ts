import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { Schemas } from '@/api/client'
import { MissingMembers } from '@/components/advanced/ComparisonIdentity'
import { LargestChanges } from '@/components/advanced/ComparisonRankings'
import * as table from '@/components/advanced/ComparisonTable'
import * as view from '@/components/advanced/ComparisonView'

/*
 * FE-35.2: ComparisonView.tsx is split into its heat map, identity and rankings parts and a
 * loading hook. The FE-25.2 vitests and Playwright specs run unchanged against it; this file
 * holds the split itself to its Done-when (no file over 300 lines) and carries FE-25.2's code
 * hygiene to the new files, which comparison.test.ts lists by name.
 */

const root = fileURLToPath(new URL('../../../', import.meta.url))
const PARTS = [
  'components/advanced/ComparisonView.tsx',
  'components/advanced/ComparisonTable.tsx',
  'components/advanced/ComparisonIdentity.tsx',
  'components/advanced/ComparisonRankings.tsx',
  'components/advanced/useComparison.ts',
]
const read = (file: string) => readFileSync(`${root}${file}`, 'utf8')
const lines = (file: string) => read(file).split('\n').length - (read(file).endsWith('\n') ? 1 : 0)

type Member = Schemas['ScenarioRunMember']
const member = (over: Partial<Member>): Member => ({
  calculation_run_id: '00000000-0000-4000-8000-000000000001',
  shock_subset: [1],
  combined: false,
  status: 'SUCCEEDED',
  manifest_sha256: 'a'.repeat(64),
  result_sha256: 'b'.repeat(64),
  ...over,
})

describe('the comparison view split (FE-35.2 Done-when)', () => {
  it.each(PARTS)('%s is at most 300 lines', (file) => {
    expect(lines(file), file).toBeLessThanOrEqual(300)
  })

  it.each(PARTS.slice(1))('%s never parses a figure as a float, hand-writes no payload type and calls no API itself', (file) => {
    const text = read(file)
    for (const p of [/parseFloat/, /\bNumber\s*\(/, /\bNumber\./, /\.toFixed\s*\(/, /NumberFormat/, /^\s*(export\s+)?interface\s+\w*(Out|Page)\b/m, /api\.[A-Z]+\(/]) expect(text, `${file} ${p}`).not.toMatch(p)
  })

  it('still exports the heat-map parts from ComparisonView, as the same components', () => {
    expect(view.ComparisonTable).toBe(table.ComparisonTable)
    expect(view.DeltaCell).toBe(table.DeltaCell)
  })

  it('never shows a member run without results as compared', () => {
    expect(renderToStaticMarkup(createElement(MissingMembers, { comparison: { members: [member({}), member({ status: 'SUCCEEDED_WITH_WARNINGS', combined: true, shock_subset: [1, 2] })] } }))).toBe('')
    const html = renderToStaticMarkup(createElement(MissingMembers, { comparison: { members: [member({}), member({ shock_subset: [2], status: 'FAILED' }), member({ shock_subset: [3], status: 'RUNNING' })] } }))
    expect(html).toContain('data-testid="comparison-missing-members"')
    expect(html).toContain('ended FAILED')
    expect(html).toContain('ended RUNNING')
    expect(html).not.toContain('ended SUCCEEDED')
  })

  it('ranks nothing when the server ranked nothing', () => {
    const html = renderToStaticMarkup(createElement(LargestChanges, { comparison: { largest_changes: [], buckets: [] }, onDrill: () => undefined }))
    expect(html).toContain('data-testid="largest-none"')
    expect(html).not.toContain('data-testid="largest-changes"')
  })
})
