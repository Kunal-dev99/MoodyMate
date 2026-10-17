import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { ApiError } from '@/api/client'
import { ContractStatusPill } from '@/components/ingestion/ContractStatusPill'
import {
  CONTRACT_STATUS_STYLE,
  DELIMITERS,
  FORMATS,
  SOURCE_KINDS,
  contractProblems,
  draftFromContract,
  emptySourceDraft,
  errorTitle,
  newestFirst,
  sourceProblems,
  toContractIn,
  toSourceCreate,
  type Contract,
  type SourceDraft,
} from '@/components/ingestion/sources'
import { adminWorkspaces, mainWorkspaces } from '@/config/navigation'

// The OpenAPI document the types are generated from: pills are tested against its enums (D-23).
const openapi = JSON.parse(readFileSync(fileURLToPath(new URL('../../../../../backend/openapi.json', import.meta.url)), 'utf8'))
const enumOf = (name: string): string[] => [...openapi.components.schemas[name].enum].sort()

const SCHEMA = '6f1c0f7e-0000-4000-8000-000000000001'

function validDraft(): SourceDraft {
  const d = emptySourceDraft('Dev Data steward')
  return {
    ...d,
    code: 'loan_book',
    name: ' Loan book ',
    contract: {
      schemaId: SCHEMA,
      columns: [
        { name: ' loan_id ', required: true, businessKey: true, controlTotal: false },
        { name: 'balance', required: true, businessKey: false, controlTotal: true },
        { name: 'note', required: false, businessKey: false, controlTotal: false },
      ],
      changeSummary: '',
    },
  }
}

const contract = (extra: Partial<Contract>): Contract => ({
  id: 'c1',
  version: 1,
  status: 'PROVISIONAL',
  schema_id: SCHEMA,
  schema_code: 'instrument',
  expected_columns: [
    { name: 'loan_id', required: true },
    { name: 'balance', required: false },
  ],
  business_key_columns: ['loan_id'],
  control_total_columns: ['balance'],
  content_sha256: 'a'.repeat(64),
  supersedes_id: null,
  change_summary: null,
  created_by: 'user:x',
  created_at: '2026-10-16T00:00:00Z',
  ...extra,
})

describe('ingestion workspace navigation (FE-7.1)', () => {
  it('is live, no longer planned, and needs source:read', () => {
    const ingestion = [...mainWorkspaces, ...adminWorkspaces].find((w) => w.slug === 'ingestion')
    expect(ingestion?.planned).toBeUndefined()
    expect(ingestion?.permission).toBe('source:read')
  })
  it('leaves every other planned workspace planned', () => {
    // FE-14.1 made `domain` live (planned again with NEXT_PUBLIC_DOMAIN_UI=off), and FE-15.1
    // `calculations` (planned again with NEXT_PUBLIC_CALCULATIONS_UI=off).
    const planned = mainWorkspaces.filter((w) => w.planned).map((w) => w.slug)
    expect(planned).toEqual(['scenarios', 'reports', 'assistant'])
  })
})

describe('source contract status pills (FE-7.2)', () => {
  it('style exactly the backend contract statuses', () => {
    expect(Object.keys(CONTRACT_STATUS_STYLE).sort()).toEqual(enumOf('ContractStatus'))
  })
  it('cover every source kind, format and delimiter the API defines', () => {
    expect(Object.keys(SOURCE_KINDS).sort()).toEqual(enumOf('SourceKind'))
    expect([...FORMATS].sort()).toEqual(enumOf('SourceFormat'))
    expect(Object.keys(DELIMITERS).sort()).toEqual(enumOf('CsvDelimiter'))
  })
  it('offer only FILE_DROP as usable (D-23)', () => {
    expect(Object.entries(SOURCE_KINDS).filter(([, k]) => k.usable).map(([kind]) => kind)).toEqual(['FILE_DROP'])
  })
  it('render the label with the status as data', () => {
    const html = renderToStaticMarkup(createElement(ContractStatusPill, { status: 'SUPERSEDED' }))
    expect(html).toContain('data-status="SUPERSEDED"')
    expect(html).toContain('>Superseded<')
    expect(renderToStaticMarkup(createElement(ContractStatusPill, { status: 'PROVISIONAL' }))).toContain('>Provisional<')
  })
})

describe('source registration form (FE-7.2)', () => {
  it('accepts a complete draft', () => {
    expect(sourceProblems(validDraft())).toEqual([])
  })
  it('builds the POST /sources body: trimmed, CSV delimiter only, no provisional flag', () => {
    const body = toSourceCreate(validDraft())
    expect(body).toEqual({
      code: 'loan_book',
      name: 'Loan book',
      description: null,
      kind: 'FILE_DROP',
      profile: {
        format: 'CSV',
        encoding: 'UTF-8',
        delimiter: ',',
        sheet_name: null,
        cadence: null,
        owner: 'Dev Data steward',
        default_classification: 'internal',
      },
      contract: {
        schema_id: SCHEMA,
        expected_columns: [
          { name: 'loan_id', required: true },
          { name: 'balance', required: true },
          { name: 'note', required: false },
        ],
        business_key_columns: ['loan_id'],
        control_total_columns: ['balance'],
        change_summary: null,
      },
    })
  })
  it('sends a sheet name only for XLSX and a delimiter only for CSV', () => {
    const d = validDraft()
    const xlsx = toSourceCreate({ ...d, profile: { ...d.profile, format: 'XLSX', sheetName: 'Positions', delimiter: ';' } })
    expect(xlsx.profile.delimiter).toBeNull()
    expect(xlsx.profile.sheet_name).toBe('Positions')
    const json = toSourceCreate({ ...d, profile: { ...d.profile, format: 'JSON', sheetName: 'ignored' } })
    expect(json.profile.delimiter).toBeNull()
    expect(json.profile.sheet_name).toBeNull()
  })
  it('reports problems before the request', () => {
    const d = validDraft()
    expect(sourceProblems({ ...d, code: 'Loan Book' })).toEqual(['Code: use lower_snake_case, starting with a letter.'])
    expect(sourceProblems({ ...d, kind: 'FEED' })).toEqual(['Only file-drop sources can be registered in this release.'])
    expect(sourceProblems({ ...d, profile: { ...d.profile, owner: ' ' } })).toEqual(['Name the profile owner.'])
    const noKey = { ...d.contract, columns: d.contract.columns.map((c) => ({ ...c, businessKey: false })) }
    expect(contractProblems(noKey)).toEqual(['Mark at least one business-key column.'])
    const dup = { ...d.contract, columns: [...d.contract.columns, { ...d.contract.columns[1] }] }
    expect(contractProblems(dup)).toEqual(['balance: duplicate column name.'])
    expect(contractProblems({ ...d.contract, schemaId: '' })).toEqual(['Choose the target canonical schema.'])
    expect(contractProblems({ ...d.contract, columns: [] })).toEqual([
      'Add at least one expected column.',
      'Mark at least one business-key column.',
    ])
  })
})

describe('contract versions (FE-7.2)', () => {
  it('starts the next version from the current contract unchanged', () => {
    const c = contract({})
    expect(toContractIn(draftFromContract(c))).toEqual({
      schema_id: c.schema_id,
      expected_columns: c.expected_columns,
      business_key_columns: c.business_key_columns,
      control_total_columns: c.control_total_columns,
      change_summary: null,
    })
  })
  it('lists versions newest first without reordering the input', () => {
    const input = [contract({ version: 1, status: 'SUPERSEDED' }), contract({ version: 2 })]
    expect(newestFirst(input).map((c) => c.version)).toEqual([2, 1])
    expect(input.map((c) => c.version)).toEqual([1, 2])
  })
  it('titles the documented error codes and falls back otherwise', () => {
    const err = (code: string) => new ApiError(409, { code, message: 'm', details: null, correlation_id: 'cid', retryable: false })
    expect(errorTitle(err('duplicate_code'), 'x')).toBe('That code is already registered')
    expect(errorTitle(err('no_change'), 'x')).toBe('Nothing changed')
    expect(errorTitle(err('ingestion_disabled'), 'x')).toBe('Ingestion changes are switched off')
    expect(errorTitle(err('something_else'), 'fallback')).toBe('fallback')
    expect(errorTitle(new Error('boom'), 'fallback')).toBe('fallback')
  })
})
