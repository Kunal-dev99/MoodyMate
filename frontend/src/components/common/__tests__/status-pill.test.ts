import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { createElement, type FunctionComponent } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { ImplementationPill, RunStatusPill } from '@/components/calculations/RunStatusPill'
import { ValidationStatusBadge } from '@/components/calculations/ValidationStatusBadge'
import { IMPLEMENTATION_STYLE, RUN_STATUS_STYLE } from '@/components/calculations/calculations'
import { VALIDATION_STATUS_STYLE } from '@/components/calculations/validation'
import { StatusPill, type PillVariant } from '@/components/common/StatusPill'
import { RatingStatusPill } from '@/components/credit/ratings/RatingStatusPill'
import { RATING_STATUS_STYLE } from '@/components/credit/ratings/ratings'
import { ContractStatusPill } from '@/components/ingestion/ContractStatusPill'
import { DriftStatusPill, DriftTypePill } from '@/components/ingestion/DriftPills'
import { ImportStatusPill } from '@/components/ingestion/ImportStatusPill'
import { MappingStatusPill } from '@/components/ingestion/MappingStatusPill'
import { QuarantineStatusPill, SeverityPill } from '@/components/ingestion/QuarantineStatusPill'
import { ReconciliationItemStatusPill, ReconciliationOutcomePill } from '@/components/ingestion/ReconciliationPills'
import { DRIFT_STATUS_STYLE, DRIFT_TYPE_STYLE } from '@/components/ingestion/drift'
import { IMPORT_STATUS_STYLE } from '@/components/ingestion/imports'
import { MAPPING_STATUS_STYLE } from '@/components/ingestion/mappings'
import { ITEM_STATUS_STYLE, OUTCOME_STYLE } from '@/components/ingestion/reconciliation'
import { CONTRACT_STATUS_STYLE } from '@/components/ingestion/sources'
import { QUARANTINE_STATUS_STYLE, SEVERITY_STYLE } from '@/components/ingestion/validation'
import { VersionStatusPill } from '@/components/registry/RegistryBadges'
import { RULE_VERSION_STATUS_STYLE } from '@/components/registry/registry'
import { RuleVersionStatusPill } from '@/components/rules/RuleVersionStatusPill'
import { RULE_STATUS_STYLE } from '@/components/rules/rules'
import { ProposalStatusPill } from '@/components/schemas/ProposalStatusPill'
import { SchemaStatusPill } from '@/components/schemas/StatusPill'
import { PROPOSAL_STATUS_STYLE } from '@/components/schemas/proposals'
import { STATUS_STYLE } from '@/components/schemas/registry'
import { ScenarioStatusPill } from '@/components/advanced/ScenarioStatusPill'
import { SCENARIO_STATUS_STYLE } from '@/components/advanced/scenarios'

/*
 * FE-29.2: every pill is a thin enum → variant map over the one StatusPill. This loop is the
 * Done-when: each map covers its openapi.json enum exactly (no value missing, none invented),
 * and each wrapper renders every value with the server's value on its data attribute.
 */

const openapi = JSON.parse(readFileSync(fileURLToPath(new URL('../../../../../backend/openapi.json', import.meta.url)), 'utf8'))
const enumOf = (name: string): string[] => [...openapi.components.schemas[name].enum].sort()

/** A wrapper pill, rendered with its one value prop. */
type AnyPill = unknown

interface Case {
  pill: string
  /** The OpenAPI enum the map mirrors. */
  schema: string
  map: Readonly<Record<string, PillVariant>>
  render: AnyPill
  /** The prop the wrapper takes the value on. */
  prop: string
  testId: string
  attr: string
  titled: boolean
}

const CASES: Case[] = [
  { pill: 'SchemaStatusPill', schema: 'SchemaVersionStatus', map: STATUS_STYLE, render: SchemaStatusPill, prop: 'status', testId: 'status-pill', attr: 'status', titled: false },
  { pill: 'ProposalStatusPill', schema: 'ProposalStatus', map: PROPOSAL_STATUS_STYLE, render: ProposalStatusPill, prop: 'status', testId: 'proposal-status', attr: 'status', titled: false },
  { pill: 'ContractStatusPill', schema: 'ContractStatus', map: CONTRACT_STATUS_STYLE, render: ContractStatusPill, prop: 'status', testId: 'contract-status', attr: 'status', titled: false },
  { pill: 'ImportStatusPill', schema: 'ImportStatus', map: IMPORT_STATUS_STYLE, render: ImportStatusPill, prop: 'status', testId: 'import-status', attr: 'status', titled: false },
  { pill: 'MappingStatusPill', schema: 'MappingVersionStatus', map: MAPPING_STATUS_STYLE, render: MappingStatusPill, prop: 'status', testId: 'mapping-status', attr: 'status', titled: false },
  { pill: 'QuarantineStatusPill', schema: 'QuarantineStatus', map: QUARANTINE_STATUS_STYLE, render: QuarantineStatusPill, prop: 'status', testId: 'quarantine-status', attr: 'status', titled: false },
  { pill: 'SeverityPill', schema: 'ValidationSeverity', map: SEVERITY_STYLE, render: SeverityPill, prop: 'severity', testId: 'issue-severity', attr: 'severity', titled: false },
  { pill: 'DriftTypePill', schema: 'DriftType', map: DRIFT_TYPE_STYLE, render: DriftTypePill, prop: 'type', testId: 'drift-type', attr: 'drift-type', titled: true },
  { pill: 'DriftStatusPill', schema: 'DriftStatus', map: DRIFT_STATUS_STYLE, render: DriftStatusPill, prop: 'status', testId: 'drift-status', attr: 'status', titled: false },
  { pill: 'ReconciliationOutcomePill', schema: 'ReconciliationOutcome', map: OUTCOME_STYLE, render: ReconciliationOutcomePill, prop: 'outcome', testId: 'recon-outcome', attr: 'outcome', titled: false },
  { pill: 'ReconciliationItemStatusPill', schema: 'ReconciliationItemStatus', map: ITEM_STATUS_STYLE, render: ReconciliationItemStatusPill, prop: 'status', testId: 'recon-item-status', attr: 'status', titled: false },
  { pill: 'RuleVersionStatusPill', schema: 'RuleVersionStatus', map: RULE_STATUS_STYLE, render: RuleVersionStatusPill, prop: 'status', testId: 'rule-version-status', attr: 'status', titled: true },
  { pill: 'VersionStatusPill', schema: 'RuleVersionStatus', map: RULE_VERSION_STATUS_STYLE, render: VersionStatusPill, prop: 'status', testId: 'version-status', attr: 'status', titled: true },
  { pill: 'RunStatusPill', schema: 'CalculationRunStatus', map: RUN_STATUS_STYLE, render: RunStatusPill, prop: 'status', testId: 'run-status', attr: 'status', titled: true },
  { pill: 'ImplementationPill', schema: 'ImplementationStatus', map: IMPLEMENTATION_STYLE, render: ImplementationPill, prop: 'status', testId: 'module-implementation', attr: 'status', titled: false },
  { pill: 'ValidationStatusBadge', schema: 'ValidationStatus', map: VALIDATION_STATUS_STYLE, render: ValidationStatusBadge, prop: 'status', testId: 'validation-status', attr: 'validation-status', titled: true },
  { pill: 'RatingStatusPill', schema: 'ObligorRatingStatus', map: RATING_STATUS_STYLE, render: RatingStatusPill, prop: 'status', testId: 'rating-status', attr: 'status', titled: true },
  { pill: 'ScenarioStatusPill', schema: 'ScenarioVersionStatus', map: SCENARIO_STATUS_STYLE, render: ScenarioStatusPill, prop: 'status', testId: 'scenario-status', attr: 'status', titled: true },
]

const escape = (text: string) => text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#x27;')

describe('every pill map covers its openapi.json enum exactly (FE-29.2)', () => {
  for (const c of CASES) {
    it(`${c.pill} ↔ ${c.schema}`, () => {
      expect(Object.keys(c.map).sort()).toEqual(enumOf(c.schema))
      for (const value of enumOf(c.schema)) {
        const html = renderToStaticMarkup(createElement(c.render as FunctionComponent<Record<string, string>>, { [c.prop]: value }))
        const variant = c.map[value]
        expect(html).toContain(`data-testid="${c.testId}"`)
        expect(html).toContain(`data-${c.attr}="${value}"`)
        expect(html).toContain(`>${escape(variant.label)}</span>`)
        expect(html).toContain(variant.className)
        const help = variant.description ?? variant.meaning
        if (c.titled && help) expect(html).toContain(`title="${escape(help)}"`)
        if (!c.titled) expect(html).not.toContain('title=')
      }
    })
  }
})

describe('the one StatusPill (FE-29.2)', () => {
  it('shows a value its map does not know as sent, in the neutral style, never as success', () => {
    const html = renderToStaticMarkup(createElement(StatusPill, { value: 'NEW_STATUS', variant: undefined, testId: 'x' }))
    expect(html).toContain('status-draft')
    expect(html).toContain('data-status="NEW_STATUS"')
    expect(html).toContain('>NEW_STATUS</span>')
    expect(html).not.toContain('status-published')
  })

  it('writes no data attribute when asked for none, and keeps an explicit title', () => {
    const html = renderToStaticMarkup(createElement(StatusPill, { value: 'added', variant: { label: 'Added', className: 'status-published' }, attr: null, title: 'why' }))
    expect(html).toBe('<span class="inline-flex items-center rounded-sm border px-2 py-0.5 text-xs font-medium status-published" title="why">Added</span>')
  })
})
