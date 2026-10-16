import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import { MappingStatusPill } from '@/components/ingestion/MappingStatusPill'
import {
  MAPPING_STATUSES,
  MAPPING_STATUS_STYLE,
  RequestScope,
  buildRules,
  columnRow,
  constantRow,
  emptyParams,
  type MappingVersionStatus,
} from '@/components/ingestion/mappings'

const openapi = JSON.parse(readFileSync(fileURLToPath(new URL('../../../../../backend/openapi.json', import.meta.url)), 'utf8'))
const enumOf = (name: string): string[] => [...openapi.components.schemas[name].enum].sort()
const flush = () => new Promise((r) => setTimeout(r, 0))

describe('mapping workbench (FE-10.1/10.2)', () => {
  it('ignores a stale response after the import or version is switched', async () => {
    const scope = new RequestScope()
    const onReady = vi.fn()
    const onError = vi.fn()
    let answerA: (v: string) => void = () => {}
    let signalA: AbortSignal | null = null
    // A server that answers even after the abort: the late answer must still be dropped.
    scope.run(
      (signal) => {
        signalA = signal
        return new Promise<string>((r) => (answerA = r))
      },
      onReady,
      onError,
    )
    scope.switch()
    scope.run(() => Promise.resolve('import B'), onReady, onError)
    answerA('import A')
    await flush()
    expect(signalA!.aborted).toBe(true)
    expect(onReady).toHaveBeenCalledTimes(1)
    expect(onReady).toHaveBeenCalledWith('import B')
    expect(onError).not.toHaveBeenCalled()
  })

  it('builds RuleIn in the API shape and reports what the API would refuse', () => {
    const rows = [
      columnRow('instrument_id', { policy: 'MAP', target_field: 'instrument_id' }),
      columnRow('notional', { policy: 'MAP', target_field: 'notional', transform: 'PARSE_DECIMAL', params: { ...emptyParams(), thousands_sep: ',' } }),
      columnRow('book_code', { policy: 'PRESERVE', target_field: 'ignored' }),
      columnRow('legacy', { policy: 'UNSET' }),
      columnRow('flag', { policy: 'MAP', target_field: 'is_active', transform: 'PARSE_BOOLEAN', params: { ...emptyParams(), true_values: 'Y, yes', false_values: 'N' }, null_policy: 'USE_DEFAULT', default_value: 'N' }),
      { ...constantRow(), target_field: 'origin', params: { ...emptyParams(), value: 'CORE' } },
    ]
    const { rules, problems } = buildRules(rows)
    expect(problems).toEqual([])
    expect(rules).toEqual([
      { source_column: 'instrument_id', target_field: 'instrument_id', column_policy: 'MAP', transform: { code: 'COPY', params: {} }, null_policy: 'ALLOW_NULL' },
      { source_column: 'notional', target_field: 'notional', column_policy: 'MAP', transform: { code: 'PARSE_DECIMAL', params: { decimal_sep: '.', thousands_sep: ',' } }, null_policy: 'ALLOW_NULL' },
      { source_column: 'book_code', column_policy: 'PRESERVE', transform: { code: 'COPY', params: {} }, null_policy: 'ALLOW_NULL' },
      { source_column: 'flag', target_field: 'is_active', column_policy: 'MAP', transform: { code: 'PARSE_BOOLEAN', params: { true_values: ['Y', 'yes'], false_values: ['N'] } }, null_policy: 'USE_DEFAULT', default_value: 'N' },
      { target_field: 'origin', column_policy: 'MAP', transform: { code: 'CONSTANT', params: { value: 'CORE' } }, null_policy: 'ALLOW_NULL' },
    ])

    const bad = buildRules([
      columnRow('a', { policy: 'MAP' }),
      columnRow('b', { policy: 'MAP', target_field: 'x', transform: 'SCALE', params: { ...emptyParams(), power_of_ten: '19' } }),
      columnRow('c', { policy: 'MAP', target_field: 'x', null_policy: 'USE_DEFAULT' }),
      columnRow('d', { policy: 'MAP', target_field: 'y' }),
      columnRow('e', { policy: 'MAP', target_field: 'y' }),
    ])
    expect(bad.problems).toEqual([
      'a: choose a target field.',
      'b: the power of ten must be a whole number from -18 to 18.',
      'c: give the default value.',
      'Target field y is mapped more than once.',
    ])
  })

  it('MappingStatusPill covers exactly the mapping_version status enum; only ACTIVE looks live', () => {
    expect([...MAPPING_STATUSES].sort()).toEqual(enumOf('MappingVersionStatus'))
    for (const status of MAPPING_STATUSES as MappingVersionStatus[]) {
      const html = renderToStaticMarkup(createElement(MappingStatusPill, { status }))
      expect(html).toContain(MAPPING_STATUS_STYLE[status].label)
      expect(html).toContain(`data-status="${status}"`)
    }
    expect(MAPPING_STATUSES.filter((s) => MAPPING_STATUS_STYLE[s].className === 'status-published')).toEqual(['ACTIVE'])
  })
})
