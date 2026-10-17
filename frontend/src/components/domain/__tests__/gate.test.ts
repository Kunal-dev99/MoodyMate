import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'

// The gate decides from the nav entry alone; the placeholder itself is FE-6 code.
const nav = vi.hoisted(() => ({ planned: undefined as undefined | { phase: string; workstream: string } }))
vi.mock('@/config/navigation', () => ({
  findWorkspace: (slug: string) => (slug === 'domain' ? { slug, planned: nav.planned } : undefined),
}))
vi.mock('@/components/common/PlannedWorkspace', () => ({
  PlannedWorkspace: ({ slug }: { slug: string }) => createElement('p', null, `planned placeholder: ${slug}`),
}))

const { DomainGate } = await import('@/components/domain/DomainGate')
const render = () => renderToStaticMarkup(createElement(DomainGate, null, createElement('p', null, 'catalogue')))

describe('domain reversibility (FE-14.1, D-02)', () => {
  afterEach(() => {
    nav.planned = undefined
  })
  it('shows the workspace while the nav entry is live', () => {
    expect(render()).toBe('<p>catalogue</p>')
  })
  it('restores the planned placeholder when the nav entry is planned (NEXT_PUBLIC_DOMAIN_UI=off)', () => {
    nav.planned = { phase: 'Feature 03', workstream: 'WS3' }
    expect(render()).toBe('<p>planned placeholder: domain</p>')
  })
})
