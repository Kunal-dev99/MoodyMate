import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { QuarantineStatusPill } from '@/components/ingestion/QuarantineStatusPill'
import { emptyImportDraft, toImportFields } from '@/components/ingestion/imports'
import {
  ISSUE_CODES,
  MANUAL_MOVES,
  QUARANTINE_STATUSES,
  QUARANTINE_STATUS_STYLE,
  SEVERITIES,
  legalMoves,
  outcomeTotal,
  retryHref,
  type QuarantineStatus,
} from '@/components/ingestion/validation'

// The OpenAPI document the types are generated from: pills and filters are tested against its enums (D-23).
const openapi = JSON.parse(readFileSync(fileURLToPath(new URL('../../../../../backend/openapi.json', import.meta.url)), 'utf8'))
const enumOf = (name: string): string[] => [...openapi.components.schemas[name].enum].sort()

describe('validation dashboard (FE-11.1)', () => {
  it('checks that the outcome counts add up to the row count; warnings are not rows', () => {
    const q = { row_count: 25, accepted_records: 20, quarantined_records: 4, rejected_records: 1, warnings: 3 }
    expect(outcomeTotal(q)).toEqual({ sum: 25, rowCount: 25, addsUp: true })
    expect(outcomeTotal({ ...q, accepted_records: 19 }).addsUp).toBe(false)
  })

  it('offers every severity and issue code of the API, no more', () => {
    expect([...SEVERITIES].sort()).toEqual(enumOf('ValidationSeverity'))
    expect([...ISSUE_CODES].sort()).toEqual(enumOf('ValidationCode'))
  })
})

describe('quarantine view (FE-11.2)', () => {
  it('styles exactly the API quarantine statuses; only RESOLVED looks finished', () => {
    expect([...QUARANTINE_STATUSES].sort()).toEqual(enumOf('QuarantineStatus'))
    for (const s of QUARANTINE_STATUSES) {
      const html = renderToStaticMarkup(createElement(QuarantineStatusPill, { status: s }))
      expect(html).toContain(`data-status="${s}"`)
      expect(html).toContain(QUARANTINE_STATUS_STYLE[s].label)
    }
    expect(QUARANTINE_STATUSES.filter((s) => QUARANTINE_STATUS_STYLE[s].className === 'status-published')).toEqual(['RESOLVED'])
  })

  it('offers only the D-20 manual moves the server also allows', () => {
    // D-20 §2 by hand: OPEN→UNDER_REVIEW, UNDER_REVIEW→{REJECTED, RETRY_READY, OPEN}; the rest only by a retry import.
    const pairs = Object.entries(MANUAL_MOVES).flatMap(([from, tos]) => tos.map((to) => `${from}->${to}`))
    expect(pairs.sort()).toEqual(['OPEN->UNDER_REVIEW', 'UNDER_REVIEW->OPEN', 'UNDER_REVIEW->REJECTED', 'UNDER_REVIEW->RETRY_READY'])
    const all = [...QUARANTINE_STATUSES]
    expect(legalMoves({ status: 'OPEN', allowed_dispositions: ['UNDER_REVIEW'] })).toEqual(['UNDER_REVIEW'])
    // Fail closed: a move outside the table is never offered, even if the server listed it.
    expect(legalMoves({ status: 'RETRY_READY', allowed_dispositions: all })).toEqual([])
    expect(legalMoves({ status: 'RESOLVED', allowed_dispositions: all })).toEqual([])
    // And a table move the server does not allow now is not offered either.
    expect(legalMoves({ status: 'UNDER_REVIEW', allowed_dispositions: ['RETRY_READY'] as QuarantineStatus[] })).toEqual(['RETRY_READY'])
  })

  it('"Retry with corrected file" pre-fills the wizard and the upload sends retry_of_import_id', () => {
    const job = { id: 'imp-1', source_id: 'src-1' }
    expect(retryHref(job)).toBe('/ingestion/imports/new?source=src-1&retry_of=imp-1')
    // D-27 §4: on a retry import's own quarantine, the retry targets that import (which holds
    // the items), never the import it retried.
    expect(retryHref({ id: 'imp-2', source_id: 'src-1', retry_of_import_id: 'imp-1' } as { id: string; source_id: string })).toBe(
      '/ingestion/imports/new?source=src-1&retry_of=imp-2',
    )
    const file = new File(['a'], 'fixed.csv')
    expect(toImportFields({ ...emptyImportDraft('src-1', 'imp-1'), file, attested: true }, file, 'synthetic')).toMatchObject({
      source_id: 'src-1',
      retry_of_import_id: 'imp-1',
    })
    expect(toImportFields({ ...emptyImportDraft('src-1'), file, attested: true }, file, 'synthetic')).not.toHaveProperty('retry_of_import_id')
  })
})
