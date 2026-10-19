'use client'

import { useEffect, useMemo, useState } from 'react'
import { Boxes, X } from 'lucide-react'
import { Forbidden } from '@/components/auth/Forbidden'
import { LoadFailed } from '@/components/common/LoadFailed'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import type { Schemas } from '@/api/client'
import { MaskedValue, isMasked } from '@/components/common/MaskedValue'
import { createLoader, type Loaded } from '@/components/common/load'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { PAGE_SIZE, fetchInstruments, type InstrumentPage } from './scenarios'

/**
 * FE-24.2: FUNDING_UNAVAILABLE's instrument picker (D-44 §3). The analyst picks up to
 * `max` instrument business keys of the chosen funding instrument type from the catalogue
 * (GET /instruments, `domain:read`); nothing is typed or suggested. Leaving it empty selects every
 * instrument of the type.
 */
export function InstrumentPicker({
  instrumentType,
  selected,
  masked = [],
  max,
  onChange,
  onRemoveMasked,
  testId,
}: {
  instrumentType: string
  selected: string[]
  /** Picks of a stored shock the server masked for this caller: shown masked, never re-sent. */
  masked?: Schemas['MaskedValue'][]
  max: number | null
  onChange: (keys: string[]) => void
  onRemoveMasked?: (index: number) => void
  testId: string
}) {
  const [offset, setOffset] = useState(0)
  const [state, setState] = useState<Loaded<InstrumentPage>>({ kind: 'loading' })
  const loader = useMemo(() => (instrumentType ? createLoader(fetchInstruments(instrumentType, offset), setState, 'Could not load the instruments') : null), [instrumentType, offset])
  useEffect(() => {
    setOffset(0)
  }, [instrumentType])
  useEffect(() => {
    if (!loader) return
    loader.load()
    return loader.abort
  }, [loader])

  const toggle = (key: string, on: boolean) => onChange(on ? [...selected.filter((k) => k !== key), key] : selected.filter((k) => k !== key))
  const full = max !== null && selected.length >= max

  return (
    <div className="space-y-2" data-testid={testId} data-count={selected.length}>
      {(selected.length > 0 || masked.length > 0) && (
        <ul className="flex flex-wrap gap-1" data-testid="picked-instruments">
          {masked.map((m, i) => (
            <li key={`masked-${i}`} className="inline-flex items-center gap-1 text-xs" data-testid="picked-instrument-masked">
              <MaskedValue value={m} />
              {onRemoveMasked && (
                <button type="button" aria-label="Remove masked instrument" onClick={() => onRemoveMasked(i)}>
                  <X className="h-3 w-3" />
                </button>
              )}
            </li>
          ))}
          {selected.map((k) => (
            <li key={k} className="inline-flex items-center gap-1 rounded-sm border border-border bg-surface-2 px-2 py-0.5 font-mono text-xs">
              {k}
              <button type="button" aria-label={`Remove ${k}`} onClick={() => toggle(k, false)}>
                <X className="h-3 w-3" />
              </button>
            </li>
          ))}
        </ul>
      )}
      <p className="text-xs text-muted-foreground">
        {selected.length} of {max ?? '—'} picked. None picked selects every {instrumentType || 'matching'} instrument.
      </p>
      {!loader ? (
        <p className="text-xs text-muted-foreground" data-testid="picker-needs-type">
          Choose the funding instrument type first.
        </p>
      ) : state.kind === 'loading' ? (
        <LoadingBlock rows={2} />
      ) : state.kind === 'forbidden' ? (
        <Forbidden permission="domain:read" correlationId={state.cid} />
      ) : state.kind === 'failed' ? (
        <LoadFailed icon={Boxes} title="Could not load the instruments" cid={state.cid} onRetry={loader.load} />
      ) : state.kind === 'not_found' ? (
        <p className="text-xs text-muted-foreground">The instrument list was refused.</p>
      ) : state.value.items.length === 0 ? (
        <p className="text-xs text-muted-foreground">No {instrumentType} instrument is in the catalogue.</p>
      ) : (
        <div className="space-y-2">
          <ul className="max-h-48 space-y-1 overflow-y-auto rounded-md border border-border p-2">
            {state.value.items.map((i) => {
              const key = i.business_key
              // A masked business key cannot be picked: the analyst never sees what it is (D-04).
              if (isMasked(key))
                return (
                  <li key={i.instrument_id} className="flex items-center gap-2 text-xs" data-testid="pick-instrument-masked">
                    <Checkbox disabled aria-label="Masked instrument: cannot be picked" />
                    <MaskedValue value={key} />
                  </li>
                )
              const on = selected.includes(key)
              const currency = isMasked(i.currency) ? <MaskedValue value={i.currency} /> : i.currency
              return (
                <li key={i.instrument_id} className="flex items-center gap-2 text-xs">
                  <Checkbox
                    id={`pick-${testId}-${i.instrument_id}`}
                    checked={on}
                    disabled={!on && full}
                    onCheckedChange={(v) => toggle(key, v === true)}
                    data-testid="pick-instrument"
                    data-key={key}
                  />
                  <label htmlFor={`pick-${testId}-${i.instrument_id}`} className="font-mono">
                    {key}
                  </label>
                  <span className="text-muted-foreground">
                    {currency ?? ''} · {i.source_code}
                  </span>
                </li>
              )
            })}
          </ul>
          {state.value.total > PAGE_SIZE && (
            <div className="flex gap-2">
              <Button type="button" size="sm" variant="outline" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
                Previous
              </Button>
              <Button type="button" size="sm" variant="outline" disabled={offset + state.value.items.length >= state.value.total} onClick={() => setOffset(offset + PAGE_SIZE)}>
                Next
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
