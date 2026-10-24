'use client'

import { useCallback, useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { fetchCommentMax } from './scenarios'

/**
 * FE-35.3 (Q20): the comment bound GET config/limits serves (`scenario.max_comment_length`).
 * `max` is null until it is read; a failed read sets `failed` and `retry` asks again. Fail closed:
 * a comment step cannot be sent before the served bound is known (no literal stands in for it).
 */
export type CommentLimit = { max: number | null; failed: boolean; retry: () => void }

export function useCommentMax(wanted: boolean): CommentLimit {
  const [max, setMax] = useState<number | null>(null)
  const [failed, setFailed] = useState(false)
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    if (!wanted) return
    let live = true
    fetchCommentMax().then(
      (served) => {
        if (live) setMax(served)
      },
      () => {
        if (live) setFailed(true)
      },
    )
    return () => {
      live = false
    }
  }, [wanted, attempt])
  const retry = useCallback(() => {
    setFailed(false)
    setAttempt((a) => a + 1)
  }, [])
  return { max, failed, retry }
}

/** While the served comment bound is unknown: reading it, or the failed read with "Try again". */
export function CommentLimitState({ limit }: { limit: CommentLimit }) {
  if (limit.max !== null) return null
  if (!limit.failed)
    return (
      <p className="text-xs text-muted-foreground" data-testid="comment-limit-loading">
        Reading the comment limit from the server…
      </p>
    )
  return (
    <div className="flex items-center gap-2 text-xs text-destructive" data-testid="comment-limit-failed">
      <span>Could not read the comment limit (GET config/limits), so this step cannot be sent yet.</span>
      <Button size="sm" variant="outline" onClick={limit.retry}>
        Try again
      </Button>
    </div>
  )
}
