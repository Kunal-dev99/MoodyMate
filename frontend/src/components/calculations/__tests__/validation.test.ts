import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { ResultContextPanel } from '@/components/calculations/ResultContextPanel'
import { RunIdentity } from '@/components/calculations/RunIdentity'
import { ValidationStatusBadge } from '@/components/calculations/ValidationStatusBadge'
import {
  CONTEXT_FIELD_TEXT,
  CURRENCY_BASIS_TEXT,
  VALIDATION_STATUSES,
  VALIDATION_STATUS_STYLE,
  VALIDATION_STATUS_UNKNOWN,
  contextMismatches,
  validationStyle,
  type ContextPins,
  type ResultContext,
  type ValidationStatus,
} from '@/components/calculations/validation'

/*
 * FE-30.1 (D-56): the validation-status badge and the "Result context" panel. The badge map is
 * tested against the OpenAPI enum the types are generated from (D-23), and the panel shows the
 * server's context block as sent, flagging (never resolving) a disagreement with the run's pins.
 */

const openapi = JSON.parse(readFileSync(fileURLToPath(new URL('../../../../../backend/openapi.json', import.meta.url)), 'utf8'))
const schema = (name: string) => openapi.components.schemas[name]
const enumOf = (name: string): string[] => [...schema(name).enum].sort()

const STATEMENT = 'Illustrative prototype. Not a regulatory calculation; no parity with any vendor system is claimed.'
const RULE_A = { rule_code: 'cashflow_direction', rule_version_id: '11111111-1111-4111-8111-111111111111', version: 1 }
const RULE_B = { rule_code: 'maturity_buckets', rule_version_id: '22222222-2222-4222-8222-222222222222', version: 2 }

const context = (over: Partial<ResultContext> = {}): ResultContext => ({
  as_of_date: '2026-12-31',
  consolidation: 'BLOCKED',
  currency: { basis: 'PER_CURRENCY_NO_FX', codes: ['EUR', 'USD'] },
  methodology: { module_code: 'maturity_ladder', module_version: 1, engine_version: 'mm-calc 1.0', rule_versions: [RULE_A, RULE_B] },
  units: { amount: 'exact decimal text in the row currency', days: 'calendar days after the reporting date', ratio: null },
  validation_status: 'ILLUSTRATIVE',
  statement: STATEMENT,
  ...over,
})

const pins = (over: Partial<ContextPins> = {}): ContextPins => ({
  reporting_date: '2026-12-31',
  module: { module_id: 'm', module_version_id: 'mv', code: 'maturity_ladder', name: 'Maturity ladder', version: 1 },
  engine_version: 'mm-calc 1.0',
  validation_status: 'ILLUSTRATIVE',
  // The manifest lists members by rule code; the context may list them in any order.
  manifest: { rule_set: { members: [RULE_B, RULE_A] } },
  ...over,
})

const render = (el: ReturnType<typeof createElement>) => renderToStaticMarkup(el)

describe('validation status follows the API enum (FE-30.1, D-56)', () => {
  it('the badge map covers exactly the OpenAPI ValidationStatus enum', () => {
    expect(Object.keys(VALIDATION_STATUS_STYLE).sort()).toEqual(enumOf('ValidationStatus'))
    expect([...VALIDATION_STATUSES].sort()).toEqual(enumOf('ValidationStatus'))
    for (const status of enumOf('ValidationStatus') as ValidationStatus[]) {
      const style = VALIDATION_STATUS_STYLE[status]
      expect(style.label.length, status).toBeGreaterThan(0)
      expect(style.description.length, status).toBeGreaterThan(0)
      expect(style.className, status).toMatch(/^status-/)
      const html = render(createElement(ValidationStatusBadge, { status }))
      expect(html).toContain(`data-validation-status="${status}"`)
      expect(html).toContain(style.label)
    }
  })

  it('ILLUSTRATIVE reads "Illustrative — not validated"; no label claims compliance, certification or vendor parity (D-35)', () => {
    expect(VALIDATION_STATUS_STYLE.ILLUSTRATIVE.label).toBe('Illustrative — not validated')
    for (const style of [...Object.values(VALIDATION_STATUS_STYLE), VALIDATION_STATUS_UNKNOWN]) {
      expect(`${style.label} ${style.description}`).not.toMatch(/complian|certif|moody/i)
    }
  })

  it('an unknown or missing status reads as not validated, never as more (D-04)', () => {
    expect(validationStyle(undefined)).toBe(VALIDATION_STATUS_UNKNOWN)
    expect(validationStyle(null)).toBe(VALIDATION_STATUS_UNKNOWN)
    expect(validationStyle('APPROVED' as ValidationStatus)).toBe(VALIDATION_STATUS_UNKNOWN)
    expect(validationStyle('toString' as ValidationStatus)).toBe(VALIDATION_STATUS_UNKNOWN)
    const html = render(createElement(ValidationStatusBadge, { status: undefined }))
    expect(html).toContain('data-validation-status="UNKNOWN"')
    expect(html).toContain('Not validated')
  })

  it('the currency basis text covers the OpenAPI ContextCurrency.basis constant', () => {
    const basis = schema('ContextCurrency').properties.basis
    expect(Object.keys(CURRENCY_BASIS_TEXT)).toEqual([basis.const ?? basis.enum?.[0]])
  })

  it('every result read the UI shows carries the status, and the five reads the context block (BE-30.2/30.3)', () => {
    for (const name of ['ModuleVersionOut', 'RunSummary', 'RunDetail', 'MaturityLadderOut', 'LiquidityOut', 'ScenarioComparisonOut', 'ResultPage'])
      expect(schema(name).required, name).toContain('validation_status')
    for (const name of ['RunDetail', 'ResultPage', 'MaturityLadderOut', 'LiquidityOut', 'ScenarioComparisonOut']) expect(schema(name).required, name).toContain('context')
    // BE-32.4 (D-61 §6) adds `consolidation: "BLOCKED"` to the block.
    expect([...schema('ResultContext').required].sort()).toEqual(['as_of_date', 'consolidation', 'currency', 'methodology', 'statement', 'units', 'validation_status'])
  })
})

describe('the result context is the server block, checked against the run pins (FE-30.1, D-56 §5)', () => {
  it('a context built from the pins matches, whatever the rule order', () => {
    expect(contextMismatches(context(), pins())).toEqual([])
    // Set-based: a rule version listed twice is still the same pins.
    expect(contextMismatches(context({ methodology: { ...context().methodology, rule_versions: [RULE_B, RULE_A, RULE_A] } }), pins())).toEqual([])
  })

  it('names every field where the context and the pins differ', () => {
    expect(contextMismatches(context({ as_of_date: '2026-12-30' }), pins())).toEqual(['as_of_date'])
    expect(contextMismatches(context({ methodology: { ...context().methodology, module_version: 2 } }), pins())).toEqual(['module'])
    expect(contextMismatches(context({ methodology: { ...context().methodology, module_code: 'lcr_hqla' } }), pins())).toEqual(['module'])
    expect(contextMismatches(context({ methodology: { ...context().methodology, engine_version: 'mm-calc 2.0' } }), pins())).toEqual(['engine_version'])
    expect(contextMismatches(context({ methodology: { ...context().methodology, rule_versions: [RULE_A] } }), pins())).toEqual(['rule_versions'])
    expect(contextMismatches(context({ methodology: { ...context().methodology, rule_versions: [RULE_A, { ...RULE_B, version: 3 }] } }), pins())).toEqual([
      'rule_versions',
    ])
    expect(contextMismatches(context({ validation_status: 'INTERNAL_VALIDATED' }), pins())).toEqual(['validation_status'])
    expect(Object.keys(CONTEXT_FIELD_TEXT).sort()).toEqual(['as_of_date', 'engine_version', 'module', 'rule_versions', 'validation_status'])
  })

  it('the panel shows the as-of date, the badge, the methodology pins, the currency basis, units and statement as sent', () => {
    const html = render(createElement(ResultContextPanel, { context: context(), pins: pins(), source: 'run' }))
    expect(html).toContain('data-testid="result-context"')
    expect(html).toContain('data-source="run"')
    expect(html).toContain('data-as-of-date="2026-12-31"')
    expect(html).toMatch(/data-testid="context-as-of-date"><span class="font-mono">2026-12-31<\/span>/)
    expect(html).toContain('data-testid="context-validation-status"')
    expect(html).toContain('Illustrative — not validated')
    expect(html).toContain('maturity_ladder v1')
    expect(html).toContain('mm-calc 1.0')
    expect(html).toContain('cashflow_direction v1')
    expect(html).toContain('maturity_buckets v2')
    expect(html).toContain('EUR, USD')
    expect(html).toContain(CURRENCY_BASIS_TEXT.PER_CURRENCY_NO_FX)
    expect(html).toContain('calendar days after the reporting date')
    expect(html).not.toContain('context-units-ratio')
    expect(html).toContain(STATEMENT)
    expect(html).not.toContain('context-mismatch')
    expect(html).toContain('Result context')
  })

  it('shows the ratio unit when the module reports a ratio (lcr_hqla), compact inside a result section', () => {
    const html = render(
      createElement(ResultContextPanel, { context: context({ units: { ...context().units, ratio: 'dimensionless, 4 dp ROUND_HALF_EVEN' } }), source: 'liquidity', compact: true }),
    )
    expect(html).toContain('data-source="liquidity"')
    expect(html).toContain('data-testid="context-units-ratio"')
    expect(html).toContain('dimensionless, 4 dp ROUND_HALF_EVEN')
  })

  it('a disagreement with the pins is shown, with both kept on screen', () => {
    const html = render(createElement(ResultContextPanel, { context: context({ as_of_date: '2026-12-30' }), pins: pins(), source: 'maturity-ladder', compact: true }))
    expect(html).toContain('data-testid="context-mismatch"')
    expect(html).toContain('data-fields="as_of_date"')
    expect(html).toContain('2026-12-30')
    // Non-blocking and neutral: the fields stay rendered, and the note is not styled as an error.
    expect(html).toContain('data-testid="context-as-of-date"')
    expect(html).toContain('differs from the run&#x27;s record')
    expect(html).not.toContain('--destructive')
  })

  it('a read without a context block says so and is treated as not validated', () => {
    const html = render(createElement(ResultContextPanel, { context: undefined, source: 'run' }))
    expect(html).toContain('data-testid="context-none"')
    expect(html).toContain('not validated')
  })

  it('the run identity carries the badge when the read sends a status, and nothing new otherwise', () => {
    const base = {
      run_id: 'r-1',
      status: 'SUCCEEDED' as const,
      reporting_date: '2026-12-31',
      module: pins().module,
      engine_version: 'mm-calc 1.0',
      rule_set_id: 'rs',
      assumption_set_id: 'as',
      dataset_snapshot_id: 'ds',
    }
    const withStatus = render(createElement(RunIdentity, { run: { ...base, validation_status: 'ILLUSTRATIVE' as const } }))
    expect(withStatus).toContain('data-testid="identity-validation-status"')
    expect(withStatus).toContain('Illustrative — not validated')
    expect(render(createElement(RunIdentity, { run: base }))).not.toContain('identity-validation-status')
  })
})
