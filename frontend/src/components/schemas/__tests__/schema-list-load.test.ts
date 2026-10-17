import { createElement, isValidElement, type ReactElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Schemas } from '@/api/client'

/*
 * FE-14.3: `/schemas` and `/schemas/proposals/new` load the schema list through
 * `schemaListLoader` and render it through their view. A failed load ends in LoadFailed with
 * "Try again", and one retry renders the list. The pages only hold the state and the loader;
 * these tests drive the same loader and render the same views, with GET /schemas mocked.
 */

const toasts = vi.hoisted(() => [] as { title?: string }[])
const get = vi.hoisted(() => vi.fn())
vi.mock('@/components/ui/use-toast', () => ({ toast: (t: { title?: string }) => toasts.push(t) }))
vi.mock('@/api/client', async (importOriginal) => ({ ...(await importOriginal<typeof import('@/api/client')>()), api: { GET: get } }))
// The editor reads the query string and the router; nothing navigates in these tests.
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn(), back: vi.fn() }), useSearchParams: () => new URLSearchParams() }))

const { LoadFailed } = await import('@/components/common/LoadFailed')
const { schemaListLoader } = await import('@/components/schemas/SchemaListLoad')
const { SchemaCatalogueView } = await import('@/components/schemas/SchemaCatalogue')
const { NewProposalView } = await import('@/components/schemas/ProposalEditor')
type SchemaListState = import('@/components/schemas/SchemaListLoad').SchemaListState
type View = (props: { state: SchemaListState; onRetry: () => void }) => ReactElement

const flush = () => new Promise((r) => setTimeout(r, 0))

const LIST: Schemas['SchemaList'] = {
  total: 1,
  items: [
    {
      id: 'sch-1',
      code: 'instrument',
      name: 'Instrument',
      description: null,
      owner: 'Data office',
      status: 'ACTIVE',
      provisional: true,
      version_count: 2,
      effective_version: { id: 'v-1', version: 1, status: 'ACTIVE', effective_from: '2026-01-01' },
      latest_version: { id: 'v-2', version: 2, status: 'DRAFT', effective_from: null },
      dependents: [],
    },
  ],
}

const serverError = () => ({
  error: { code: 'internal_error', message: 'boom', details: null, correlation_id: 'cid-500', retryable: false },
  response: new Response(null, { status: 500 }),
})
const ok = (data: unknown) => ({ data, response: new Response(null, { status: 200 }) })

/** Presses the "Try again" button the view rendered (LoadFailed's action). */
function pressTryAgain(view: ReactElement) {
  // The view renders the gate, the gate renders LoadFailed for a retryable failure.
  const gate = (view.type as (p: unknown) => ReactElement)(view.props)
  const failed = (gate.type as (p: unknown) => ReactElement)(gate.props)
  expect(failed.type).toBe(LoadFailed)
  const action = (LoadFailed(failed.props as Parameters<typeof LoadFailed>[0]).props as { action: unknown }).action
  expect(isValidElement(action)).toBe(true)
  ;(action as ReactElement<{ onClick: () => void; 'data-testid': string }>).props.onClick()
}

describe.each<[string, View, string]>([
  ['/schemas', SchemaCatalogueView as View, 'data-testid="schema-row" data-code="instrument"'],
  ['/schemas/proposals/new', NewProposalView as View, '<option value="sch-1" selected="">Instrument (instrument)</option>'],
])('%s: a failed schema load ends in "Try again", and one retry renders the list (FE-14.3)', (_page, view, listed) => {
  beforeEach(() => {
    get.mockReset()
  })
  afterEach(() => {
    toasts.length = 0
  })

  it('failure → LoadFailed → retry → ready', async () => {
    get.mockResolvedValueOnce(serverError()).mockResolvedValueOnce(ok(LIST))
    const states: SchemaListState[] = []
    const loader = schemaListLoader((s) => states.push(s))
    const render = () => createElement(view, { state: states[states.length - 1], onRetry: loader.load })

    loader.load()
    expect(renderToStaticMarkup(render())).toContain('aria-busy="true"')
    await flush()

    // The 500 is a retryable failure: the error state with "Try again", never a loading block.
    expect(states.map((s) => s.kind)).toEqual(['loading', 'failed'])
    expect(states[1]).toEqual({ kind: 'failed', status: 500, cid: 'cid-500' })
    const failed = renderToStaticMarkup(render())
    expect(failed).toContain('The schemas could not be loaded')
    expect(failed).toContain('Correlation ID: cid-500')
    expect(failed).toContain('data-testid="load-retry"')
    expect(failed).toContain('Try again')
    expect(failed).not.toContain('aria-busy')
    expect(failed).not.toContain(listed)
    expect(toasts.map((t) => t.title)).toEqual(['Could not load schemas'])

    // One click on "Try again" loads again and renders the list.
    pressTryAgain(render())
    expect(states[states.length - 1].kind).toBe('loading')
    await flush()
    expect(states.map((s) => s.kind)).toEqual(['loading', 'failed', 'loading', 'ready'])
    expect(get).toHaveBeenCalledTimes(2)
    for (const [path, init] of get.mock.calls) {
      expect(path).toBe('/api/v1/schemas')
      expect(init.signal).toBeInstanceOf(AbortSignal)
    }
    const ready = renderToStaticMarkup(render())
    expect(ready).toContain(listed)
    expect(ready).not.toContain('Try again')
  })

  it('a 403 shows the access state, not "Try again"', async () => {
    get.mockResolvedValueOnce({
      error: { code: 'forbidden', message: 'no', details: null, correlation_id: 'cid-403', retryable: false },
      response: new Response(null, { status: 403 }),
    })
    const states: SchemaListState[] = []
    schemaListLoader((s) => states.push(s)).load()
    await flush()
    const html = renderToStaticMarkup(createElement(view, { state: states[states.length - 1], onRetry: () => {} }))
    expect(html).toContain("You don&#x27;t have access to this")
    expect(html).toContain('schema:read')
    expect(html).not.toContain('Try again')
    expect(toasts).toHaveLength(0)
  })

  it('a network failure is retryable too', async () => {
    get.mockRejectedValueOnce(new TypeError('Failed to fetch'))
    const states: SchemaListState[] = []
    schemaListLoader((s) => states.push(s)).load()
    await flush()
    expect(states[states.length - 1]).toEqual({ kind: 'failed', status: 0, cid: null })
    expect(renderToStaticMarkup(createElement(view, { state: states[states.length - 1], onRetry: () => {} }))).toContain('Try again')
  })
})

describe('/schemas/proposals/new with no schemas registered', () => {
  it('says so instead of an editor that waits for a contract forever', () => {
    const html = renderToStaticMarkup(createElement(NewProposalView, { state: { kind: 'ready', value: { items: [], total: 0 } }, onRetry: () => {} }))
    expect(html).toContain('No schemas')
    expect(html).not.toContain('aria-busy')
  })
})
