import { expect, test, type APIRequestContext, type Page } from '@playwright/test'
import type { components } from '../src/api/schema'
import { API, bearer, cashflowImportReady, ensureActiveMapping, registerInstrumentSource, signIn, uploadSample, validateAndProject } from './support'

/**
 * Feature 03 Phase 16 browser checks against the real stack (backend ENV=test, synthetic).
 * FE-16.2: risk_analyst proposes new bucket boundaries for `maturity_buckets` in the typed
 *          editor and submits the DRAFT; the author sees no Approve button, and the server
 *          refuses the author's direct POST; the approver approves with a required comment; a
 *          run submitted afterwards shows the new version in its manifest while a run submitted
 *          before still shows the old one.
 *
 * Every payload type is the generated one (src/api/schema.d.ts); none is written here.
 *
 * Setup builds its own projected instrument and cash-flow imports and their dataset snapshot,
 * as calculations.spec does (shared helpers in support.ts), so the file also runs alone.
 * Ordering: it sorts before smoke.spec, whose applied proposal moves the `instrument` schema
 * past v1, which projector v1 refuses. It changes the seeded bucket rule, so run it on a fresh
 * database (CI does; locally a disposable copy, D-03).
 */

type Schemas = components['schemas']

test.describe.configure({ mode: 'serial' })

const RULE = 'maturity_buckets'
const MODULE = 'maturity_ladder'
const REPORTING_DATE = '2026-12-31'

let ruleId = ''
let oldVersion: Schemas['RuleVersionOut'] | null = null
let proposed: Schemas['RuleVersionOut'] | null = null
let oldRun: Schemas['RunDetail'] | null = null
let snapshotId = ''
let moduleVersionId = ''

const isPost = (page: Page, url: string) => page.waitForResponse((r) => r.request().method() === 'POST' && r.url() === url)

async function ruleDetail(request: APIRequestContext, headers: Record<string, string>): Promise<Schemas['RuleDetail']> {
  const r = await request.get(`${API}/api/v1/rules/${ruleId}`, { headers })
  expect(r.status(), await r.text()).toBe(200)
  return r.json()
}

/** POST /calculation-runs on the shared snapshot, as risk_analyst: what the run pins now. */
async function submitRun(request: APIRequestContext): Promise<Schemas['RunDetail']> {
  const body: Schemas['RunCreate'] = { dataset_snapshot_id: snapshotId, module_version_id: moduleVersionId, reporting_date: REPORTING_DATE }
  const r = await request.post(`${API}/api/v1/calculation-runs`, {
    headers: { ...(await bearer(request, 'risk_analyst')), 'Idempotency-Key': `e2e-rules-${Date.now()}-${Math.random().toString(16).slice(2)}` },
    data: body,
  })
  expect(r.status(), await r.text()).toBe(201)
  const run: Schemas['RunDetail'] = await r.json()
  expect(['SUCCEEDED', 'SUCCEEDED_WITH_WARNINGS']).toContain(run.status)
  return run
}

const pinnedBuckets = (run: Schemas['RunDetail']) => run.manifest.rule_set.members.find((m) => m.rule_code === RULE)

/**
 * A bound of bucket `i` of a DAY_BUCKETS version (null: the open last bucket). The contract types
 * `parameters` as an open object (its kind is checked server-side, D-32 §1), so the value is
 * checked here rather than typed by hand.
 */
function bound(v: Schemas['RuleVersionOut'], i: number, key: 'lower_exclusive' | 'upper_inclusive'): number | null {
  const buckets = v.parameters.buckets
  expect(Array.isArray(buckets), 'DAY_BUCKETS parameters hold a buckets list').toBe(true)
  const value = (buckets as Record<string, unknown>[])[i]?.[key]
  expect(value === null || typeof value === 'number', `bucket ${i} ${key} is a JSON integer or null`).toBe(true)
  return value as number | null
}
const bucketCount = (v: Schemas['RuleVersionOut']): number => (Array.isArray(v.parameters.buckets) ? v.parameters.buckets.length : 0)

test('setup: the ACTIVE maturity_buckets version and a run that pins it', async ({ request }) => {
  const analyst = await bearer(request, 'risk_analyst')
  const rules: Schemas['RulePage'] = await (await request.get(`${API}/api/v1/rules`, { headers: analyst })).json()
  const rule = rules.items.find((r) => r.code === RULE)
  expect(rule, 'maturity_buckets is seeded (D-35)').toBeTruthy()
  ruleId = rule!.rule_id
  const detail = await ruleDetail(request, analyst)
  expect(detail.parameter_schema.kind).toBe('DAY_BUCKETS')
  expect(detail.change_enabled, 'RULE_CHANGE_ENABLED is on').toBe(true)
  oldVersion = detail.versions.find((v) => v.status === 'ACTIVE') ?? null
  expect(oldVersion).not.toBeNull()

  // A dataset snapshot of projected synthetic instruments and cash flows (not under test).
  const steward = await bearer(request, 'data_steward')
  const { sourceId, contractId } = await registerInstrumentSource(request, steward)
  const instruments: Schemas['ImportDetail'] = await uploadSample(request, steward, sourceId, 'instrument.csv')
  expect(instruments.schema_version, 'projector v1 supports instrument v1 only: run before smoke.spec').toBe(1)
  await ensureActiveMapping(request, contractId, sourceId, instruments.id, `e2e_rules_instr_${Date.now()}`)
  await validateAndProject(request, steward, instruments.id)
  const cashflows = await cashflowImportReady(request, steward)
  await validateAndProject(request, steward, cashflows)
  const snapshotBody: Schemas['SnapshotCreate'] = { import_ids: [instruments.id, cashflows] }
  const built = await request.post(`${API}/api/v1/dataset-snapshots`, { headers: analyst, data: snapshotBody })
  expect([200, 201], await built.text()).toContain(built.status())
  snapshotId = ((await built.json()) as Schemas['SnapshotOut']).snapshot_id
  const modules: Schemas['ModulePage'] = await (await request.get(`${API}/api/v1/calculation-modules`, { headers: analyst })).json()
  const ladder = modules.items.find((m) => m.code === MODULE && m.implementation_status === 'AVAILABLE')
  expect(ladder?.runnable, 'maturity_ladder v1 is seeded AVAILABLE with every rule ACTIVE').toBe(true)
  moduleVersionId = ladder!.module_version_id

  oldRun = await submitRun(request)
  expect(pinnedBuckets(oldRun)?.rule_version_id).toBe(oldVersion!.rule_version_id)
})

test('FE-16.2: risk_analyst proposes new bucket boundaries and submits; the author has no Approve button and the server refuses a direct POST', async ({
  page,
  request,
}) => {
  expect(oldVersion).not.toBeNull()
  const count = bucketCount(oldVersion!)
  expect(count).toBeGreaterThan(2)
  // Move the second bucket's upper bound, inside (its lower, the third's upper), to a new value.
  const lower = bound(oldVersion!, 1, 'lower_exclusive')!
  const current = bound(oldVersion!, 1, 'upper_inclusive')
  const cap = bound(oldVersion!, 2, 'upper_inclusive') ?? Infinity
  const next = [14, 7, 10, 20, 3].find((c) => c > lower && c < cap && c !== current)
  expect(next).toBeDefined()

  await signIn(page, 'Risk analyst')
  await page.goto(`/domain/rules/${ruleId}/propose`)
  const editor = page.getByTestId('parameter-editor')
  await expect(editor).toHaveAttribute('data-kind', 'DAY_BUCKETS')
  await expect(page.getByTestId('rule-propose')).toHaveAttribute('data-base-version', String(oldVersion!.version))
  await expect(editor.getByTestId('bucket-row')).toHaveCount(count)
  // Nothing changed yet: proposing is disabled (the server would answer 422 no_change).
  await expect(page.getByTestId('propose-submit')).toBeDisabled()

  const second = editor.locator('[data-testid="bucket-row"][data-index="1"]')
  const third = editor.locator('[data-testid="bucket-row"][data-index="2"]')
  await second.getByTestId('bucket-upper').fill(String(next))
  // Contiguous by construction: the third bucket now starts where the second ends.
  await expect(third.getByTestId('bucket-lower')).toHaveText(String(next))
  await expect(second.getByTestId('bucket-label')).toHaveText(`(${lower},${next}]`)
  await page.getByTestId('change-summary').fill(`e2e: second bucket ends at day ${next}`)
  await expect(page.getByTestId('parameter-problem')).toHaveCount(0)

  // Propose: a DRAFT whose buckets carry the change, authored by the analyst.
  const created = isPost(page, `${API}/api/v1/rules/${ruleId}/versions`)
  await page.getByTestId('propose-submit').click()
  const createdResponse = await created
  expect(createdResponse.status(), await createdResponse.text()).toBe(201)
  const sent: Schemas['RuleVersionCreate'] = createdResponse.request().postDataJSON()
  expect(Object.keys(sent.parameters_patch)).toEqual(['buckets'])
  proposed = await createdResponse.json()
  expect(proposed!.status).toBe('DRAFT')
  expect(proposed!.version).toBeGreaterThan(oldVersion!.version)
  expect(bound(proposed!, 1, 'upper_inclusive')).toBe(next)
  expect(bound(proposed!, 2, 'lower_exclusive')).toBe(next)
  await expect(page.getByTestId('propose-result')).toHaveAttribute('data-status', 'DRAFT')

  // Submit the DRAFT from the open versions.
  const mine = page.locator(`[data-testid="open-version"][data-version="${proposed!.version}"]`)
  await expect(mine).toHaveAttribute('data-status', 'DRAFT')
  const submitted = isPost(page, `${API}/api/v1/rules/${ruleId}/versions/${proposed!.version}/submit`)
  await mine.getByTestId('version-submit').click()
  const submittedResponse = await submitted
  expect(submittedResponse.status(), await submittedResponse.text()).toBe(200)
  expect((await submittedResponse.json()).status).toBe('IN_REVIEW')
  await expect(page.getByTestId('rule-step-result')).toHaveAttribute('data-status', 'IN_REVIEW')
  await expect(mine).toHaveAttribute('data-status', 'IN_REVIEW')

  // The author sees no Approve button, here and on the rule page, and is told why.
  await expect(mine.getByTestId('version-approve')).toHaveCount(0)
  await expect(mine.getByTestId('self-approval-blocked')).toBeVisible()
  await page.goto(`/domain/rules/${ruleId}`)
  const onRule = page.locator(`[data-testid="open-version"][data-version="${proposed!.version}"]`)
  await expect(onRule).toHaveAttribute('data-status', 'IN_REVIEW')
  await expect(onRule).toHaveAttribute('data-mine', 'true')
  await expect(onRule.getByTestId('version-approve')).toHaveCount(0)
  await expect(onRule.getByTestId('self-approval-blocked')).toBeVisible()

  // The hidden button is cosmetic: the server refuses the author's direct POST (D-04) and the
  // version stays IN_REVIEW. Under D-34 the author's role (risk_analyst) holds no rule:approve,
  // so the permission check answers (403 forbidden) before the self-approval check (409
  // self_approval, asserted for a two-role author in test_rules.py).
  const analyst = await bearer(request, 'risk_analyst')
  const decision: Schemas['RuleVersionDecision'] = { decision: 'APPROVE', comment: 'e2e: the author tries to approve' }
  const refused = await request.post(`${API}/api/v1/rules/${ruleId}/versions/${proposed!.version}/approval`, { headers: analyst, data: decision })
  expect(refused.status(), await refused.text()).toBe(403)
  const envelope: Schemas['ErrorEnvelope'] = await refused.json()
  expect(envelope.code).toBe('forbidden')
  const after = await ruleDetail(request, analyst)
  expect(after.versions.find((v) => v.version === proposed!.version)?.status).toBe('IN_REVIEW')
})

test('FE-16.2: the approver approves with a comment; a new run pins the new version and the old run keeps the old one', async ({ page, request }) => {
  expect(proposed).not.toBeNull()
  expect(oldRun).not.toBeNull()

  await signIn(page, 'Approver')
  await page.goto(`/domain/rules/${ruleId}`)
  const version = page.locator(`[data-testid="open-version"][data-version="${proposed!.version}"]`)
  await expect(version).toHaveAttribute('data-status', 'IN_REVIEW')
  await expect(version).toHaveAttribute('data-mine', 'false')
  await version.getByTestId('version-approve').click()
  const dialog = page.getByTestId('decision-dialog')
  await expect(dialog).toHaveAttribute('data-decision', 'APPROVE')
  // The comment is required.
  await expect(dialog.getByTestId('decision-confirm')).toBeDisabled()
  await dialog.getByTestId('decision-comment').fill('e2e: boundaries checked against the illustrative ladder')
  const approved = isPost(page, `${API}/api/v1/rules/${ruleId}/versions/${proposed!.version}/approval`)
  await dialog.getByTestId('decision-confirm').click()
  const approvedResponse = await approved
  expect(approvedResponse.status(), await approvedResponse.text()).toBe(200)
  const sent: Schemas['RuleVersionDecision'] = approvedResponse.request().postDataJSON()
  expect(sent.decision).toBe('APPROVE')
  const active: Schemas['RuleVersionOut'] = await approvedResponse.json()
  expect(active.status).toBe('ACTIVE')
  await expect(page.getByTestId('rule-step-result')).toHaveAttribute('data-status', 'ACTIVE')
  await expect(version).toHaveCount(0) // no longer open

  // Exactly one ACTIVE version, the new one; the old one SUPERSEDED (never deleted).
  const detail = await ruleDetail(request, await bearer(request, 'approver'))
  expect(detail.versions.filter((v) => v.status === 'ACTIVE').map((v) => v.version)).toEqual([proposed!.version])
  expect(detail.versions.find((v) => v.rule_version_id === oldVersion!.rule_version_id)?.status).toBe('SUPERSEDED')

  // A run submitted now pins the new version; its manifest shows it.
  const newRun = await submitRun(request)
  expect(pinnedBuckets(newRun)?.rule_version_id).toBe(active.rule_version_id)
  expect(newRun.rule_set_id).not.toBe(oldRun!.rule_set_id)
  await page.goto(`/calculations/runs/${newRun.run_id}`)
  const newPin = page.locator(`[data-testid="manifest-rule"][data-rule="${RULE}"]`)
  await expect(newPin).toHaveAttribute('data-rule-version-id', active.rule_version_id)
  await expect(newPin).toContainText(`v${active.version}`)

  // The run submitted before still shows the old version, and its pins and hashes are unchanged.
  await page.goto(`/calculations/runs/${oldRun!.run_id}`)
  const oldPin = page.locator(`[data-testid="manifest-rule"][data-rule="${RULE}"]`)
  await expect(oldPin).toHaveAttribute('data-rule-version-id', oldVersion!.rule_version_id)
  await expect(oldPin).toContainText(`v${oldVersion!.version}`)
  await expect(page.getByTestId('manifest-sha256')).toHaveText(oldRun!.manifest_sha256)
  await expect(page.getByTestId('result-sha256')).toHaveText(oldRun!.result_sha256!)
})
