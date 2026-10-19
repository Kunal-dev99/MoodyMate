import { expect, test } from '@playwright/test'
import type { components } from '../src/api/schema'
import { API, actorTitle, bearer, signIn } from './support'

/**
 * Feature 03 Phase 16 browser checks against the real stack (backend ENV=test, synthetic).
 * FE-16.1: from the Financial Domain tabs, risk_analyst opens the seeded `maturity_buckets` rule
 *          and sees its ILLUSTRATIVE badge, the D-35 statement and its version history (status,
 *          author, "used by N runs" per version, as GET /rules/{id} reports them), the ACTIVE
 *          version's buckets with the open last bucket, and the parameter diff. The auditor
 *          browses the module registry (AVAILABLE and PLANNED apart, PLANNED never ready) and
 *          the assumption sets, read-only.
 *
 * Every payload type is the generated one (src/api/schema.d.ts); none is written here.
 * It only reads, so it runs anywhere in the suite: rules.spec (FE-16.2) adds versions later,
 * and this spec compares with whatever versions the API reports.
 */

type Schemas = components['schemas']

const RULE = 'maturity_buckets'

test('FE-16.1: the seeded maturity_buckets rule shows its ILLUSTRATIVE badge and its version history', async ({ page, request }) => {
  const analyst = await bearer(request, 'risk_analyst')
  const listed = await request.get(`${API}/api/v1/rules`, { headers: analyst })
  expect(listed.status()).toBe(200)
  const list: Schemas['RulePage'] = await listed.json()
  const summary = list.items.find((r) => r.code === RULE)
  expect(summary, `${RULE} is seeded by migration 0018`).toBeTruthy()
  expect(summary!.illustrative).toBe(true)
  const detail: Schemas['RuleDetail'] = await (await request.get(`${API}/api/v1/rules/${summary!.rule_id}`, { headers: analyst })).json()
  const active = detail.versions.find((v) => v.status === 'ACTIVE')
  expect(active, 'the rule has an ACTIVE version').toBeTruthy()

  await signIn(page, 'Risk analyst')
  await page.goto('/domain')
  await page.getByTestId('domain-tab-rules').click()
  await page.waitForURL('**/domain/rules')
  await expect(page.getByTestId('rule-row')).toHaveCount(list.items.length)
  const row = page.locator(`[data-testid="rule-row"][data-code="${RULE}"]`)
  await expect(row.getByTestId('illustrative-badge')).toBeVisible()
  await expect(row.getByTestId('rule-version-count')).toHaveText(String(detail.version_count))
  await row.getByTestId('rule-link').click()
  await page.waitForURL(`**/domain/rules/${summary!.rule_id}`)

  // The ILLUSTRATIVE badge and the D-35 label.
  await expect(page.getByTestId('rule-detail')).toHaveAttribute('data-code', RULE)
  await expect(page.getByTestId('rule-labels').getByTestId('illustrative-badge')).toHaveText('Illustrative')
  await expect(page.getByTestId('illustrative-banner')).toContainText(detail.statement)
  await expect(page.getByTestId('parameter-kind')).toHaveText('DAY_BUCKETS')

  // The version history: every version the API lists, with its status, author and run count.
  const history = page.getByTestId('rule-versions')
  await expect(history.getByTestId('rule-version-row')).toHaveCount(detail.versions.length)
  for (const v of detail.versions) {
    const r = history.locator(`[data-testid="rule-version-row"][data-version="${v.version}"]`)
    await expect(r).toHaveAttribute('data-status', v.status)
    await expect(r.getByTestId('rule-version-status')).toHaveAttribute('data-status', v.status)
    // The author reads in plain language (P-1 / blocker 3); the raw actor stays in the tooltip.
    const author = r.getByTestId('rule-version-author').locator('[data-actor-kind]')
    await expect(author).toHaveAttribute('title', actorTitle(v.authored_by))
    if (v.seeded) await expect(author).toHaveText('Platform seed (illustrative)')
    await expect(r.getByTestId('rule-version-run-count')).toHaveAttribute('data-run-count', String(v.run_count))
    if (v.seeded) await expect(r.getByTestId('seeded-badge')).toBeVisible()
  }
  expect(detail.versions.find((v) => v.version === 1)?.seeded, 'v1 is the seeded version (D-32 §7)').toBe(true)

  // The ACTIVE version opens: its hash, its buckets (the last one open) and "used by N runs".
  const shown = page.getByTestId('rule-version-detail')
  await expect(shown).toHaveAttribute('data-version', String(active!.version))
  await expect(shown.getByTestId('rule-version-sha256')).toHaveText(active!.parameters_sha256)
  const buckets = active!.parameters.buckets
  expect(Array.isArray(buckets)).toBe(true)
  await expect(shown.getByTestId('parameter-bucket')).toHaveCount((buckets as unknown[]).length)
  await expect(shown.getByTestId('parameter-bucket-interval').last()).toContainText('∞')
  await expect(shown.getByTestId('rule-version-used-by')).toHaveText(
    active!.run_count === 0 ? 'Not used by any run' : `Used by ${active!.run_count} run${active!.run_count === 1 ? '' : 's'}`,
  )
  await expect(shown.getByTestId('pinning-run')).toHaveCount(active!.runs.length)

  // The parameter diff: nothing to compare with one version, else the default pair's changes.
  if (detail.versions.length < 2) await expect(page.getByTestId('parameter-diff-single')).toBeVisible()
  else await expect(page.getByTestId('parameter-diff').or(page.getByTestId('parameter-diff-none'))).toBeVisible()

  // Another version can be opened from the history.
  const first = history.locator('[data-testid="rule-version-row"][data-version="1"]')
  await first.getByTestId('rule-version-show').click()
  await expect(shown).toHaveAttribute('data-version', '1')
})

test('FE-16.1: the auditor browses the modules (PLANNED never ready) and the assumption sets, read-only', async ({ page, request }) => {
  const auditor = await bearer(request, 'auditor')
  const modules: Schemas['ModulePage'] = await (await request.get(`${API}/api/v1/calculation-modules`, { headers: auditor })).json()
  const sets = await request.get(`${API}/api/v1/assumption-sets`, { headers: auditor })
  expect(sets.status()).toBe(200)
  const assumptionSets: Schemas['AssumptionSetList'] = await sets.json()
  const baseline = assumptionSets.items.find((a) => a.code === 'baseline' && a.status === 'ACTIVE')
  expect(baseline, 'the baseline assumption set is seeded ACTIVE').toBeTruthy()

  await signIn(page, 'Auditor')
  await page.goto('/domain/modules')
  for (const status of ['AVAILABLE', 'PLANNED'] as const) {
    const expected = modules.items.filter((m) => m.implementation_status === status)
    if (expected.length === 0) await expect(page.getByTestId(`modules-none-${status}`)).toBeVisible()
    else await expect(page.getByTestId(`modules-${status}`).getByTestId('registry-module')).toHaveCount(expected.length)
  }
  const ladder = page.locator('[data-testid="registry-module"][data-code="maturity_ladder"]')
  await expect(ladder).toHaveAttribute('data-status', 'AVAILABLE')
  await expect(ladder.getByTestId('module-implementation')).toHaveText('Available · illustrative prototype')
  await expect(ladder.getByTestId('illustrative-badge')).toBeVisible()
  const planned = page.locator('[data-testid="registry-module"][data-status="PLANNED"]')
  for (const card of await planned.all()) {
    // FE-18.1: the shared PlannedModuleBadge, never the implementation pill.
    await expect(card.getByTestId('planned-module-badge')).toHaveText('Planned — extension point')
    await expect(card.getByTestId('module-implementation')).toHaveCount(0)
    await expect(card.getByTestId('module-contracts')).toBeVisible()
    await expect(card.getByTestId('module-planned-note')).toContainText('not production-ready')
    await expect(card.getByTestId('module-runnable')).toHaveCount(0)
  }

  await page.getByTestId('domain-tab-assumption-sets').click()
  await page.waitForURL('**/domain/assumption-sets')
  await expect(page.getByTestId('assumption-set-row')).toHaveCount(assumptionSets.items.length)
  const row = page.locator(`[data-testid="assumption-set-row"][data-assumption-set-id="${baseline!.assumption_set_id}"]`)
  await expect(row.getByTestId('assumption-set-status')).toHaveAttribute('data-status', 'ACTIVE')
  await row.getByTestId('assumption-set-link').click()
  await page.waitForURL(`**/domain/assumption-sets/${baseline!.assumption_set_id}`)
  await expect(page.getByTestId('assumption-set-id')).toHaveText(baseline!.assumption_set_id)
  await expect(page.getByTestId('assumption-set-provenance')).toHaveText(baseline!.provenance)
  await expect(page.getByTestId('assumption-set-labels').getByTestId('illustrative-badge')).toHaveCount(baseline!.illustrative ? 1 : 0)
})
