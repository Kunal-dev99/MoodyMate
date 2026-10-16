import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'

// The gate decides from the nav entry alone; the placeholder itself is FE-6 code.
const nav = vi.hoisted(() => ({ planned: undefined as undefined | { phase: string; workstream: string } }))
vi.mock('@/config/navigation', () => ({
  findWorkspace: (slug: string) => (slug === 'ingestion' ? { slug, planned: nav.planned } : undefined),
}))
vi.mock('@/components/common/PlannedWorkspace', () => ({
  PlannedWorkspace: ({ slug }: { slug: string }) => createElement('p', null, `planned placeholder: ${slug}`),
}))

const { IngestionGate } = await import('@/components/ingestion/IngestionGate')
const render = () => renderToStaticMarkup(createElement(IngestionGate, null, createElement('p', null, 'workbench')))

describe('ingestion reversibility (FE-7.1, D-02)', () => {
  afterEach(() => {
    nav.planned = undefined
  })
  it('shows the workbench while the nav entry is live', () => {
    expect(render()).toBe('<p>workbench</p>')
  })
  it('restores the planned placeholder when the nav entry is reverted to planned', () => {
    nav.planned = { phase: 'Feature 02', workstream: 'WS2' }
    expect(render()).toBe('<p>planned placeholder: ingestion</p>')
  })
})
