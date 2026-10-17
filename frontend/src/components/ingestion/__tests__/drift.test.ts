import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { ApiError } from '@/api/client'
import { DriftStatusPill, DriftTypePill } from '@/components/ingestion/DriftPills'
import {
  COMMENT_MAX,
  DRIFT_STATUSES,
  DRIFT_STATUS_STYLE,
  DRIFT_TYPES,
  DRIFT_TYPE_STYLE,
  REVIEW_OUTCOMES,
  canReview,
  commentProblem,
  driftProposalHref,
  driftRefusal,
  reviewBody,
  reviewOutcomes,
  thresholdFields,
  type DriftStatus,
} from '@/components/ingestion/drift'

// The OpenAPI document the types are generated from: pills are tested against its enums (D-23).
const openapi = JSON.parse(readFileSync(fileURLToPath(new URL('../../../../../backend/openapi.json', import.meta.url)), 'utf8'))
const schemas = openapi.components.schemas
const enumOf = (name: string): string[] => [...schemas[name].enum].sort()

const envelope = (code: string) => ({ code, message: `${code} message`, details: null, correlation_id: 'cid-9', retryable: false })

describe('drift pills (FE-12.2, D-23)', () => {
  it('styles exactly the API drift types', () => {
    expect([...DRIFT_TYPES].sort()).toEqual(enumOf('DriftType'))
    for (const t of DRIFT_TYPES) {
      const html = renderToStaticMarkup(createElement(DriftTypePill, { type: t }))
      expect(html).toContain(`data-drift-type="${t}"`)
      expect(html).toContain(DRIFT_TYPE_STYLE[t].label)
    }
  })

  it('styles exactly the API drift statuses; OPEN never looks finished', () => {
    expect([...DRIFT_STATUSES].sort()).toEqual(enumOf('DriftStatus'))
    for (const s of DRIFT_STATUSES) {
      const html = renderToStaticMarkup(createElement(DriftStatusPill, { status: s }))
      expect(html).toContain(`data-status="${s}"`)
      expect(html).toContain(DRIFT_STATUS_STYLE[s].label)
    }
    expect(DRIFT_STATUS_STYLE.OPEN.className).not.toBe('status-published')
  })

  it('offers exactly the review outcomes the API accepts', () => {
    expect([...REVIEW_OUTCOMES].sort()).toEqual([...schemas.DriftReviewIn.properties.outcome.enum].sort())
  })
})

describe('drift review (FE-12.2)', () => {
  const both = ['ACKNOWLEDGED', 'ACTION_REQUIRED'] as DriftStatus[]

  it('offers a review only for OPEN events, and only outcomes the server also allows', () => {
    expect(reviewOutcomes({ status: 'OPEN', allowed_outcomes: both })).toEqual(['ACKNOWLEDGED', 'ACTION_REQUIRED'])
    expect(reviewOutcomes({ status: 'OPEN', allowed_outcomes: ['ACKNOWLEDGED'] })).toEqual(['ACKNOWLEDGED'])
    // Fail closed: a reviewed event offers nothing even if the server listed outcomes, and
    // OPEN is never a review outcome.
    expect(reviewOutcomes({ status: 'ACKNOWLEDGED', allowed_outcomes: both })).toEqual([])
    expect(reviewOutcomes({ status: 'ACTION_REQUIRED', allowed_outcomes: both })).toEqual([])
    expect(reviewOutcomes({ status: 'OPEN', allowed_outcomes: ['OPEN'] })).toEqual([])
    expect(canReview({ status: 'OPEN', allowed_outcomes: [] })).toBe(false)
    expect(canReview({ status: 'OPEN', allowed_outcomes: both })).toBe(true)
  })

  it('requires a comment of 1 to 1000 characters after trimming, like DriftReviewIn', () => {
    expect(schemas.DriftReviewIn.required).toEqual(expect.arrayContaining(['outcome', 'comment']))
    expect(schemas.DriftReviewIn.properties.comment.maxLength).toBe(COMMENT_MAX)
    expect(commentProblem('')).toMatch(/required/)
    expect(commentProblem('   \n ')).toMatch(/required/)
    expect(commentProblem('x')).toBeNull()
    expect(commentProblem('x'.repeat(COMMENT_MAX))).toBeNull()
    expect(commentProblem('x'.repeat(COMMENT_MAX + 1))).toMatch(/1000/)
    expect(reviewBody(null, 'Expected: new flag column.')).toBeNull()
    expect(reviewBody('ACKNOWLEDGED', '  ')).toBeNull()
    expect(reviewBody('ACKNOWLEDGED', '  Expected: new flag column.  ')).toEqual({ outcome: 'ACKNOWLEDGED', comment: 'Expected: new flag column.' })
  })

  it('"Propose schema change" is the Phase 5 new-proposal route the server hints, schema prefilled only', () => {
    const id = '2f1d6c1e-6a39-4d0e-9f0b-3c2b1a000001'
    // The server's ProposalLink.href format (backend: f"/schemas/proposals/new?schema={id}").
    expect(schemas.ProposalLink.properties.href.description).toContain('/schemas/proposals/new?schema={id}')
    expect(driftProposalHref({ schema_id: id })).toBe(`/schemas/proposals/new?schema=${id}`)
    // That route exists and its editor (FE-14.3: moved out of the page) prefills the schema
    // from ?schema= (and nothing else from the query), else the first schema listed.
    const page = readFileSync(join(__dirname, '..', '..', '..', 'app', '(platform)', 'schemas', 'proposals', 'new', 'page.tsx'), 'utf8')
    expect(page).toContain('<NewProposalView ')
    expect(page).not.toMatch(/params\.get\(/)
    const editor = readFileSync(join(__dirname, '..', '..', 'schemas', 'ProposalEditor.tsx'), 'utf8')
    expect(editor).toContain("useState(() => params.get('schema') || schemas[0]?.id || '')")
    expect(editor.match(/params\.get\(/g)).toHaveLength(1)
  })

  it('reads the PROVISIONAL thresholds as text', () => {
    expect(thresholdFields({ null_rate_threshold: '0.10', range_threshold: '0.25', provisional: true })).toEqual({ nullRate: '0.10', range: '0.25', provisional: true })
    expect(thresholdFields({})).toEqual({ nullRate: null, range: null, provisional: false })
  })

  it('titles the documented review refusals and keeps the envelope', () => {
    for (const [status, code, title] of [
      [409, 'already_reviewed', 'This drift event was reviewed already'],
      [409, 'ingestion_disabled', 'Ingestion changes are switched off'],
      [403, 'forbidden', 'You may not review drift'],
      [404, 'not_found', 'Drift event not found'],
      [422, 'validation_error', 'Some values were rejected'],
    ] as const) {
      expect(driftRefusal(new ApiError(status, envelope(code)), 'fallback')).toEqual({ title, status, code, message: `${code} message`, cid: 'cid-9' })
    }
    expect(driftRefusal(new Error('boom'), 'fallback')).toMatchObject({ title: 'fallback', status: null, code: 'unexpected_error' })
  })
})

describe('drift code writes only the review (FE-12.2)', () => {
  const root = join(__dirname, '..', '..', '..')
  const calls = (file: string) => readFileSync(join(root, file), 'utf8').match(/api\.[A-Z]+\('[^']+'/g) ?? []

  it('the drift tab only reads; the dialog only posts the review', () => {
    expect(new Set(calls('components/ingestion/ImportDriftTab.tsx'))).toEqual(new Set(["api.GET('/api/v1/imports/{import_id}/drift'"]))
    expect(new Set(calls('components/ingestion/DriftReviewDialog.tsx'))).toEqual(new Set(["api.POST('/api/v1/drift-events/{drift_event_id}/review'"]))
  })
})
