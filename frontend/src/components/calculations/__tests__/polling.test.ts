import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/api/client'
import type { RunDetail, RunStatus } from '@/components/calculations/calculations'
import { POLL_FIRST_MS, POLL_MAX_MS, isPending, isRetryableRead, pollDelay, pollRun, type PollEvent } from '@/components/calculations/polling'
import { RunPollingNotice, pollView, type PollView } from '@/components/calculations/RunPolling'

/*
 * FE-33.1: following a background run (D-59 §2). The poller is driven without timers: `sleep`
 * records each wait and returns at once, and `fetchRun` answers from a script of server reads.
 */

const RUN_ID = '66666666-6666-4666-8666-666666666666'
const CID = 'cid-33'

// Only the fields the poller reads; the rest of the generated RunDetail is never inspected.
const run = (status: RunStatus, runId = RUN_ID) => ({ run_id: runId, status }) as unknown as RunDetail

const refusal = (status: number, code: string, retryAfter: number | null = null, retryable = true) =>
  new ApiError(status, { code, message: `${code} message`, details: null, correlation_id: CID, retryable }, retryAfter)

/** A poller run against scripted reads: each entry is a run the server returns or an error it throws. */
async function drive(initial: RunDetail, reads: (RunDetail | Error)[], controller = new AbortController()) {
  const waits: number[] = []
  const events: PollEvent[] = []
  const fetchRun = vi.fn(async () => {
    const next = reads.shift()
    if (!next) throw new Error('the script ran out of reads')
    if (next instanceof Error) throw next
    return next
  })
  const result = await pollRun(initial, controller.signal, {
    fetchRun,
    onEvent: (e) => events.push(e),
    sleep: async (ms) => {
      waits.push(ms)
    },
  })
  return { result, waits, events, fetchRun }
}

describe('FE-33.1 polling a background run', () => {
  it('backs off from 0.5 s, doubling, and never waits more than 5 s between reads', () => {
    expect([0, 1, 2, 3, 4, 5, 20].map(pollDelay)).toEqual([500, 1000, 2000, 4000, 5000, 5000, 5000])
    expect(POLL_FIRST_MS).toBe(500)
    expect(POLL_MAX_MS).toBe(5000)
  })

  it('reads until the server reports a terminal status, and reports every read as the server returned it', async () => {
    const { result, waits, events, fetchRun } = await drive(run('RUNNING'), [run('RUNNING'), run('RUNNING'), run('RUNNING'), run('RUNNING'), run('RUNNING'), run('SUCCEEDED')])
    expect(result?.status).toBe('SUCCEEDED')
    expect(fetchRun).toHaveBeenCalledTimes(6)
    expect(waits).toEqual([500, 1000, 2000, 4000, 5000, 5000])
    // Never a success before the server says so: every RUNNING read is reported RUNNING.
    const statuses = events.flatMap((e) => (e.kind === 'run' ? [e.run.status] : []))
    expect(statuses).toEqual(['RUNNING', 'RUNNING', 'RUNNING', 'RUNNING', 'RUNNING', 'SUCCEEDED'])
    expect(events.filter((e) => e.kind === 'waiting').map((e) => (e.kind === 'waiting' ? e.ms : 0))).toEqual(waits)
  })

  it.each(['FAILED', 'BLOCKED', 'SUCCEEDED_WITH_WARNINGS'] as const)('stops at %s, as the server ended it', async (status) => {
    const { result, fetchRun } = await drive(run('RUNNING'), [run(status)])
    expect(result?.status).toBe(status)
    expect(fetchRun).toHaveBeenCalledTimes(1)
  })

  it('does not read a run the server already ended (the Phase 32 synchronous answer)', async () => {
    const { result, waits, events, fetchRun } = await drive(run('SUCCEEDED'), [])
    expect(result?.status).toBe('SUCCEEDED')
    expect(fetchRun).not.toHaveBeenCalled()
    expect(waits).toEqual([])
    expect(events).toEqual([])
    expect(isPending('QUEUED')).toBe(true) // reserved, never taken for an outcome
  })

  it('a 503 workers_busy or 429 rate_limited read waits at least the server’s Retry-After, then reads again', async () => {
    const { result, waits, events } = await drive(run('RUNNING'), [refusal(503, 'workers_busy', 7), refusal(429, 'rate_limited', 30), run('SUCCEEDED')])
    expect(result?.status).toBe('SUCCEEDED')
    // 0.5 s first; then the 7 s and 30 s the server asked for (both above the backoff).
    expect(waits).toEqual([500, 7000, 30000])
    const retries = events.filter((e) => e.kind === 'retrying')
    expect(retries.map((e) => (e.kind === 'retrying' ? [e.ms, e.retryAfterSeconds] : null))).toEqual([
      [7000, 7],
      [30000, 30],
    ])
  })

  it('a retryable read without Retry-After keeps the backoff', async () => {
    const { waits } = await drive(run('RUNNING'), [new ApiError(0, { code: 'network_error', message: 'down', details: null, correlation_id: null, retryable: true }), run('SUCCEEDED')])
    expect(waits).toEqual([500, 1000])
  })

  it('a 403 or 404 stops following for good (D-04): reported as failed, nothing assumed', async () => {
    for (const err of [refusal(403, 'forbidden', null, false), refusal(404, 'not_found', null, false)]) {
      const { result, events, fetchRun } = await drive(run('RUNNING'), [err, run('SUCCEEDED')])
      expect(result).toBeNull()
      expect(fetchRun).toHaveBeenCalledTimes(1)
      expect(events.at(-1)).toEqual({ kind: 'failed', error: err })
    }
    expect(isRetryableRead(refusal(403, 'forbidden', null, true))).toBe(false)
    expect(isRetryableRead(new TypeError('boom'))).toBe(false)
  })

  it('an answer for another run is never taken for this one', async () => {
    const { result, events } = await drive(run('RUNNING'), [run('SUCCEEDED', 'another-run')])
    expect(result).toBeNull()
    expect(events.at(-1)?.kind).toBe('failed')
  })

  it('stops when aborted (the page was left), without reading again', async () => {
    const controller = new AbortController()
    const fetchRun = vi.fn(async () => run('RUNNING'))
    const result = await pollRun(run('RUNNING'), controller.signal, {
      fetchRun,
      onEvent: () => {},
      sleep: async () => {
        if (fetchRun.mock.calls.length === 2) controller.abort()
      },
    })
    expect(fetchRun).toHaveBeenCalledTimes(2)
    expect(result?.status).toBe('RUNNING')
  })
})

describe('FE-33.1 the polling notice', () => {
  const html = (view: PollView) => renderToStaticMarkup(createElement(RunPollingNotice, { view }))

  it('says when it reads again, and shows the retry time of a refusal', () => {
    expect(html({ phase: 'idle' })).toBe('')
    let view = pollView({ phase: 'idle' }, { kind: 'waiting', ms: 2000, attempt: 2 })
    expect(html(view)).toContain('data-phase="waiting"')
    expect(html(view)).toContain('Checking again in 2 s.')
    view = pollView(view, { kind: 'retrying', ms: 30000, attempt: 3, error: refusal(429, 'rate_limited', 30), retryAfterSeconds: 30 })
    expect(view).toMatchObject({ phase: 'retrying', code: 'rate_limited', retryAfterSeconds: 30 })
    expect(html(view)).toContain('Retry in 30 s.')
    expect(pollView(view, { kind: 'run', run: run('RUNNING') })).toBe(view)
  })

  it('a stop shows the server’s status, code and correlation ID', () => {
    const view = pollView({ phase: 'idle' }, { kind: 'failed', error: refusal(403, 'forbidden', null, false) })
    expect(view).toEqual({ phase: 'failed', status: 403, code: 'forbidden', message: 'forbidden message', cid: CID })
    const out = html(view)
    expect(out).toContain('HTTP 403')
    expect(out).toContain(`Correlation ID: ${CID}`)
  })
})
