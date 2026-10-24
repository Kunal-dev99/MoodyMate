import { createElement, isValidElement, type ReactElement, type ReactNode } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import { ModuleOption } from '@/components/calculations/ModuleOption'
import { ModuleContracts, PlannedModuleBadge } from '@/components/calculations/PlannedModuleBadge'
import {
  ILLUSTRATIVE_STATEMENT,
  IMPLEMENTATION_STYLE,
  INITIAL_PANEL,
  PLANNED_MODULE_REASON,
  isSelectable,
  panelReducer,
  submitProblems,
  type ModuleVersion,
} from '@/components/calculations/calculations'

/*
 * FE-18.1: PLANNED module versions (the BE-18.1 seeds alm_irrbb, collateral, capital) are
 * disabled "Planned — extension point" entries in the New-run picker, with the reason and their
 * declared contracts, and they can never be selected. Fixtures use the generated ModuleVersionOut.
 */

const AVAILABLE_ID = '44444444-4444-4444-8444-444444444444'
const PLANNED_ID = '77777777-7777-4777-8777-777777777777'

const moduleVersion = (over: Partial<ModuleVersion> = {}): ModuleVersion => ({
  module_id: 'module-1',
  module_version_id: AVAILABLE_ID,
  code: 'maturity_ladder',
  name: 'Maturity ladder',
  version: 1,
  description: 'Illustrative maturity ladder.',
  engine_version: '1',
  implementation_status: 'AVAILABLE',
  runnable: true,
  illustrative: true,
  provisional: true,
  statement: ILLUSTRATIVE_STATEMENT,
  validation_status: 'ILLUSTRATIVE',
  created_at: '2026-10-17T00:00:00Z',
  input_contract: {
    schemas: [{ schema_code: 'cashflow_event', required: true, fields: ['amount'] }],
    rules: ['maturity_buckets'],
    assumption_keys: [],
    parameters: ['reporting_date'],
  },
  output_contract: { results: ['liquidity_gap_result'], intermediates: ['CASHFLOW_CONTRIBUTION'], warning_codes: [] },
  rules: [],
  assumption_set: null,
  ...over,
})

const planned = moduleVersion({
  module_id: 'module-alm',
  module_version_id: PLANNED_ID,
  code: 'alm_irrbb',
  name: 'ALM / IRRBB',
  description: null,
  engine_version: '0',
  implementation_status: 'PLANNED',
  runnable: false,
  input_contract: {
    schemas: [
      { schema_code: 'instrument', required: true, fields: ['interest_rate', 'maturity_date'] },
      { schema_code: 'position', required: false, fields: [] },
    ],
    rules: ['repricing_buckets'],
    assumption_keys: ['rate_shock_bp'],
    parameters: ['reporting_date'],
  },
  output_contract: { results: ['irrbb_result'], intermediates: ['REPRICING_GAP'], warning_codes: ['NO_REPRICING_DATE'] },
})

const option = (m: ModuleVersion, onPick = vi.fn(), over: { checked?: boolean; busy?: boolean } = {}) =>
  createElement(ModuleOption, { module: m, checked: over.checked ?? false, busy: over.busy ?? false, missing: [], onPick })

/** The host element with this data-testid in the tree a component returns (no DOM in vitest). */
function byTestId(node: ReactNode, testId: string): ReactElement<Record<string, unknown>> | null {
  if (Array.isArray(node)) {
    for (const child of node) {
      const hit = byTestId(child, testId)
      if (hit) return hit
    }
    return null
  }
  if (!isValidElement<Record<string, unknown>>(node)) return null
  if (node.props['data-testid'] === testId) return node
  return byTestId(node.props.children as ReactNode, testId)
}

/** The radio ModuleOption renders, with its props (disabled, onChange). */
const radioOf = (m: ModuleVersion, onPick = vi.fn(), over: { checked?: boolean; busy?: boolean } = {}) => {
  const tree = ModuleOption({ module: m, checked: over.checked ?? false, busy: over.busy ?? false, missing: [], onPick })
  const radio = byTestId(tree, 'module-radio')
  expect(radio).not.toBeNull()
  return radio!.props as { disabled: boolean; onChange: () => void; 'aria-describedby'?: string }
}

describe('PLANNED modules in the New-run picker (FE-18.1)', () => {
  it('the badge reads "Planned — extension point", never as ready, and says why', () => {
    expect(IMPLEMENTATION_STYLE.PLANNED.label).toBe('Planned — extension point')
    const html = renderToStaticMarkup(createElement(PlannedModuleBadge))
    expect(html).toContain('Planned — extension point')
    expect(html).toContain('data-testid="planned-module-badge"')
    expect(html).toContain('data-status="PLANNED"')
    expect(html).toContain('module_not_available')
    expect(html.replace(PLANNED_MODULE_REASON, '')).not.toMatch(/available|ready|runnable/i)
  })

  it('renders a PLANNED module as a disabled entry with the reason and its declared contracts', () => {
    const html = renderToStaticMarkup(option(planned))
    expect(html).toMatch(/<input[^>]*type="radio"[^>]*disabled=""[^>]*data-testid="module-radio"/)
    expect(html).toContain('aria-disabled="true"')
    expect(html).toContain(`aria-describedby="module-reason-${PLANNED_ID}"`)
    expect(html).toContain(`id="module-reason-${PLANNED_ID}"`)
    expect(html).toContain('data-testid="module-planned-reason"')
    expect(html).toContain('data-testid="planned-module-badge"')
    expect(html).not.toContain('data-testid="module-implementation"')
    expect(html).not.toContain('Available')
    // The declared contracts, as the server sent them.
    expect(html).toContain('data-testid="module-contracts"')
    for (const text of ['instrument (required): interest_rate maturity_date', 'position (optional)', 'repricing_buckets', 'rate_shock_bp', 'irrbb_result', 'REPRICING_GAP', 'NO_REPRICING_DATE'])
      expect(html).toContain(text)
  })

  it('an AVAILABLE module stays an enabled option with its own pill and no planned entry', () => {
    const html = renderToStaticMarkup(option(moduleVersion()))
    expect(html).not.toMatch(/<input[^>]*disabled=""/)
    expect(html).toContain('data-testid="module-implementation"')
    expect(html).not.toContain('planned-module-badge')
    expect(html).not.toContain('module-contracts')
    expect(html).not.toContain('aria-disabled')
  })

  it('a PLANNED module can never be selected: its radio is disabled and a change event picks nothing', () => {
    const onPick = vi.fn()
    const radio = radioOf(planned, onPick)
    expect(radio.disabled).toBe(true)
    expect(radio['aria-describedby']).toBe(`module-reason-${PLANNED_ID}`)
    radio.onChange()
    expect(onPick).not.toHaveBeenCalled()

    const pick = vi.fn()
    const available = radioOf(moduleVersion(), pick)
    expect(available.disabled).toBe(false)
    available.onChange()
    expect(pick).toHaveBeenCalledWith(AVAILABLE_ID)
    // While a run is being submitted, nothing is picked either.
    const busy = vi.fn()
    radioOf(moduleVersion(), busy, { busy: true }).onChange()
    expect(busy).not.toHaveBeenCalled()
  })

  it('only AVAILABLE is selectable, and a PLANNED id that reaches the state still cannot be submitted', () => {
    expect(isSelectable(planned)).toBe(false)
    expect(isSelectable(moduleVersion())).toBe(true)
    const s = panelReducer(INITIAL_PANEL, { type: 'module', moduleVersionId: PLANNED_ID })
    expect(submitProblems(s, [moduleVersion(), planned])).toContain('Only an AVAILABLE module can run.')
  })

  it('the contracts block shows "—" for an empty list (never an invented value)', () => {
    const html = renderToStaticMarkup(createElement(ModuleContracts, { module: moduleVersion() }))
    expect(html).toMatch(/data-testid="contract-assumption-keys">.*—/)
    expect(html).toMatch(/data-testid="contract-warning-codes">.*—/)
    expect(html).toContain('cashflow_event (required): amount')
  })
})
