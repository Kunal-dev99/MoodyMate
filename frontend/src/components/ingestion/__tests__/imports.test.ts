import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { ApiError, dataClassOf, multipart, toFormData } from '@/api/client'
import { ImportStatusPill } from '@/components/ingestion/ImportStatusPill'
import {
  IMPORT_ERROR_CODES,
  IMPORT_MAX_BYTES,
  IMPORT_STATUSES,
  IMPORT_STATUS_STYLE,
  attestationRequired,
  buildImportFormData,
  emptyImportDraft,
  importErrorTitle,
  importProblems,
  isReplay,
  newIdempotencyKey,
  sizeProblem,
  type CreateImportBody,
  type ImportDraft,
  type ImportStatus,
} from '@/components/ingestion/imports'

// The OpenAPI document the types are generated from: the pill is tested against its enum (D-23).
const openapi = JSON.parse(readFileSync(fileURLToPath(new URL('../../../../../backend/openapi.json', import.meta.url)), 'utf8'))
const enumOf = (name: string): string[] => [...openapi.components.schemas[name].enum].sort()

const SOURCE = '6f1c0f7e-0000-4000-8000-000000000002'

function envelope(code: string) {
  return { code, message: 'm', details: null, correlation_id: 'cid-1', retryable: false }
}

function draftWith(file: File, patch: Partial<ImportDraft> = {}): ImportDraft {
  return { ...emptyImportDraft(SOURCE), file, attested: true, ...patch }
}

describe('ImportStatusPill (FE-8.2)', () => {
  it('maps exactly the import_job status enum of the generated API', () => {
    expect([...IMPORT_STATUSES].sort()).toEqual(enumOf('ImportStatus'))
    expect(enumOf('ImportStatus')).toEqual(
      [
        'CANCELLED',
        'COMPLETED',
        'COMPLETED_WITH_WARNINGS',
        'CREATED',
        'FAILED',
        'MAPPING_REQUIRED',
        'PROFILING',
        'QUARANTINED',
        'RECEIVED',
        'VALIDATING',
      ].sort(),
    )
  })

  it('renders every status with a label and a design-system status class', () => {
    for (const status of enumOf('ImportStatus') as ImportStatus[]) {
      const html = renderToStaticMarkup(createElement(ImportStatusPill, { status }))
      expect(html).toContain(`data-status="${status}"`)
      expect(html).toContain(IMPORT_STATUS_STYLE[status].label)
      expect(IMPORT_STATUS_STYLE[status].className).toMatch(/^status-(draft|building|testing|published|failed)$/)
    }
  })

  it('never shows an unfinished or failed import as success', () => {
    const success = IMPORT_STATUSES.filter((s) => IMPORT_STATUS_STYLE[s].className === 'status-published')
    expect(success).toEqual(['COMPLETED'])
    expect(IMPORT_STATUS_STYLE.RECEIVED.className).toBe('status-building')
  })
})

describe('client-side size pre-check (D-16)', () => {
  it('allows up to 10 MiB and refuses anything larger or empty', () => {
    expect(IMPORT_MAX_BYTES).toBe(10 * 1024 * 1024)
    expect(sizeProblem(1)).toBeNull()
    expect(sizeProblem(IMPORT_MAX_BYTES)).toBeNull()
    expect(sizeProblem(IMPORT_MAX_BYTES + 1)).toMatch(/limit is 10\.00 MiB/)
    expect(sizeProblem(0)).toBe('The file is empty.')
  })

  it('blocks the upload of an oversize file', () => {
    const big = new File([new Uint8Array(IMPORT_MAX_BYTES + 1)], 'big.csv')
    expect(importProblems(draftWith(big), 'synthetic')).toEqual([expect.stringMatching(/limit is 10\.00 MiB/)])
    const ok = new File(['a,b\n1,2\n'], 'ok.csv')
    expect(importProblems(draftWith(ok), 'synthetic')).toEqual([])
  })
})

describe('import form checks', () => {
  const file = new File(['a\n1\n'], 'x.csv')

  it('asks for the attestation unless the environment holds client data (fail closed)', () => {
    expect(attestationRequired('synthetic')).toBe(true)
    expect(attestationRequired(null)).toBe(true)
    expect(attestationRequired('client')).toBe(false)
    expect(importProblems(draftWith(file, { attested: false }), 'synthetic')).toContain('Confirm that the file holds synthetic data only.')
    expect(importProblems(draftWith(file, { attested: false }), null)).toContain('Confirm that the file holds synthetic data only.')
    expect(importProblems(draftWith(file, { attested: false }), 'client')).toEqual([])
  })

  it('checks the source, the file, the row count and decimal control totals', () => {
    expect(importProblems(emptyImportDraft(), 'client')).toEqual(['Choose a source.', 'Choose a file.'])
    expect(importProblems(draftWith(file, { expectedRowCount: '-1' }), 'synthetic')).toEqual([
      'Expected row count: a whole number, 0 or more.',
    ])
    expect(importProblems(draftWith(file, { expectedRowCount: '2.5' }), 'synthetic')).toHaveLength(1)
    expect(importProblems(draftWith(file, { expectedRowCount: ' 25 ' }), 'synthetic')).toEqual([])
    expect(importProblems(draftWith(file, { controlTotals: { notional: '1e5' } }), 'synthetic')).toEqual([
      'Control total notional: a decimal number such as 1234.50.',
    ])
    expect(importProblems(draftWith(file, { controlTotals: { notional: '-1234.50', other: '' } }), 'synthetic')).toEqual([])
  })
})

describe('import form-data builder', () => {
  const file = new File(['instrument_id\nINS-1\n'], 'instrument.csv', { type: 'text/csv' })

  it('sends the source, the file with its name and the attestation as text', async () => {
    const form = buildImportFormData(draftWith(file), file, 'synthetic')
    expect([...form.keys()].sort()).toEqual(['file', 'source_id', 'synthetic_attested'])
    expect(form.get('source_id')).toBe(SOURCE)
    expect(form.get('synthetic_attested')).toBe('true')
    const part = form.get('file') as File
    expect(part.name).toBe('instrument.csv')
    expect(await part.text()).toBe('instrument_id\nINS-1\n')
  })

  it('adds the expected row count and the declared control totals as a JSON object', () => {
    const form = buildImportFormData(
      draftWith(file, { expectedRowCount: ' 25 ', controlTotals: { notional: ' 1234.50 ', unused: '  ' } }),
      file,
      'synthetic',
    )
    expect(form.get('expected_row_count')).toBe('25')
    expect(JSON.parse(form.get('control_totals') as string)).toEqual({ notional: '1234.50' })
  })

  it('never attests for client data, and attests false when unticked', () => {
    expect(buildImportFormData(draftWith(file), file, 'client').get('synthetic_attested')).toBe('false')
    expect(buildImportFormData(draftWith(file, { attested: false }), file, 'synthetic').get('synthetic_attested')).toBe('false')
  })
})

describe('multipart through the generated client types', () => {
  it('keeps the typed body and serialises it as FormData', () => {
    const file = new File(['x'], 'a.csv')
    const { body, bodySerializer } = multipart<CreateImportBody>({ source_id: SOURCE, file, synthetic_attested: true })
    expect(body.source_id).toBe(SOURCE)
    const form = bodySerializer()
    expect(form).toBeInstanceOf(FormData)
    expect(form.get('synthetic_attested')).toBe('true')
    expect((form.get('file') as File).name).toBe('a.csv')
  })

  it('leaves out undefined and null fields and refuses objects', () => {
    const form = toFormData({ a: 'x', b: undefined, c: null, d: 0, e: false })
    expect([...form.entries()]).toEqual([
      ['a', 'x'],
      ['d', '0'],
      ['e', 'false'],
    ])
    expect(() => toFormData({ bad: { nested: 1 } })).toThrow(TypeError)
  })
})

describe('responses', () => {
  it('reads X-Data-Class, null when missing or unknown', () => {
    expect(dataClassOf(new Response(null, { headers: { 'X-Data-Class': 'synthetic' } }))).toBe('synthetic')
    expect(dataClassOf(new Response(null, { headers: { 'X-Data-Class': 'client' } }))).toBe('client')
    expect(dataClassOf(new Response(null, { headers: { 'X-Data-Class': 'other' } }))).toBeNull()
    expect(dataClassOf(new Response(null))).toBeNull()
  })

  it('tells an idempotent replay (200) from a new import (201)', () => {
    expect(isReplay(new Response('{}', { status: 200 }))).toBe(true)
    expect(isReplay(new Response('{}', { status: 201 }))).toBe(false)
  })

  it('generates a fresh visible-ASCII Idempotency-Key per attempt', () => {
    const a = newIdempotencyKey()
    const b = newIdempotencyKey()
    expect(a).not.toBe(b)
    expect(a).toMatch(/^[\x21-\x7e]{1,255}$/)
  })
})

describe('upload error titles', () => {
  // Every documented POST /imports error code (BE-8.4, D-16 adapters).
  const documented = [
    'forbidden',
    'ingestion_disabled',
    'payload_too_large',
    'synthetic_attestation_required',
    'idempotency_conflict',
    'not_found',
    'unknown_control_total_column',
    'no_current_contract',
    'no_active_version',
    'too_many_rows',
    'too_many_columns',
    'cell_too_long',
    'content_too_large',
    'xlsx_uncompressed_too_large',
    'xlsx_compression_ratio_too_high',
    'unsupported_file_type',
    'format_mismatch',
    'legacy_xls_not_supported',
    'macro_enabled_workbook',
    'encoding_not_utf8',
    'invalid_character',
    'missing_header',
    'invalid_header',
    'csv_malformed',
    'json_malformed',
    'json_invalid_shape',
    'json_nested_value',
    'sheet_not_found',
    'xlsx_invalid',
    'artifact_integrity_error',
    'validation_error',
    // Phase 11: a retry upload (D-20 §3).
    'retry_source_mismatch',
    'retry_not_applicable',
  ]

  it('has a specific title for every documented code', () => {
    expect([...IMPORT_ERROR_CODES].sort()).toEqual([...documented].sort())
    for (const code of documented) {
      expect(importErrorTitle(new ApiError(422, envelope(code)), 'FALLBACK')).not.toBe('FALLBACK')
    }
    expect(importErrorTitle(new ApiError(413, envelope('payload_too_large')))).toBe('The file is too large')
  })

  it('covers every UploadRefusal code the backend defines', () => {
    // Read from the backend source, so a new refusal code fails here until it has a title.
    const constants = readFileSync(fileURLToPath(new URL('../../../../../backend/app/ingestion/constants.py', import.meta.url)), 'utf8')
    const block = constants.slice(constants.indexOf('class UploadRefusal'))
    const end = block.search(/\n(?:class |[A-Z_]+ = |def )/)
    const codes = [...block.slice(0, end === -1 ? undefined : end).matchAll(/^ {4}[A-Z0-9_]+ = "([a-z0-9_]+)"/gm)].map((m) => m[1])
    expect(codes.length).toBeGreaterThanOrEqual(25)
    for (const code of codes) expect(IMPORT_ERROR_CODES).toContain(code)
  })

  it('falls back for unknown codes and non-API errors', () => {
    expect(importErrorTitle(new ApiError(500, envelope('internal_error')))).toBe('Could not upload the file')
    expect(importErrorTitle(new Error('boom'), 'X')).toBe('X')
  })
})
