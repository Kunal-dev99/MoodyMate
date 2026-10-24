import { readFileSync, readdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { DecisionSection } from '@/components/ingestion/mapping-workbench/ApprovalSections'
import { ValidationResult } from '@/components/ingestion/mapping-workbench/ValidationResult'
import { VersionsSection } from '@/components/ingestion/mapping-workbench/VersionsSection'
import type { MappingWorkbenchState } from '@/components/ingestion/mapping-workbench/useMappingWorkbench'
import type { MappingDetail, MappingValidation, MappingVersion } from '@/components/ingestion/mappings'

/*
 * FE-35.2: the mapping workbench page (ingestion/mappings/[mappingId]/page.tsx) is split into
 * components/ingestion/mapping-workbench/. The mapping vitests and mappings.spec.ts run unchanged
 * against it; this file holds the split to its Done-when (no file over 300 lines) and checks the
 * split kept the page's reads, writes, switch and gate.
 */

const root = fileURLToPath(new URL('../../../', import.meta.url))
const PAGE = 'app/(platform)/ingestion/mappings/[mappingId]/page.tsx'
const DIR = 'components/ingestion/mapping-workbench'
const FILES = [PAGE, ...readdirSync(`${root}${DIR}`).filter((f) => /\.tsx?$/.test(f)).map((f) => `${DIR}/${f}`)]
const read = (file: string) => readFileSync(`${root}${file}`, 'utf8')
const lines = (file: string) => read(file).split('\n').length - (read(file).endsWith('\n') ? 1 : 0)
const calls = (method: string) =>
  FILES.flatMap((f) => [...read(f).matchAll(new RegExp(`api\\.${method}\\('([^']+)'`, 'g'))].map((m) => m[1])).sort()

const OTHER = '00000000-0000-4000-8000-0000000000bb'
const version = (n: number, over: Partial<MappingVersion> = {}): MappingVersion => ({
  id: `00000000-0000-4000-8000-00000000000${n}`,
  mapping_id: '00000000-0000-4000-8000-000000000100',
  version: n,
  status: 'DRAFT',
  schema_version_id: '00000000-0000-4000-8000-000000000200',
  schema_version: 1,
  content_sha256: 'c'.repeat(64),
  author: `user:${OTHER}`,
  author_user_id: OTHER,
  change_summary: null,
  submitted_at: null,
  decided_by: null,
  decided_at: null,
  decision_comment: null,
  last_validation: null,
  rules: [],
  created_at: '2026-10-24T09:00:00Z',
  ...over,
})
const mapping = (versions: MappingVersion[]): MappingDetail => ({
  id: '00000000-0000-4000-8000-000000000100',
  code: 'loans_v1',
  name: 'Loans',
  description: null,
  source_id: '00000000-0000-4000-8000-000000000300',
  source_code: 'core_loans',
  source_contract_id: '00000000-0000-4000-8000-000000000400',
  contract_version: 1,
  schema_id: '00000000-0000-4000-8000-000000000500',
  schema_code: 'loan',
  active_version: null,
  latest_version: versions.length,
  version_count: versions.length,
  created_by: `user:${OTHER}`,
  created_at: '2026-10-24T09:00:00Z',
  versions,
})
const noop = () => undefined

describe('the mapping workbench split (FE-35.2 Done-when)', () => {
  it('splits into the page and at least five parts', () => {
    expect(FILES.length).toBeGreaterThanOrEqual(6)
  })

  it.each(FILES)('%s is at most 300 lines', (file) => {
    expect(lines(file), file).toBeLessThanOrEqual(300)
  })

  it('keeps the page switch (NEXT_PUBLIC_MAPPING_UI) and its mapping:read gate on the page', () => {
    const page = read(PAGE)
    expect(page).toContain('if (!MAPPING_UI_ENABLED)')
    expect(page).toContain('<MappingsPlanned />')
    expect(page).toMatch(/<RequirePermission\s+permission="mapping:read"/)
    expect(page).toContain('<MappingWorkbench mappingId={mappingId} />')
  })

  it('reads and writes through the same routes as before the split, and no others', () => {
    expect(calls('GET')).toEqual(['/api/v1/imports', '/api/v1/imports/{import_id}/profile', '/api/v1/mappings/{mapping_id}', '/api/v1/schemas/{schema_id}/versions'])
    expect(calls('POST')).toEqual([
      '/api/v1/imports/{import_id}/mappings/validate',
      '/api/v1/mappings/{mapping_id}/versions',
      '/api/v1/mappings/{mapping_id}/versions/{version}/approval',
      '/api/v1/mappings/{mapping_id}/versions/{version}/approval',
      '/api/v1/mappings/{mapping_id}/versions/{version}/submit',
    ])
    for (const method of ['PUT', 'PATCH', 'DELETE']) expect(calls(method), method).toEqual([])
  })

  it('renders the version history newest first, marking the open one', () => {
    const v1 = version(1, { status: 'ACTIVE' })
    const v2 = version(2, { last_validation: { id: 'v', import_id: 'i', outcome: 'WARNINGS', ok: true, mapping_sha256: 'd'.repeat(64), created_at: '2026-10-24T09:00:00Z' } })
    const html = renderToStaticMarkup(createElement(VersionsSection, { m: mapping([v1, v2]), version: v1, select: () => true }))
    expect([...html.matchAll(/data-testid="mapping-version-row" data-version="(\d+)" data-status="(\w+)"/g)].map((m) => `${m[1]}:${m[2]}`)).toEqual(['2:DRAFT', '1:ACTIVE'])
    expect(html.match(/\(open\)/g)).toHaveLength(1)
    expect(html).toContain('WARNINGS')
  })

  it('renders a dry run: outcome, issues by severity and per-rule counts, never values', () => {
    const v: MappingValidation = {
      id: '00000000-0000-4000-8000-000000000600',
      mapping_id: '00000000-0000-4000-8000-000000000100',
      mapping_version_id: '00000000-0000-4000-8000-000000000001',
      version: 1,
      import_id: '00000000-0000-4000-8000-000000000700',
      mapping_sha256: 'e'.repeat(64),
      outcome: 'FAILED',
      ok: false,
      row_count: 10,
      blocker_count: 1,
      error_count: 0,
      warning_count: 1,
      issues: [
        { severity: 'WARNING', code: 'profile_type_mismatch', message: 'Type differs.' },
        { severity: 'BLOCKER', code: 'required_field_unmapped', message: 'Required field not mapped.', target_field: 'notional' },
      ],
      rule_results: [{ position: 1, source_column: 'amount', target_field: 'notional', target_type: 'DECIMAL', column_policy: 'MAP', transform: 'PARSE_DECIMAL', rows: 10, mapped: 8, empty: 0, failed: 2, failures: { not_a_decimal: 2 } }],
      created_by: `user:${OTHER}`,
      created_at: '2026-10-24T09:00:00Z',
    }
    const html = renderToStaticMarkup(createElement(ValidationResult, { v }))
    expect(html).toContain('data-outcome="FAILED"')
    expect([...html.matchAll(/data-testid="mapping-issue" data-severity="(\w+)"/g)].map((m) => m[1])).toEqual(['BLOCKER', 'WARNING'])
    expect(html).toContain('data-testid="mapping-rule-result" data-failed="2"')
    expect(html).toContain('not_a_decimal: 2')
  })

  it('shows approve and reject only when the decision is allowed, and tells the author they cannot approve', () => {
    const wb = { comment: '', setComment: noop, busy: null } as unknown as MappingWorkbenchState
    const v = version(2, { status: 'IN_REVIEW' })
    const props = { wb, m: mapping([version(1), v]), version: v, active: undefined, onApprove: noop, onReject: noop }
    const allowed = renderToStaticMarkup(createElement(DecisionSection, { ...props, mine: false, decisionAllowed: true }))
    expect(allowed).toContain('data-testid="mapping-approve"')
    // Reject needs a comment: it is disabled while the comment is empty.
    expect(allowed.match(/<button[^>]*data-testid="mapping-reject"[^>]*>/)?.[0]).toMatch(/\sdisabled=""/)
    expect(allowed.match(/<button[^>]*data-testid="mapping-approve"[^>]*>/)?.[0]).not.toMatch(/\sdisabled=""/)
    const author = renderToStaticMarkup(createElement(DecisionSection, { ...props, mine: true, decisionAllowed: false }))
    expect(author).not.toContain('data-testid="mapping-approve"')
    expect(author).toContain('You cannot approve your own mapping.')
  })
})
