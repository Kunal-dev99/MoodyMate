import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, type Schemas } from '@/api/client'
import { ScenarioStatusPill } from '@/components/advanced/ScenarioStatusPill'
import {
  buildShock,
  compareUnits,
  draftFromShock,
  editorProblems,
  fieldProblem,
  hasProblems,
  keysProblem,
  newShock,
  percentToFraction,
  shockProblems,
  type ShockDraft,
} from '@/components/advanced/editor'
import {
  COMMENT_MAX,
  MINE_STATUSES,
  SCENARIO_STATUS_STYLE,
  acknowledged,
  acknowledgement,
  canConfirm,
  canEdit,
  canSubmit,
  commentProblem,
  decisionBody,
  decisionGate,
  describeShock,
  fractionToPercent,
  libraryQuery,
  proposedDefaults,
  shockProblemLines,
  tabOf,
  tabsFor,
  type ScenarioVersion,
  type ScenarioVersionStatus,
  type TemplateField,
  type TemplateOut,
} from '@/components/advanced/scenarios'

/*
 * FE-24.2: the Agentic Stress-Test Explorer. Fixtures use the generated types (TemplateOut,
 * TemplateField, ScenarioVersionOut, Permission); enums and limits are tested against
 * openapi.json. The template fixture has the bounds GET /scenario-templates returns for the
 * seeded cashflow_direction rule (LOAN and BOND inflows, DEPOSIT an outflow).
 */

const openapi = JSON.parse(readFileSync(fileURLToPath(new URL('../../../../../backend/openapi.json', import.meta.url)), 'utf8'))
const schemas = openapi.components.schemas
const enumOf = (name: string): string[] => [...schemas[name].enum].sort()

const ME = { user_id: '11111111-1111-4111-8111-111111111111' }
const OTHER = '99999999-9999-4999-8999-999999999999'
type Permission = Schemas['Permission']
const holding =
  (...ps: Permission[]) =>
  (p: Permission) =>
    ps.includes(p)

const field = (over: Partial<TemplateField> & Pick<TemplateField, 'name' | 'kind'>): TemplateField => ({
  required: false,
  min: null,
  min_exclusive: false,
  max: null,
  scale: null,
  max_items: null,
  values: [],
  unit: null,
  description: '',
  ...over,
})
const fraction = (name: string) => field({ name, kind: 'DECIMAL', required: true, min: '0', min_exclusive: true, max: '1', scale: 4, unit: 'fraction' })
const days = (name: string, min: string, max: string, required: boolean) => field({ name, kind: 'INTEGER', required, min, max, unit: 'calendar days' })
const CALENDAR = field({ name: 'delay_calendar', kind: 'ENUM', values: ['CALENDAR_DAYS'] })
const COMMON = [
  field({ name: 'currency', kind: 'CURRENCY' }),
  field({ name: 'origin', kind: 'ENUM', values: ['CONTRACTUAL', 'BEHAVIOURAL'] }),
  days('due_from_day', '0', '3650', false),
  days('due_to_day', '0', '3650', false),
]
const DELAYED: TemplateOut = {
  template: 'DELAYED_INFLOWS',
  name: 'Delayed inflows',
  description: '',
  direction: 'INFLOW',
  changes_amounts: false,
  parameters: [field({ name: 'cashflow_type', kind: 'INSTRUMENT_TYPE', required: true, values: ['BOND', 'LOAN'] }), fraction('affected_fraction'), days('delay_days', '1', '3650', true), CALENDAR],
  selector: COMMON,
}
const WITHDRAWAL: TemplateOut = {
  template: 'DEPOSIT_WITHDRAWAL',
  name: 'Deposit withdrawal',
  description: '',
  direction: 'OUTFLOW',
  changes_amounts: true,
  parameters: [field({ name: 'cashflow_type', kind: 'INSTRUMENT_TYPE', required: true, values: ['DEPOSIT'] }), fraction('increase_fraction')],
  selector: COMMON,
}
const FUNDING: TemplateOut = {
  template: 'FUNDING_UNAVAILABLE',
  name: 'Funding unavailable',
  description: '',
  direction: 'INFLOW',
  changes_amounts: false,
  parameters: [days('unavailable_days', '1', '365', true), fraction('affected_fraction'), CALENDAR],
  selector: [
    field({ name: 'instrument_type', kind: 'INSTRUMENT_TYPE', required: true, values: ['BOND', 'LOAN'] }),
    field({ name: 'instrument_keys', kind: 'INSTRUMENT_KEYS', max_items: 50 }),
    field({ name: 'source_system_code', kind: 'TEXT' }),
    ...COMMON,
  ],
}
const CATALOGUE = [DELAYED, WITHDRAWAL, FUNDING]

const fill = (t: TemplateOut, parameters: Record<string, string>, selector: Record<string, string> = {}, keys: string[] = []): ShockDraft => {
  const d = newShock(t)
  return { ...d, parameters: { ...d.parameters, ...parameters }, selector: { ...d.selector, ...selector }, instrumentKeys: keys }
}

const version = (over: Partial<ScenarioVersion> = {}): Pick<ScenarioVersion, 'status' | 'author_user_id' | 'origin' | 'parameter_sources'> => ({
  status: 'DRAFT',
  author_user_id: ME.user_id,
  origin: 'MANUAL',
  parameter_sources: null,
  ...over,
})

afterEach(() => {
  vi.unstubAllEnvs()
  vi.doUnmock('next/navigation')
  vi.resetModules()
})

describe('scenario-status pill (FE-24.2 Done-when)', () => {
  const statuses = enumOf('ScenarioVersionStatus')

  it('styles exactly the openapi.json ScenarioVersionStatus enum', () => {
    expect(Object.keys(SCENARIO_STATUS_STYLE).sort()).toEqual(statuses)
    expect(statuses).toEqual(['APPROVED_FOR_TEST', 'CLOSED', 'CONFIRMED_FOR_EXPLORATION', 'DRAFT', 'IN_REVIEW', 'REJECTED', 'RUNNING', 'TESTED'])
  })

  it.each(statuses)('renders %s as the server sent it, with its label and style', (status) => {
    const html = renderToStaticMarkup(createElement(ScenarioStatusPill, { status: status as ScenarioVersionStatus }))
    const style = SCENARIO_STATUS_STYLE[status as ScenarioVersionStatus]
    expect(html).toContain(`data-status="${status}"`)
    expect(html).toContain(`>${style.label}</span>`)
    expect(html).toContain(style.className)
  })

  it('never shows an exploration as approved, and marks the reserved statuses', () => {
    expect(SCENARIO_STATUS_STYLE.CONFIRMED_FOR_EXPLORATION.className).not.toBe(SCENARIO_STATUS_STYLE.APPROVED_FOR_TEST.className)
    expect(SCENARIO_STATUS_STYLE.CONFIRMED_FOR_EXPLORATION.description).toContain('Exploration, not approved')
    expect(SCENARIO_STATUS_STYLE.RUNNING.label).toContain('reserved')
    expect(SCENARIO_STATUS_STYLE.TESTED.label).toContain('reserved')
  })

  it('matches the approval labels the server sends (ApprovalLabel enum)', () => {
    expect(enumOf('ApprovalLabel')).toEqual(['Approved scenario (illustrative)', 'Exploration, not approved'])
  })
})

describe('editor validation states (FE-24.2 Done-when)', () => {
  const pct = fraction('affected_fraction')

  it('bounds: a fraction typed as percent is 0 < f <= 100%', () => {
    expect(fieldProblem(pct, '0')).toBe('Must be more than 0%.')
    expect(fieldProblem(pct, '0.00')).toBe('Must be more than 0%.')
    expect(fieldProblem(pct, '0.01')).toBeNull()
    expect(fieldProblem(pct, '100')).toBeNull()
    expect(fieldProblem(pct, '100.01')).toBe('At most 100%.')
    expect(fieldProblem(pct, '150')).toBe('At most 100%.')
  })

  it('bounds: day counts come from the template (delay 1-3650, unavailable 1-365)', () => {
    const delay = DELAYED.parameters[2]
    expect(fieldProblem(delay, '0')).toBe('Between 1 and 3650 calendar days.')
    expect(fieldProblem(delay, '1')).toBeNull()
    expect(fieldProblem(delay, '3650')).toBeNull()
    expect(fieldProblem(delay, '3651')).toBe('Between 1 and 3650 calendar days.')
    expect(fieldProblem(delay, '14.5')).toBe('Enter a whole number.')
    expect(fieldProblem(FUNDING.parameters[0], '366')).toBe('Between 1 and 365 calendar days.')
  })

  it('scale 4: at most 2 decimals in percent (4 as a fraction), never rounded', () => {
    expect(fieldProblem(pct, '15.55')).toBeNull()
    expect(fieldProblem(pct, '15.555')).toBe('At most 2 decimal places in percent (scale 4 as a fraction); never rounded.')
    expect(percentToFraction('15.55')).toBe('0.1555')
  })

  it('no formulas, exponents or signs: only plain numbers', () => {
    for (const bad of ['amount*0.2', '2E-1', '-5', '+5', '1/5', ' 20 %', '0x10', '20,5'])
      expect(fieldProblem(pct, bad), bad).toMatch(/^Enter a plain percentage/)
  })

  it('required fields: empty required fields are refused; empty optional ones are fine', () => {
    const problems = shockProblems(DELAYED, newShock(DELAYED))
    expect(problems).toEqual({
      'parameters.cashflow_type': 'Required.',
      'parameters.affected_fraction': 'Required.',
      'parameters.delay_days': 'Required.',
    })
    expect(shockProblems(FUNDING, newShock(FUNDING))).toEqual({
      'parameters.unavailable_days': 'Required.',
      'parameters.affected_fraction': 'Required.',
      'selector.instrument_type': 'Required.',
    })
  })

  it('CALENDAR_DAYS only: the calendar is prefilled and anything else is refused', () => {
    expect(newShock(DELAYED).parameters.delay_calendar).toBe('CALENDAR_DAYS')
    expect(newShock(FUNDING).parameters.delay_calendar).toBe('CALENDAR_DAYS')
    expect(fieldProblem(CALENDAR, 'BUSINESS_DAYS')).toBe('Only CALENDAR_DAYS.')
    expect(fieldProblem(CALENDAR, 'CALENDAR_DAYS')).toBeNull()
  })

  it('instrument types: only those the ACTIVE rule lists with the template direction', () => {
    expect(fieldProblem(WITHDRAWAL.parameters[0], 'LOAN')).toBe('Only DEPOSIT.')
    expect(fieldProblem(DELAYED.parameters[0], 'DEPOSIT')).toBe('Only BOND or LOAN.')
    expect(fieldProblem({ ...WITHDRAWAL.parameters[0], values: [] }, 'DEPOSIT')).toMatch(/No instrument type fits/)
  })

  it('selectors: currency, the due window and the instrument picker limits', () => {
    expect(fieldProblem(COMMON[0], 'eur')).toMatch(/three-letter/)
    expect(fieldProblem(COMMON[0], 'EUR')).toBeNull()
    const window = shockProblems(DELAYED, fill(DELAYED, { cashflow_type: 'LOAN', affected_fraction: '20', delay_days: '14' }, { due_from_day: '30', due_to_day: '7' }))
    expect(window).toEqual({ 'selector.due_to_day': 'Not before "Due from day".' })
    const keys = FUNDING.selector[1]
    expect(keysProblem(keys, Array.from({ length: 50 }, (_, i) => `K${i}`))).toBeNull()
    expect(keysProblem(keys, Array.from({ length: 51 }, (_, i) => `K${i}`))).toBe('At most 50 instruments.')
    expect(keysProblem(keys, ['A', 'A'])).toBe('An instrument is picked twice.')
  })

  it('the editor needs a name and 1 to max_shocks shocks', () => {
    const empty = editorProblems(CATALOGUE, 5, [], { name: ' ', summary: '' })
    expect(empty.name).toBe('A name is required.')
    expect(empty.count).toBe('Add at least one shock.')
    expect(hasProblems(empty)).toBe(true)
    const six = Array.from({ length: 6 }, () => fill(WITHDRAWAL, { cashflow_type: 'DEPOSIT', increase_fraction: '15' }))
    expect(editorProblems(CATALOGUE, 5, six, { name: 'x', summary: '' }).count).toBe('At most 5 shocks.')
    // Editing a version: the name is not edited, so it is not checked.
    expect(editorProblems(CATALOGUE, 5, six.slice(0, 1), { summary: '' }).name).toBeNull()
  })
})

describe('the plan example as a payload (FE-24.2)', () => {
  const delayed = fill(DELAYED, { cashflow_type: 'LOAN', affected_fraction: '20', delay_days: '14' })
  const withdrawal = fill(WITHDRAWAL, { cashflow_type: 'DEPOSIT', increase_fraction: '15' })

  it('builds "Two-week loan repayment delay" with exact fraction text and no empty selector', () => {
    const problems = editorProblems(CATALOGUE, 5, [delayed, withdrawal], { name: 'Two-week loan repayment delay', summary: '' })
    expect(hasProblems(problems)).toBe(false)
    expect(buildShock(DELAYED, delayed)).toEqual({
      template: 'DELAYED_INFLOWS',
      parameters: { cashflow_type: 'LOAN', affected_fraction: '0.2000', delay_days: 14, delay_calendar: 'CALENDAR_DAYS' },
    })
    expect(buildShock(WITHDRAWAL, withdrawal)).toEqual({ template: 'DEPOSIT_WITHDRAWAL', parameters: { cashflow_type: 'DEPOSIT', increase_fraction: '0.1500' } })
  })

  it('builds a funding shock with picked instruments and a selector', () => {
    const d = fill(FUNDING, { unavailable_days: '7', affected_fraction: '100' }, { instrument_type: 'LOAN', currency: 'EUR' }, ['L-1', 'L-2'])
    expect(shockProblems(FUNDING, d)).toEqual({})
    const shock = buildShock(FUNDING, d)
    expect(shock).toEqual({
      template: 'FUNDING_UNAVAILABLE',
      parameters: { unavailable_days: 7, affected_fraction: '1.0000', delay_calendar: 'CALENDAR_DAYS' },
      selector: { instrument_type: 'LOAN', instrument_keys: ['L-1', 'L-2'], currency: 'EUR' },
    })
    expect(describeShock(shock)).toBe('Funding unavailable: 100% of LOAN (2 picked instruments) inflows due within 7 days move to day 8')
  })

  it('keeps masked picks out of every request and blocks the shock until they are removed', () => {
    const masked = { masked: true as const, classification: 'CONFIDENTIAL' }
    const stored = {
      template: 'FUNDING_UNAVAILABLE',
      parameters: { unavailable_days: 7, affected_fraction: '1.0000', delay_calendar: 'CALENDAR_DAYS' },
      selector: { instrument_type: 'LOAN', instrument_keys: ['L-1', masked] },
    } as unknown as Parameters<typeof draftFromShock>[1]
    const d = draftFromShock(FUNDING, stored)
    expect(d.instrumentKeys).toEqual(['L-1'])
    expect(d.maskedKeys).toEqual([masked])
    expect(shockProblems(FUNDING, d)['selector.instrument_keys']).toMatch(/masked for your role/)
    const cleared = { ...d, maskedKeys: [] }
    expect(shockProblems(FUNDING, cleared)).toEqual({})
    expect(JSON.stringify(buildShock(FUNDING, cleared))).not.toContain('masked')
    expect(JSON.stringify(buildShock(FUNDING, d))).not.toContain('masked')
  })

  it('round-trips a stored shock into the editor (percent text) and back', () => {
    const shock = buildShock(DELAYED, delayed)
    const again = draftFromShock(DELAYED, shock)
    expect(again.parameters).toMatchObject({ cashflow_type: 'LOAN', affected_fraction: '20', delay_days: '14', delay_calendar: 'CALENDAR_DAYS' })
    expect(buildShock(DELAYED, again)).toEqual(shock)
    expect(describeShock(shock)).toBe('Delayed inflows: 20% of LOAN inflows move 14 calendar days later')
    expect(describeShock(buildShock(WITHDRAWAL, withdrawal))).toBe('Deposit withdrawal: DEPOSIT outflows +15%')
  })

  it('moves the decimal point in text, never through a float', () => {
    expect(['0.2000', '0.1500', '0.0001', '1', '1.0000', '0.1555', '0.3333'].map(fractionToPercent)).toEqual(['20', '15', '0.01', '100', '100', '15.55', '33.33'])
    expect(['20', '15', '0.01', '100', '15.55', '7.5'].map(percentToFraction)).toEqual(['0.2000', '0.1500', '0.0001', '1.0000', '0.1555', '0.0750'])
    // 0.1 + 0.2 in binary floats is 0.30000000000000004; the text path stays exact.
    expect(percentToFraction('30')).toBe('0.3000')
    expect(compareUnits('10000', '9999')).toBe(1)
    expect(compareUnits('0', '0')).toBe(0)
  })
})

describe('library tabs (FE-24.2)', () => {
  it('offers Review only with scenario:approve', () => {
    expect(tabsFor(holding('calc:read', 'scenario:propose'))).toEqual(['shared', 'mine'])
    expect(tabsFor(holding('calc:read', 'scenario:approve'))).toEqual(['shared', 'mine', 'review'])
    expect(tabOf('review', holding('calc:read'))).toBe('shared')
    expect(tabOf('review', holding('scenario:approve'))).toBe('review')
    expect(tabOf('nonsense', holding('calc:read'))).toBe('shared')
  })

  it('the shared library sends no status (the server default: APPROVED_FOR_TEST only)', () => {
    expect(libraryQuery('shared', 0)).toEqual({ limit: 50, offset: 0 })
    expect(libraryQuery('review', 50)).toEqual({ limit: 50, offset: 50, status: ['IN_REVIEW'] })
    expect(libraryQuery('mine', 0)).toEqual({ limit: 50, offset: 0, mine: true, status: MINE_STATUSES })
    expect(libraryQuery('mine', 0, true).status).toContain('CLOSED')
    expect(MINE_STATUSES).not.toContain('CLOSED')
  })
})

describe('version gates (FE-24.2; the server decides, D-04)', () => {
  const analyst = holding('calc:read', 'scenario:propose')
  const approver = holding('calc:read', 'scenario:approve')

  it('only the author edits, confirms and submits', () => {
    expect(canEdit(version(), ME, analyst)).toBe(true)
    expect(canConfirm(version(), ME, analyst)).toBe(true)
    expect(canConfirm(version({ author_user_id: OTHER }), ME, analyst)).toBe(false)
    expect(canConfirm(version({ status: 'CONFIRMED_FOR_EXPLORATION' }), ME, analyst)).toBe(false)
    expect(canSubmit(version({ status: 'CONFIRMED_FOR_EXPLORATION' }), ME, analyst)).toBe(true)
    expect(canSubmit(version({ status: 'IN_REVIEW' }), ME, analyst)).toBe(false)
    expect(canSubmit(version(), ME, approver)).toBe(false)
  })

  it('Approve shows only to an approver who is not the author', () => {
    expect(decisionGate(version({ status: 'IN_REVIEW', author_user_id: OTHER }), ME, approver)).toBe('allowed')
    expect(decisionGate(version({ status: 'IN_REVIEW' }), ME, holding('scenario:approve', 'scenario:propose'))).toBe('self_approval')
    expect(decisionGate(version({ status: 'IN_REVIEW', author_user_id: OTHER }), ME, analyst)).toBe('no_permission')
    expect(decisionGate(version({ status: 'CONFIRMED_FOR_EXPLORATION', author_user_id: OTHER }), ME, approver)).toBe('not_in_review')
  })

  it('a decision needs a comment of 1 to 1,000 characters (openapi.json)', () => {
    expect(schemas.ScenarioVersionDecision.properties.comment.maxLength).toBe(COMMENT_MAX)
    expect(schemas.ScenarioVersionDecision.required).toContain('comment')
    expect(commentProblem('  ')).toBe('A comment is required.')
    expect(commentProblem('', false)).toBeNull()
    expect(commentProblem('x'.repeat(COMMENT_MAX + 1))).toMatch(/^At most 1,000 characters/)
    expect(decisionBody('APPROVE', '  ok  ')).toEqual({ decision: 'APPROVE', comment: 'ok' })
    expect(decisionBody('REJECT', '')).toBeNull()
  })

  it('an AI_PROPOSED version needs the origin and every proposed default acknowledged', () => {
    const ai = version({ origin: 'AI_PROPOSED', parameter_sources: { 'shocks.0.parameters.delay_days': 'PROPOSED_DEFAULT', 'shocks.0.parameters.cashflow_type': 'STATED' } })
    expect(proposedDefaults(ai)).toEqual(['shocks.0.parameters.delay_days'])
    expect(acknowledged(ai, true, [])).toBe(false)
    expect(acknowledged(ai, false, ['shocks.0.parameters.delay_days'])).toBe(false)
    expect(acknowledged(ai, true, ['shocks.0.parameters.delay_days'])).toBe(true)
    expect(acknowledgement(ai, true, ['shocks.0.parameters.delay_days'])).toEqual({ acknowledge_ai_origin: true, acknowledged_defaults: ['shocks.0.parameters.delay_days'] })
    expect(acknowledged(version(), false, [])).toBe(true)
    expect(acknowledgement(version(), true, ['x'])).toEqual({ acknowledge_ai_origin: false })
  })
})

describe('refusals (FE-24.2)', () => {
  it('shows a 422 invalid_shock as value-free lines', () => {
    const err = new ApiError(422, {
      code: 'invalid_shock',
      message: 'A shock was refused.',
      retryable: false,
      details: {
        problems: [
          { shock_index: 1, field: 'parameters.increase_fraction', code: 'too_many_places' },
          { shock_index: null, field: 'shocks', code: 'too_many_shocks' },
        ],
      },
    })
    expect(shockProblemLines(err)).toEqual([
      { shock: 1, text: 'Shock 2 · parameters.increase_fraction: more than 4 decimal places (never rounded) (too_many_places)' },
      { shock: null, text: 'The shock list: too many shocks (too_many_shocks)' },
    ])
    expect(shockProblemLines(new ApiError(409, { code: 'not_author', message: 'x', retryable: false }))).toEqual([])
    // The pre-change bare list is not the contract any more: it yields no lines, never a crash.
    expect(shockProblemLines(new ApiError(422, { code: 'invalid_shock', message: 'x', retryable: false, details: [{ shock_index: 0, field: 'f', code: 'required' }] }))).toEqual([])
    expect(
      shockProblemLines(new ApiError(422, { code: 'invalid_shock', message: 'x', retryable: false, details: { problems: [{ shock_index: 0, field: 'selector.instrument_keys', code: 'unknown_instrument' }] } })),
    ).toEqual([{ shock: 0, text: 'Shock 1 · selector.instrument_keys: an instrument that is unknown or not visible to you (unknown_instrument)' }])
  })
})

describe('NEXT_PUBLIC_STRESS_EXPLORER_UI (D-02)', () => {
  it('every explorer page sits under the gated /advanced layout (FE-24.1 tests the 404 itself)', () => {
    const root = join(__dirname, '..', '..', '..')
    expect(readFileSync(join(root, 'app', '(platform)', 'advanced', 'layout.tsx'), 'utf8')).toContain('<AdvancedGate>')
    expect(readFileSync(join(root, 'app', '(platform)', 'advanced', 'stress-explorer', 'layout.tsx'), 'utf8')).toContain('<StressExplorerBanner />')
  })
})

/**
 * The explorer's files: no fraction parsed to a float, no hand-written payload type (the Done-when
 * grep), only the declared writes, and no run action before Phase 25.
 */
describe('explorer code hygiene (FE-24.2 Done-when)', () => {
  const root = join(__dirname, '..', '..', '..')
  const app = 'app/(platform)/advanced/stress-explorer'
  const files = [
    'components/advanced/scenarios.ts',
    'components/advanced/editor.ts',
    'components/advanced/InstrumentPicker.tsx',
    'components/advanced/ScenarioEditor.tsx',
    'components/advanced/ScenarioLibrary.tsx',
    'components/advanced/ScenarioRefusalNotice.tsx',
    'components/advanced/ScenarioStatusPill.tsx',
    'components/advanced/ScenarioView.tsx',
    'components/advanced/VersionActions.tsx',
    `${app}/layout.tsx`,
    `${app}/page.tsx`,
    `${app}/new/page.tsx`,
    `${app}/[scenarioId]/page.tsx`,
    `${app}/[scenarioId]/versions/new/page.tsx`,
    `${app}/[scenarioId]/versions/[version]/edit/page.tsx`,
  ]
  const read = (file: string) => readFileSync(join(root, file), 'utf8')
  const FLOAT = [/parseFloat/, /\bNumber\s*\(/, /\bNumber\./, /\.toFixed\s*\(/, /NumberFormat/]
  const HAND_WRITTEN =
    /\b(scenario_id|scenario_version_id|shock_index|shocks_sha256|affected_fraction|increase_fraction|delay_days|unavailable_days|cashflow_type|instrument_type|instrument_keys|approval_label|author_user_id|parent_version|latest_status|status|template)\??\s*:\s*(string|number|boolean|string\[\])\s*[;,}\n]/

  it.each(files)('%s never turns a fraction into a float', (file) => {
    for (const p of FLOAT) expect(read(file), `${file} ${p}`).not.toMatch(p)
  })

  it.each([...files, '../e2e/stress-explorer.spec.ts'])('%s declares no hand-written payload type', (file) => {
    expect(read(file)).not.toMatch(HAND_WRITTEN)
  })

  it('writes only through the declared lifecycle routes (close is deferred)', () => {
    const writes = (read('components/advanced/scenarios.ts').match(/api\.(POST|PUT|PATCH|DELETE)\('[^']+'/g) ?? []).sort()
    expect(writes).toEqual([
      "api.POST('/api/v1/scenarios'",
      "api.POST('/api/v1/scenarios/{scenario_id}/versions'",
      "api.POST('/api/v1/scenarios/{scenario_id}/versions/{version}/approval'",
      "api.POST('/api/v1/scenarios/{scenario_id}/versions/{version}/confirm'",
      "api.POST('/api/v1/scenarios/{scenario_id}/versions/{version}/submit'",
      "api.PUT('/api/v1/scenarios/{scenario_id}/versions/{version}'",
    ])
    for (const file of files.filter((f) => f !== 'components/advanced/scenarios.ts')) expect(read(file), file).not.toMatch(/api\.[A-Z]+\(/)
  })

  it('shows no run action before Phase 25, and no free-text formula field', () => {
    for (const file of files) {
      expect(read(file), file).not.toMatch(/calc:run|\/runs\b|Run scenario|Run exploration/)
      expect(read(file), file).not.toMatch(/formula\s*[:=]|name="formula"|<textarea[^>]*shock/i)
    }
  })

  it('the greps would catch a violation', () => {
    expect(HAND_WRITTEN.test('type V = { scenario_version_id: string }')).toBe(true)
    expect(HAND_WRITTEN.test('interface S {\n  affected_fraction: string\n}')).toBe(true)
    expect(FLOAT.some((p) => p.test('const f = parseFloat(shock.parameters.affected_fraction)'))).toBe(true)
  })
})
