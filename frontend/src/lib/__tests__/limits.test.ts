import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SERVED_LIMITS } from './served-limits'

/*
 * FE-35.3 Done-when: the bounds come from GET config/limits. A scan of src/components finds none
 * of the bound literals (the grep, kept as a test), the editor vitests take the served values
 * (served-limits.ts), and those values are the backend's own constants.
 */

/** frontend/src */
const SRC = join(__dirname, '..', '..')
const BACKEND = join(SRC, '..', '..', 'backend', 'app')

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) return name === '__tests__' ? [] : sourceFiles(path)
    return /\.(ts|tsx)$/.test(name) ? [path] : []
  })
}

/**
 * The bound literals FE-35.3 retired from src/components, as `grep -rnE` patterns. Each is a
 * value GET config/limits serves (or the name a component used to mirror one under); `under`
 * scopes a pattern to one folder. The scan covers the served fields only (Q15): see UNSERVED.
 */
const BOUND_LITERALS: { what: string; pattern: RegExp; under?: string }[] = [
  { what: 'a mirrored limit constant', pattern: /\b(?:LIMITS|NAME_MAX|DESCRIPTION_MAX|SUMMARY_MAX|IMPORT_MAX_BYTES|FRACTION_SCALE|PERCENT_SCALE|MIN_SHOCKS|MAX_SHOCKS|MAX_BUCKETS)\s*=/ },
  { what: 'the retired rule-limit keys', pattern: /\b(?:maxBuckets|maxMapEntries|maxDecimalScale|maxDays|bucketCodeLength)\b/ },
  { what: 'rule max_days (36 600)', pattern: /\b36_?600\b/ },
  { what: 'upload max_bytes (10 MiB)', pattern: /\b10\s*\*\s*1024\s*\*\s*1024\b/ },
  { what: 'scenario min/max_shocks in text', pattern: /\b1 to 5 shocks\b/ },
  { what: 'scenario fraction_scale as a fallback', pattern: /scale\s*\?\?\s*4\b|padEnd\(4\b|\\d\{1,4\}/ },
  // Q20: the explorer's submit and decision comments read the served scenario.max_comment_length.
  { what: 'scenario max_comment_length (COMMENT_MAX) in the explorer', pattern: /\bCOMMENT_MAX\b|maxLength=\{1_?000\}/, under: 'components/advanced/' },
]

/**
 * Q15: bounds in src/components that GET config/limits does not serve, so the scan above leaves
 * them alone (exclusions, listed in the Phase 35 report with a follow-up to serve them). Each is
 * the contract's own `maxLength` for the field (checked against openapi.json below).
 */
const UNSERVED: { file: string; literal: string; schema: string; field: string }[] = [
  { file: 'components/ingestion/mapping-workbench/ApprovalSections.tsx', literal: 'maxLength={2000} aria-label="Submit comment"', schema: 'SubmitIn', field: 'comment' },
  { file: 'components/ingestion/mapping-workbench/ApprovalSections.tsx', literal: 'maxLength={2000} aria-label="Decision comment"', schema: 'ApprovalIn', field: 'comment' },
  { file: 'components/ingestion/mapping-workbench/RulesSection.tsx', literal: 'maxLength={1000} aria-label="Change summary"', schema: 'MappingVersionIn', field: 'change_summary' },
  { file: 'components/ingestion/ContractEditor.tsx', literal: 'maxLength={1000}', schema: 'ContractIn', field: 'change_summary' },
]

/** `NAME = 123` (or `NAME = 10 * _MIB`, `"NAME": 50_000`) in a backend module, as a number. */
function backendInt(file: string, name: string): number {
  const text = readFileSync(join(BACKEND, file), 'utf8')
  const m = new RegExp(`^\\s*"?${name}"?\\s*[=:]\\s*([\\d_]+)(\\s*\\*\\s*_MIB)?`, 'm').exec(text)
  if (!m) throw new Error(`${name} not found in backend/app/${file}`)
  return Number(m[1].replace(/_/g, '')) * (m[2] ? 1024 * 1024 : 1)
}

afterEach(() => {
  vi.doUnmock('@/api/client')
  vi.resetModules()
})

describe('no bound literal in src/components (FE-35.3 Done-when)', () => {
  const files = sourceFiles(join(SRC, 'components'))

  it('scans the component sources; the unserved bounds it excludes are the contract maxLengths (Q15)', () => {
    expect(files.length).toBeGreaterThan(100)
    const openapi = JSON.parse(readFileSync(join(SRC, '..', '..', 'backend', 'openapi.json'), 'utf8'))
    for (const { file, literal, schema, field } of UNSERVED) {
      expect(readFileSync(join(SRC, file), 'utf8'), file).toContain(literal)
      const prop = openapi.components.schemas[schema].properties[field]
      const maxLength = prop.maxLength ?? prop.anyOf?.find((a: { maxLength?: number }) => a.maxLength)?.maxLength
      expect(`maxLength={${maxLength}}`, `${schema}.${field}`).toBe(/maxLength=\{\d+\}/.exec(literal)![0])
    }
    // Unserved: GET config/limits has no mapping or contract group.
    expect(Object.keys(openapi.components.schemas.ConfigLimits.properties).sort()).toEqual(['rule', 'scenario', 'upload'])
  })

  it.each(BOUND_LITERALS)('finds no $what', ({ pattern, under }) => {
    const hits = files.filter((file) => !under || relative(SRC, file).startsWith(under)).flatMap((file) =>
      readFileSync(file, 'utf8')
        .split('\n')
        .flatMap((line, i) => (pattern.test(line) ? [`${relative(SRC, file)}:${i + 1}: ${line.trim()}`] : [])),
    )
    expect(hits).toEqual([])
  })

  it('the editors read GET config/limits (fetchLimits) instead', () => {
    for (const file of ['components/rules/ProposeRuleVersion.tsx', 'components/advanced/ScenarioEditor.tsx', 'app/(platform)/ingestion/imports/new/page.tsx'])
      expect(readFileSync(join(SRC, file), 'utf8'), file).toMatch(/\bfetchLimits\(\)/)
  })
})

describe('the served fixture is what the backend serves', () => {
  it('rule limits are rules.constants (BE-16.1, D-32 §1)', () => {
    expect(SERVED_LIMITS.rule).toEqual({
      max_buckets: backendInt('rules/constants.py', 'MAX_BUCKETS'),
      max_map_entries: backendInt('rules/constants.py', 'MAX_MAP_ENTRIES'),
      max_decimal_scale: backendInt('rules/constants.py', 'MAX_DECIMAL_SCALE'),
      max_days: backendInt('rules/constants.py', 'MAX_DAYS'),
      max_bucket_code_length: backendInt('rules/constants.py', 'MAX_BUCKET_CODE_LENGTH'),
    })
  })

  it('scenario limits are the D-44 §1-4 constants', () => {
    const o = (name: string) => backendInt('orchestration/constants.py', name)
    expect(SERVED_LIMITS.scenario).toEqual({
      min_shocks: backendInt('calculations/constants.py', 'MIN_SHOCKS'),
      max_shocks: backendInt('calculations/constants.py', 'MAX_SHOCKS'),
      max_instrument_keys: o('MAX_INSTRUMENT_KEYS'),
      max_instrument_key_length: o('MAX_TEXT_FIELD'),
      fraction_scale: o('FRACTION_SCALE'),
      max_delay_days: o('MAX_DELAY_DAYS'),
      max_unavailable_days: o('MAX_UNAVAILABLE_DAYS'),
      max_name_length: o('MAX_NAME'),
      max_description_length: o('MAX_DESCRIPTION'),
      max_change_summary_length: o('MAX_CHANGE_SUMMARY'),
      max_comment_length: o('MAX_DECISION_COMMENT'),
    })
  })

  it('upload limits are the default IMPORT_MAX_* settings (the D-16 §1 hard ceilings)', () => {
    const c = (name: string) => backendInt('core/config.py', name)
    expect(SERVED_LIMITS.upload).toEqual({
      max_bytes: c('IMPORT_MAX_BYTES'),
      max_rows: c('IMPORT_MAX_ROWS'),
      max_columns: c('IMPORT_MAX_COLUMNS'),
      max_cell_chars: c('IMPORT_MAX_CELL_CHARS'),
      max_xlsx_uncompressed_bytes: c('IMPORT_MAX_XLSX_UNCOMPRESSED_BYTES'),
      max_xlsx_ratio: c('IMPORT_MAX_XLSX_RATIO'),
      max_decoded_chars: c('IMPORT_MAX_DECODED_CHARS'),
    })
  })

  it('the fixture has exactly the contract fields (openapi.json ConfigLimits)', () => {
    const openapi = JSON.parse(readFileSync(join(SRC, '..', '..', 'backend', 'openapi.json'), 'utf8'))
    const fields = (name: string) => Object.keys(openapi.components.schemas[name].properties).sort()
    expect(Object.keys(SERVED_LIMITS).sort()).toEqual(fields('ConfigLimits'))
    expect(Object.keys(SERVED_LIMITS.rule).sort()).toEqual(fields('RuleLimits'))
    expect(Object.keys(SERVED_LIMITS.scenario).sort()).toEqual(fields('ScenarioLimits'))
    expect(Object.keys(SERVED_LIMITS.upload).sort()).toEqual(fields('UploadLimits'))
    expect(openapi.paths['/api/v1/config/limits'].get.security).toEqual([{ HTTPBearer: [] }])
  })
})

describe('fetchLimits', () => {
  async function withApi(GET: ReturnType<typeof vi.fn>) {
    vi.doMock('@/api/client', () => ({
      api: { GET },
      call: async (request: () => Promise<{ data?: unknown; error?: unknown }>) => {
        const { data, error } = await request()
        if (error) throw error
        return data
      },
    }))
    vi.resetModules()
    return import('@/lib/limits')
  }

  it('reads GET config/limits once per page life and shares the answer', async () => {
    const GET = vi.fn(async () => ({ data: SERVED_LIMITS }))
    const { fetchLimits } = await withApi(GET)
    const [a, b] = await Promise.all([fetchLimits(), fetchLimits()])
    expect(await fetchLimits()).toBe(a)
    expect(b).toBe(a)
    expect(GET).toHaveBeenCalledTimes(1)
    expect(GET).toHaveBeenCalledWith('/api/v1/config/limits')
  })

  it('forgets a failed read, so "Try again" asks the server again', async () => {
    const GET = vi.fn().mockResolvedValueOnce({ error: { code: 'db_pool_exhausted' } }).mockResolvedValueOnce({ data: SERVED_LIMITS })
    const { fetchLimits } = await withApi(GET)
    await expect(fetchLimits()).rejects.toEqual({ code: 'db_pool_exhausted' })
    expect(await fetchLimits()).toEqual(SERVED_LIMITS)
    expect(GET).toHaveBeenCalledTimes(2)
  })
})
