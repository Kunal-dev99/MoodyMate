import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs'
import { dirname, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'
import { describe, expect, it } from 'vitest'
import * as calculations from '@/components/calculations/calculations'
import * as scenarioRuns from '@/components/advanced/scenario-runs'
import * as scenarios from '@/components/advanced/scenarios'
import * as runs from '@/lib/runs'
import * as links from '@/lib/scenario-links'

/*
 * FE-35.1 Done-when: a vitest import-graph check finds no cycle. The graph is every non-test
 * source file under src/, with each `import`, `export … from` and `import()` resolved through
 * the `@/` alias or a relative path (TypeScript's own pre-processor reads them, so comments and
 * strings never add an edge). Type-only imports count too.
 */

const SRC = fileURLToPath(new URL('../../', import.meta.url))
const openapi = JSON.parse(readFileSync(fileURLToPath(new URL('../../../../backend/openapi.json', import.meta.url)), 'utf8'))

function sourceFiles(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) {
      if (name !== '__tests__') sourceFiles(path, out)
    } else if (/\.tsx?$/.test(name) && !/\.d\.ts$/.test(name) && !/\.test\.tsx?$/.test(name)) out.push(path)
  }
  return out
}

function resolveSpecifier(from: string, spec: string): string | null {
  const base = spec.startsWith('@/') ? join(SRC, spec.slice(2)) : spec.startsWith('.') ? resolve(dirname(from), spec) : null
  if (base === null) return null
  for (const candidate of [base, `${base}.ts`, `${base}.tsx`, join(base, 'index.ts'), join(base, 'index.tsx')]) if (existsSync(candidate) && statSync(candidate).isFile()) return candidate
  return null
}

/** file (relative to src/) → the src files it imports. */
function importGraph(): Map<string, string[]> {
  const graph = new Map<string, string[]>()
  for (const file of sourceFiles(SRC)) {
    const { importedFiles } = ts.preProcessFile(readFileSync(file, 'utf8'), true, true)
    const deps = new Set<string>()
    for (const { fileName } of importedFiles) {
      const target = resolveSpecifier(file, fileName)
      if (target) deps.add(relative(SRC, target))
    }
    graph.set(relative(SRC, file), [...deps])
  }
  return graph
}

/** Tarjan's strongly connected components with more than one node (or a self-import): the cycles. */
function cycles(graph: Map<string, string[]>): string[][] {
  let next = 0
  const index = new Map<string, number>()
  const low = new Map<string, number>()
  const stack: string[] = []
  const onStack = new Set<string>()
  const found: string[][] = []
  const visit = (v: string) => {
    index.set(v, next)
    low.set(v, next)
    next += 1
    stack.push(v)
    onStack.add(v)
    for (const w of graph.get(v) ?? []) {
      if (!index.has(w)) {
        visit(w)
        low.set(v, Math.min(low.get(v)!, low.get(w)!))
      } else if (onStack.has(w)) low.set(v, Math.min(low.get(v)!, index.get(w)!))
    }
    if (low.get(v) === index.get(v)) {
      const component: string[] = []
      let w: string
      do {
        w = stack.pop()!
        onStack.delete(w)
        component.push(w)
      } while (w !== v)
      if (component.length > 1 || (graph.get(v) ?? []).includes(v)) found.push(component.sort())
    }
  }
  for (const v of graph.keys()) if (!index.has(v)) visit(v)
  return found
}

/** A file's workspace folder: `components/<area>`, `app/<group>`, or the top folder (lib, api, config). */
const folderOf = (file: string): string => {
  const parts = file.split('/')
  return parts[0] === 'components' || parts[0] === 'app' ? parts.slice(0, 2).join('/') : parts[0]
}

function folderGraph(graph: Map<string, string[]>): Map<string, string[]> {
  const folders = new Map<string, Set<string>>()
  for (const [file, deps] of graph) {
    const from = folderOf(file)
    if (!folders.has(from)) folders.set(from, new Set())
    for (const dep of deps) if (folderOf(dep) !== from) folders.get(from)!.add(folderOf(dep))
  }
  return new Map([...folders].map(([k, v]) => [k, [...v]]))
}

const CALCULATIONS = 'components/calculations'
const ADVANCED = 'components/advanced'

describe('the import graph (FE-35.1 Done-when)', () => {
  const graph = importGraph()

  it('reads the real tree: the helpers module and both workspaces are in it', () => {
    expect(graph.get('lib/runs.ts')).toBeDefined()
    expect([...graph.keys()].filter((f) => f.startsWith(`${CALCULATIONS}/`)).length).toBeGreaterThan(5)
    expect([...graph.keys()].filter((f) => f.startsWith(`${ADVANCED}/`)).length).toBeGreaterThan(5)
    // The advanced workspace still builds on the calculations one (run identity, status pill).
    expect(graph.get(`${ADVANCED}/ScenarioRunPanel.tsx`)).toContain(`${CALCULATIONS}/RunStatusPill.tsx`)
  })

  it('finds no cycle between source files', () => {
    expect(cycles(graph)).toEqual([])
  })

  it('finds no calculations ↔ advanced cycle, direct or through another folder', () => {
    const offending = [...graph].filter(([file]) => folderOf(file) === CALCULATIONS).flatMap(([file, deps]) => deps.filter((d) => folderOf(d) === ADVANCED).map((d) => `${file} -> ${d}`))
    expect(offending).toEqual([])
    const together = cycles(folderGraph(graph)).filter((c) => c.includes(CALCULATIONS) && c.includes(ADVANCED))
    expect(together).toEqual([])
    // Q16: neither workspace folder sits in any folder-level cycle. The two that remain (the app
    // shell: api, config, lib, components/{auth,common,layout,ui}; components/credit <->
    // components/domain) are a logged LOW follow-up, not asserted here.
    const folders = folderGraph(graph)
    expect(folders.has(CALCULATIONS) && folders.has(ADVANCED)).toBe(true)
    expect(cycles(folders).filter((c) => c.includes(CALCULATIONS) || c.includes(ADVANCED))).toEqual([])
  })

  it('keeps lib/runs.ts below both workspaces: it imports only the generated API types and the leaf scenario-links.ts', () => {
    expect([...graph.get('lib/runs.ts')!].sort()).toEqual(['api/client.ts', 'lib/scenario-links.ts'])
    expect(readFileSync(join(SRC, 'lib/runs.ts'), 'utf8')).toMatch(/^import type \{ Schemas \} from '@\/api\/client'$/m)
    expect(graph.get('lib/scenario-links.ts')).toEqual([])
  })

  it('has no component outside a workspace import these helpers from that workspace: they come from @/lib/runs', () => {
    const moved: Record<string, { folder: string; names: string[] }> = {
      'components/calculations/calculations.ts': { folder: CALCULATIONS, names: ['RESULT_STATUSES', 'hasResults', 'runHref'] },
      'components/advanced/scenario-runs.ts': { folder: ADVANCED, names: ['approvalVariant', 'memberLabel'] },
      'components/advanced/scenarios.ts': { folder: ADVANCED, names: ['explorerHref', 'scenarioHref'] },
    }
    const offending: string[] = []
    for (const file of sourceFiles(join(SRC, 'components'))) {
      const rel = relative(SRC, file)
      const text = readFileSync(file, 'utf8')
      for (const [home, { folder, names }] of Object.entries(moved)) {
        if (folderOf(rel) === folder) continue
        const spec = `@/${home.replace(/\.ts$/, '')}`
        for (const m of text.matchAll(/import\s+(?:type\s+)?\{([^}]*)\}\s+from\s+'([^']+)'/g)) {
          if (m[2] !== spec) continue
          for (const name of names) if (new RegExp(`\\b${name}\\b`).test(m[1])) offending.push(`${rel} imports ${name} from ${spec}`)
        }
      }
    }
    expect(offending).toEqual([])
  })
})

describe('src/lib/runs.ts (FE-35.1)', () => {
  it('is the one definition: the home modules re-export the same objects, not copies', () => {
    expect(calculations.RESULT_STATUSES).toBe(runs.RESULT_STATUSES)
    expect(calculations.hasResults).toBe(runs.hasResults)
    expect(calculations.runHref).toBe(runs.runHref)
    expect(scenarioRuns.approvalVariant).toBe(runs.approvalVariant)
    expect(scenarioRuns.memberLabel).toBe(runs.memberLabel)
    expect(scenarios.scenarioHref).toBe(runs.scenarioHref)
    expect(scenarios.explorerHref).toBe(runs.explorerHref)
    expect(runs.scenarioHref).toBe(links.scenarioHref)
    expect(runs.explorerHref).toBe(links.explorerHref)
    for (const [file, names] of [
      ['components/calculations/calculations.ts', ['RESULT_STATUSES', 'hasResults', 'runHref']],
      ['components/advanced/scenario-runs.ts', ['approvalVariant', 'memberLabel']],
      ['components/advanced/scenarios.ts', ['explorerHref', 'scenarioHref']],
      ['lib/runs.ts', ['explorerHref', 'scenarioHref']],
    ] as const) {
      const text = readFileSync(join(SRC, file), 'utf8')
      for (const name of names) expect(text, `${file} defines ${name}`).not.toMatch(new RegExp(`export (const|function) ${name}\\b`))
    }
  })

  it('has results only for the statuses the server calls succeeded, from the published enum', () => {
    const statuses: runs.RunStatus[] = openapi.components.schemas.CalculationRunStatus.enum
    expect(statuses.length).toBeGreaterThan(2)
    for (const s of statuses) expect(runs.hasResults(s), s).toBe(s === 'SUCCEEDED' || s === 'SUCCEEDED_WITH_WARNINGS')
    expect([...runs.RESULT_STATUSES].sort()).toEqual(['SUCCEEDED', 'SUCCEEDED_WITH_WARNINGS'])
  })

  it('never shows an exploration as approved', () => {
    const states: runs.ApprovalState[] = openapi.components.schemas.ApprovalState.enum
    expect(states).toContain('EXPLORATION')
    for (const s of states) expect(runs.approvalVariant(s), s).toBe(s === 'APPROVED' ? 'success' : 'warning')
  })

  it('builds the run and scenario links and the member labels', () => {
    expect(runs.runHref('a/b c')).toBe('/calculations/runs/a%2Fb%20c')
    expect(runs.explorerHref).toBe('/advanced/stress-explorer')
    expect(runs.scenarioHref('s-1')).toBe('/advanced/stress-explorer/s-1')
    expect(runs.memberLabel({ combined: false, shock_subset: [2] })).toBe('Shock 2 alone')
    expect(runs.memberLabel({ combined: true, shock_subset: [3, 1] })).toBe('Combined (shocks 1 + 3)')
    expect(runs.memberLabel({ combined: true, shock_subset: [1] })).toBe('Shock 1')
    expect(runs.memberLabel({ combined: false, shock_subset: [2, 1] })).toBe('Shocks 1 + 2')
  })
})
