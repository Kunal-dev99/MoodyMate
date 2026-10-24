import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { Masked, MaskedFlag } from '@/components/common/Masked'
import { MaskedValue, isMasked } from '@/components/common/MaskedValue'

/* FE-29.2: the one <Masked> value component; MaskedValue is the same component by its FE-3.2 name. */

const marker = { masked: true, classification: 'confidential' } as const

describe('<Masked> (FE-29.2)', () => {
  it('is what the FE-3.2 names export', () => {
    expect(MaskedValue).toBe(Masked)
    expect(isMasked(marker)).toBe(true)
  })

  it('renders a masked value as explicit "masked" text with its classification, never blank', () => {
    const html = renderToStaticMarkup(createElement(Masked, { value: marker }))
    expect(html).toContain('data-testid="masked-value"')
    expect(html).toContain('aria-label="Masked by policy: confidential data"')
    expect(html).toMatch(/<\/svg>masked<\/span>$/)
  })

  it('wraps a masked cell so a test finds it by its own id, with extra attributes before data-masked', () => {
    const html = renderToStaticMarkup(createElement(Masked, { value: marker, cell: true, testId: 'lcr-cap-applied', attrs: { 'data-applied': 'masked' } }))
    expect(html.startsWith('<span data-testid="lcr-cap-applied" data-applied="masked" data-masked="true">')).toBe(true)
    expect(html).toContain('data-testid="masked-value"')
  })

  it('renders a clear value through its children, or as is, and never adds the marker', () => {
    // Masked holds no hooks, so it is called directly to hand it a render function.
    const viaChildren = renderToStaticMarkup(Masked({ value: '4443500.86', cell: true, testId: 'x', children: (v: string) => createElement('b', null, v) }))
    expect(viaChildren).toBe('<b>4443500.86</b>')
    expect(renderToStaticMarkup(createElement(Masked, { value: 'plain' }))).toBe('plain')
    expect(renderToStaticMarkup(Masked({ value: { masked: false, classification: 'internal' }, children: () => 'clear' }))).toBe('clear')
  })

  it('MaskedFlag says the flag reads a masked figure, never "no"', () => {
    const html = renderToStaticMarkup(createElement(MaskedFlag, { flag: 'combined-only', as: 'div', testId: 'combined-only-unknown', className: 'c', attrs: { 'data-reason': 'masked' } }))
    expect(html).toBe('<div class="c" data-testid="combined-only-unknown" data-reason="masked" title="The flag reads a masked figure, so it is not shown.">combined-only: masked</div>')
  })
})
