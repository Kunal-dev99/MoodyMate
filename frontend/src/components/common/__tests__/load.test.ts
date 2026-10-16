import { afterEach, describe, expect, it, vi } from 'vitest'

const toasts = vi.hoisted(() => [] as { title?: string }[])
vi.mock('@/components/ui/use-toast', () => ({ toast: (t: { title?: string }) => toasts.push(t) }))

const { ApiError, call } = await import('@/api/client')
const { startLoad } = await import('@/components/common/load')

const flush = () => new Promise((r) => setTimeout(r, 0))
const envelope = (code: string) => ({ code, message: code, details: null, correlation_id: 'cid-1', retryable: false })

function handlers() {
  return { onReady: vi.fn(), onFailure: vi.fn(), failureTitle: 'Could not load' }
}

describe('page-level loads end in an error state, never an endless loading block (D10)', () => {
  afterEach(() => {
    toasts.length = 0
  })

  it('a fetch that rejects with a TypeError reports a retryable failure', async () => {
    const h = handlers()
    startLoad(() => Promise.reject(new TypeError('Failed to fetch')), h)
    await flush()
    expect(h.onReady).not.toHaveBeenCalled()
    expect(h.onFailure).toHaveBeenCalledWith({ kind: 'failed', status: null, cid: null })
  })

  it('a network failure wrapped by call() is a retryable failure, not "not found"', async () => {
    const h = handlers()
    startLoad(() => call(() => Promise.reject(new TypeError('Failed to fetch'))), h)
    await flush()
    expect(h.onFailure).toHaveBeenCalledWith({ kind: 'failed', status: 0, cid: null })
  })

  it('keeps 403 as forbidden and 404 as not found; a 503 stays retryable', async () => {
    const h = handlers()
    startLoad(() => Promise.reject(new ApiError(403, envelope('forbidden'))), h)
    await flush()
    expect(h.onFailure).toHaveBeenLastCalledWith({ kind: 'forbidden', status: 403, cid: 'cid-1' })
    startLoad(() => Promise.reject(new ApiError(404, envelope('not_found'))), h)
    await flush()
    expect(h.onFailure).toHaveBeenLastCalledWith({ kind: 'not_found', status: 404, cid: 'cid-1' })
    startLoad(() => Promise.reject(new ApiError(503, { ...envelope('unavailable'), retryable: true })), h)
    await flush()
    expect(h.onFailure).toHaveBeenLastCalledWith({ kind: 'failed', status: 503, cid: 'cid-1' })
  })

  it('an aborted request shows no error', async () => {
    const h = handlers()
    let reject: (e: unknown) => void = () => {}
    const controller = startLoad(() => new Promise((_, r) => (reject = r)), h)
    controller.abort()
    reject(new TypeError('Failed to fetch'))
    await flush()
    expect(h.onFailure).not.toHaveBeenCalled()
    expect(toasts).toHaveLength(0)
  })

  it('"Try again" re-invokes the fetch and cancels the previous attempt', async () => {
    const h = handlers()
    const fetcher = vi
      .fn<(signal: AbortSignal) => Promise<string>>()
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce('loaded')
    const first = startLoad(fetcher, h)
    await flush()
    expect(h.onFailure).toHaveBeenCalledTimes(1)
    const second = startLoad(fetcher, h, first)
    await flush()
    expect(fetcher).toHaveBeenCalledTimes(2)
    expect(first.signal.aborted).toBe(true)
    expect(second.signal.aborted).toBe(false)
    expect(h.onReady).toHaveBeenCalledWith('loaded')
  })
})

describe('the retryable error state (D10)', () => {
  it('renders the failure with a "Try again" action that re-runs the load', async () => {
    const { createElement, isValidElement } = await import('react')
    const { renderToStaticMarkup } = await import('react-dom/server')
    const { Database } = await import('lucide-react')
    const { LoadFailed } = await import('@/components/common/LoadFailed')
    const onRetry = vi.fn()
    const props = { icon: Database, title: 'The source could not be loaded', cid: 'cid-9', onRetry }

    const html = renderToStaticMarkup(createElement(LoadFailed, props))
    expect(html).toContain('The source could not be loaded')
    expect(html).toContain('Correlation ID: cid-9')
    expect(html).toContain('Try again')
    expect(html).not.toContain('aria-busy')

    // The action's click handler is the caller's retry.
    const action = (LoadFailed(props).props as { action: unknown }).action
    expect(isValidElement(action)).toBe(true)
    ;(action as { props: { onClick: () => void } }).props.onClick()
    expect(onRetry).toHaveBeenCalledTimes(1)
  })
})
