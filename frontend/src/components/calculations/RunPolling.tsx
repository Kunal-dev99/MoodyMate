'use client'

import { useEffect, useRef, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { ApiError, api, call } from '@/api/client'
import { BACKGROUND_RUNS_UI_ENABLED } from '@/config/features'
import { retryText } from '@/lib/retry'
import { followsRun, pollRun, type PollEvent } from './polling'
import { RUN_STATUS_STYLE, type RunDetail, type RunStatus } from './calculations'

/** Where following a run stands, for the notice under it. */
export type PollView =
  | { phase: 'idle' }
  | { phase: 'waiting'; ms: number; attempt: number }
  | { phase: 'retrying'; ms: number; attempt: number; code: string; retryAfterSeconds: number | null }
  | { phase: 'failed'; status: number | null; code: string; message: string; cid: string | null }

/** The notice's state after one poller event (a new run read changes nothing here). */
export function pollView(previous: PollView, event: PollEvent): PollView {
  switch (event.kind) {
    case 'waiting':
      return { phase: 'waiting', ms: event.ms, attempt: event.attempt }
    case 'retrying':
      return {
        phase: 'retrying',
        ms: event.ms,
        attempt: event.attempt,
        code: event.error instanceof ApiError ? event.error.envelope.code : 'network_error',
        retryAfterSeconds: event.retryAfterSeconds,
      }
    case 'failed':
      return event.error instanceof ApiError
        ? { phase: 'failed', status: event.error.status, code: event.error.envelope.code, message: event.error.envelope.message, cid: event.error.correlationId }
        : { phase: 'failed', status: null, code: 'unexpected_error', message: 'Something went wrong.', cid: null }
    case 'run':
      return previous
  }
}

const readRun = (runId: string) => (signal: AbortSignal) =>
  call(() => api.GET('/api/v1/calculation-runs/{run_id}', { params: { path: { run_id: runId } }, signal }))

/**
 * FE-33.1: follows `run` while the server reports it RUNNING (or QUEUED), handing each read to
 * `onRun`, and stops at the first terminal status, on unmount, or for good on a 403/404. Off
 * (NEXT_PUBLIC_BACKGROUND_RUNS_UI=off) it never reads: the page shows the run as loaded, as in
 * Phase 32.
 */
export function useRunPolling(run: RunDetail | null, onRun: (run: RunDetail) => void, enabled: boolean = BACKGROUND_RUNS_UI_ENABLED): PollView {
  const [view, setView] = useState<PollView>({ phase: 'idle' })
  const onRunRef = useRef(onRun)
  onRunRef.current = onRun
  const runRef = useRef(run)
  runRef.current = run
  const runId = run?.run_id ?? null
  const pending = followsRun(run, enabled)

  useEffect(() => {
    const initial = runRef.current
    if (!pending || !runId || !initial) {
      setView({ phase: 'idle' })
      return
    }
    const controller = new AbortController()
    void pollRun(initial, controller.signal, {
      fetchRun: readRun(runId),
      onEvent: (event) => {
        if (controller.signal.aborted) return
        setView((previous) => pollView(previous, event))
        if (event.kind === 'run') onRunRef.current(event.run)
      },
    })
    return () => controller.abort()
  }, [pending, runId])

  return view
}

const seconds = (ms: number) => Math.max(1, Math.ceil(ms / 1000))

/** "Running in the background: checking again in 2 s." — the server's status, never a guess. */
export function RunPollingNotice({ view, testId = 'run-polling' }: { view: PollView; testId?: string }) {
  if (view.phase === 'idle') return null
  return (
    <p className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground" data-testid={testId} data-phase={view.phase}>
      <RefreshCw className="h-3.5 w-3.5" />
      {view.phase === 'waiting' && <span>Running in the background; the server sets the outcome. Checking again in {seconds(view.ms)} s.</span>}
      {view.phase === 'retrying' && (
        <span>
          The server could not answer (<span className="font-mono">{view.code}</span>).{' '}
          {view.retryAfterSeconds !== null ? retryText(view.retryAfterSeconds) : `Checking again in ${seconds(view.ms)} s.`}
        </span>
      )}
      {view.phase === 'failed' && (
        <span>
          Stopped following the run: {view.message} ({view.status !== null && <>HTTP {view.status} · </>}
          <span className="font-mono">{view.code}</span>
          {view.cid && <> · Correlation ID: {view.cid}</>}). Reload the page to read it again.
        </span>
      )}
    </p>
  )
}

/**
 * The result panels (ladder, liquidity) of a run the server has not ended: nothing is read and
 * nothing here is a result. The panel is keyed on the run's status, so it reads its results once
 * the server reports the run ended.
 */
export function ResultsPending({ status, testId }: { status: RunStatus; testId: string }) {
  return (
    <p className="text-sm text-muted-foreground" data-testid={testId} data-status={status}>
      Results appear when the run ends: the server reports it {RUN_STATUS_STYLE[status].label.toLowerCase()}, so nothing here is a result yet.
    </p>
  )
}
