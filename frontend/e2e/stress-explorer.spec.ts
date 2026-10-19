import { expect, test, type Page } from '@playwright/test'
import type { components } from '../src/api/schema'
import { API, actorTitle, bearer, signIn } from './support'

/**
 * Feature 05 Phase 24 browser check against the real stack (backend ENV=test, synthetic).
 * FE-24.2: the Agentic Stress-Test Explorer's library, editor, detail and review.
 *   1. risk_analyst creates "Two-week loan repayment delay" in the editor: DELAYED_INFLOWS on
 *      LOAN, 20%, 14 days, plus DEPOSIT_WITHDRAWAL on DEPOSIT, +15% (stored as exact text);
 *   2. confirms it, and the version shows "Exploration, not approved";
 *   3. submits it for approval;
 *   4. approver approves it with a comment, and it appears in the Shared library;
 *   5. risk_analyst sees no Approve button and a direct POST to the approval route gets a
 *      backend 403 (the role lacks scenario:approve).
 *
 * Every payload type is the generated one (src/api/schema.d.ts). Needs a backend with
 * SCENARIOS_ENABLED on (its default) and the seeded ACTIVE cashflow_direction rule (LOAN an
 * inflow, DEPOSIT an outflow), a frontend built with NEXT_PUBLIC_STRESS_EXPLORER_UI on (its
 * default) and a disposable database (D-03). Nothing runs: run actions arrive in Phase 25.
 */

type Schemas = components['schemas']

test.describe.configure({ mode: 'serial' })

const NAME = 'Two-week loan repayment delay'
const EXPLORER = '/advanced/stress-explorer'
const EXPLORATION = 'Exploration, not approved'
const APPROVED = 'Approved scenario (illustrative)'
const STATEMENT = 'Illustrative stress exploration, not a regulatory stress test'

let scenario: Schemas['ScenarioDetail'] | null = null

const posted = (page: Page, url: string) => page.waitForResponse((r) => r.request().method() === 'POST' && r.url() === url)
const versionUrl = () => `${API}/api/v1/scenarios/${scenario!.scenario_id}/versions/1`
const versionCard = (page: Page) => page.locator('[data-testid="scenario-version"][data-version="1"]')

test('FE-24.2: risk_analyst composes the two-shock scenario in the editor, confirms it for exploration and submits it', async ({ page }) => {
  await signIn(page, 'Risk analyst')
  await page.goto(EXPLORER)
  await expect(page.getByTestId('stress-explorer-banner')).toContainText(STATEMENT)
  // Q12: the shell layout owns the one D-44 §8 banner; pages never render a second.
  await expect(page.getByTestId('stress-explorer-banner')).toHaveCount(1)
  // risk_analyst holds scenario:propose, not scenario:approve: no Review tab.
  await expect(page.getByTestId('explorer-tab-shared')).toBeVisible()
  await expect(page.getByTestId('explorer-tab-review')).toHaveCount(0)
  await page.getByTestId('new-scenario').click()
  await page.waitForURL(`**${EXPLORER}/new`)
  await expect(page.locator('[data-testid="template-card"]')).toHaveCount(3)

  await page.getByTestId('scenario-name').fill(NAME)
  await page.getByTestId('add-DELAYED_INFLOWS').click()
  const delayed = page.locator('[data-testid="shock-card"][data-index="0"]')
  await expect(delayed).toHaveAttribute('data-template', 'DELAYED_INFLOWS')
  await delayed.getByTestId('field-parameters-cashflow-type').selectOption('LOAN')
  await delayed.getByTestId('field-parameters-affected-fraction').fill('20')
  await expect(delayed.getByTestId('stored-parameters-affected-fraction')).toHaveText('0.2000')
  // Bounds are checked as typed: 14 is in 1..3650, 0 is not.
  await delayed.getByTestId('field-parameters-delay-days').fill('0')
  await delayed.getByTestId('field-parameters-delay-days').blur()
  await expect(delayed.getByTestId('problem-parameters-delay-days')).toHaveText('Between 1 and 3650 calendar days.')
  await delayed.getByTestId('field-parameters-delay-days').fill('14')
  await expect(delayed.getByTestId('problem-parameters-delay-days')).toHaveCount(0)
  await expect(delayed.getByTestId('field-parameters-delay-calendar')).toHaveValue('CALENDAR_DAYS')

  await page.getByTestId('add-DEPOSIT_WITHDRAWAL').click()
  const withdrawal = page.locator('[data-testid="shock-card"][data-index="1"]')
  await expect(withdrawal).toHaveAttribute('data-template', 'DEPOSIT_WITHDRAWAL')
  await withdrawal.getByTestId('field-parameters-cashflow-type').selectOption('DEPOSIT')
  await withdrawal.getByTestId('field-parameters-increase-fraction').fill('15')
  await expect(withdrawal.getByTestId('stored-parameters-increase-fraction')).toHaveText('0.1500')

  const created = posted(page, `${API}/api/v1/scenarios`)
  await page.getByTestId('save-scenario').click()
  const response = await created
  expect(response.status(), await response.text()).toBe(201)
  const body: Schemas['ScenarioDetail'] = await response.json()
  scenario = body
  expect(body.name).toBe(NAME)
  expect(body.versions).toHaveLength(1)
  expect(body.versions[0].status).toBe('DRAFT')
  expect(body.versions[0].origin).toBe('MANUAL')
  // The backend echoes each shock with its (here empty) ShockSelector: every filter null.
  const anySelector: Schemas['ShockSelector'] = { currency: null, origin: null, due_from_day: null, due_to_day: null }
  expect(body.versions[0].shocks).toEqual([
    { template: 'DELAYED_INFLOWS', parameters: { cashflow_type: 'LOAN', affected_fraction: '0.2000', delay_days: 14, delay_calendar: 'CALENDAR_DAYS' }, selector: anySelector },
    { template: 'DEPOSIT_WITHDRAWAL', parameters: { cashflow_type: 'DEPOSIT', increase_fraction: '0.1500' }, selector: anySelector },
  ])
  await page.waitForURL(`**${EXPLORER}/${body.scenario_id}`)
  await expect(page.getByTestId('stress-explorer-banner')).toContainText(STATEMENT)
  // Q12: the shell layout owns the one D-44 §8 banner; pages never render a second.
  await expect(page.getByTestId('stress-explorer-banner')).toHaveCount(1)
  await expect(versionCard(page)).toHaveAttribute('data-status', 'DRAFT')
  await expect(versionCard(page).locator('[data-testid="version-shock"]')).toHaveText([
    /Delayed inflows: 20% of LOAN inflows move 14 calendar days later/,
    /Deposit withdrawal: DEPOSIT outflows \+15%/,
  ])
  await expect(versionCard(page).getByTestId('approval-label')).toHaveCount(0)

  // 2. Confirm for exploration: the author alone, no second person.
  await versionCard(page).getByTestId('version-confirm').click()
  const confirmed = posted(page, `${versionUrl()}/confirm`)
  await page.getByTestId('version-confirm-send').click()
  const confirmResponse = await confirmed
  expect(confirmResponse.status(), await confirmResponse.text()).toBe(200)
  const afterConfirm: Schemas['ScenarioVersionOut'] = await confirmResponse.json()
  expect(afterConfirm.status).toBe('CONFIRMED_FOR_EXPLORATION')
  expect(afterConfirm.approval_label).toBe(EXPLORATION)
  await expect(page.getByTestId('scenario-step-result')).toHaveAttribute('data-status', 'CONFIRMED_FOR_EXPLORATION')
  await expect(versionCard(page)).toHaveAttribute('data-status', 'CONFIRMED_FOR_EXPLORATION')
  await expect(versionCard(page).getByTestId('approval-label')).toHaveText(EXPLORATION)
  await expect(versionCard(page).getByTestId('version-status')).toHaveText('Confirmed for exploration')

  // An exploration is never in the shared library.
  const analyst = await bearer(page.request, 'risk_analyst')
  const library: Schemas['ScenarioPage'] = await (await page.request.get(`${API}/api/v1/scenarios?limit=200`, { headers: analyst })).json()
  expect(library.items.map((s) => s.scenario_id)).not.toContain(body.scenario_id)

  // 3. Submit for approval.
  await versionCard(page).getByTestId('version-submit').click()
  await page.getByTestId('submit-comment').fill('FE-24.2 e2e: please review the two-week delay with the deposit run-off.')
  const submitted = posted(page, `${versionUrl()}/submit`)
  await page.getByTestId('version-submit-send').click()
  const submitResponse = await submitted
  expect(submitResponse.status(), await submitResponse.text()).toBe(200)
  expect(((await submitResponse.json()) as Schemas['ScenarioVersionOut']).status).toBe('IN_REVIEW')
  await expect(versionCard(page)).toHaveAttribute('data-status', 'IN_REVIEW')
  await expect(versionCard(page).getByTestId('version-status')).toHaveText('In review')
  await expect(versionCard(page).getByTestId('version-actions')).toHaveAttribute('data-gate', 'self_approval')
  await expect(page.getByTestId('version-approve')).toHaveCount(0)
  await expect(page.getByTestId('version-reject')).toHaveCount(0)
})

test('FE-24.2: the approver approves it from the Review tab with a comment, and it appears in the Shared library', async ({ page }) => {
  expect(scenario, 'the analyst test created the scenario').toBeTruthy()
  await signIn(page, 'Approver')
  await page.goto(EXPLORER)
  await page.getByTestId('explorer-tab-review').click()
  await expect(page.getByTestId('scenario-library')).toHaveAttribute('data-tab', 'review')
  const row = page.locator(`[data-testid="scenario-row"][data-id="${scenario!.scenario_id}"]`)
  await expect(row).toHaveAttribute('data-status', 'IN_REVIEW')
  await row.getByTestId('scenario-link').click()
  await page.waitForURL(`**${EXPLORER}/${scenario!.scenario_id}`)

  await expect(versionCard(page).getByTestId('version-actions')).toHaveAttribute('data-gate', 'allowed')
  await versionCard(page).getByTestId('version-approve').click()
  const dialog = page.getByTestId('decision-dialog')
  await expect(dialog).toHaveAttribute('data-decision', 'APPROVE')
  // The comment is required: nothing can be sent while it is empty.
  await expect(dialog.getByTestId('decision-confirm')).toBeDisabled()
  const comment = 'FE-24.2 e2e: shocks and bounds checked; promoted for illustrative testing.'
  await dialog.getByTestId('decision-comment').fill(comment)
  const decided = posted(page, `${versionUrl()}/approval`)
  await dialog.getByTestId('decision-confirm').click()
  const response = await decided
  expect(response.status(), await response.text()).toBe(200)
  const approved: Schemas['ScenarioVersionOut'] = await response.json()
  expect(approved.status).toBe('APPROVED_FOR_TEST')
  expect(approved.approval_label).toBe(APPROVED)
  expect(approved.decided_by).toBeTruthy()
  await expect(dialog).toBeHidden()
  await expect(versionCard(page)).toHaveAttribute('data-status', 'APPROVED_FOR_TEST')
  // The status pill says it; the API's approval label is not repeated beside it (P-11).
  await expect(versionCard(page).getByTestId('version-status')).toHaveText('Approved for test')
  await expect(versionCard(page).getByTestId('approval-label')).toHaveCount(0)
  // The approver decided it: shown as "You", the raw actor kept in the tooltip (P-1).
  await expect(versionCard(page).getByTestId('version-decider')).toContainText('You')
  await expect(versionCard(page).getByTestId('version-decider').locator('[data-actor-kind]')).toHaveAttribute('title', actorTitle(approved.decided_by!))
  await expect(versionCard(page).getByTestId('version-decider')).toContainText(comment)

  // The Shared library (the default tab) now lists it, with its approved version.
  const listed = page.waitForResponse((r) => r.request().method() === 'GET' && r.url().startsWith(`${API}/api/v1/scenarios?`))
  await page.goto(EXPLORER)
  const libraryResponse = await listed
  expect(new URL(libraryResponse.url()).searchParams.getAll('status')).toEqual([])
  await expect(page.getByTestId('scenario-library')).toHaveAttribute('data-tab', 'shared')
  const shared = page.locator(`[data-testid="scenario-row"][data-id="${scenario!.scenario_id}"]`)
  await expect(shared).toHaveAttribute('data-status', 'APPROVED_FOR_TEST')
  await expect(shared.getByTestId('scenario-row-approved')).toHaveText('v1')
})

test('FE-24.2: risk_analyst sees no Approve button, and a direct POST to the approval route gets a backend 403', async ({ page }) => {
  expect(scenario, 'the analyst test created the scenario').toBeTruthy()
  await signIn(page, 'Risk analyst')
  await page.goto(`${EXPLORER}/${scenario!.scenario_id}`)
  await expect(versionCard(page)).toHaveAttribute('data-status', 'APPROVED_FOR_TEST')
  await expect(page.getByTestId('version-approve')).toHaveCount(0)
  await expect(page.getByTestId('version-reject')).toHaveCount(0)

  // The hidden button is cosmetic (D-04): the server refuses the decision itself.
  const analyst = await bearer(page.request, 'risk_analyst')
  const decision: Schemas['ScenarioVersionDecision'] = { decision: 'APPROVE', comment: 'FE-24.2 e2e: a direct POST by the author.' }
  const refused = await page.request.post(`${versionUrl()}/approval`, { headers: analyst, data: decision })
  expect(refused.status(), await refused.text()).toBe(403)
  const envelope: Schemas['ErrorEnvelope'] = await refused.json()
  expect(envelope.code).toBe('forbidden')
  const still: Schemas['ScenarioDetail'] = await (await page.request.get(`${API}/api/v1/scenarios/${scenario!.scenario_id}`, { headers: analyst })).json()
  expect(still.versions[0].status).toBe('APPROVED_FOR_TEST')
})
