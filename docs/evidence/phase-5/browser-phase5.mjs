// Phase 5 browser verification (FE-5.1, FE-5.2): the full proposal flow with two seeded
// users (data_steward proposes, approver decides), against the real backend (dev mode).
import { chromium } from 'playwright'
const OUT = process.argv[2]
const BASE = 'http://localhost:3000'
const API = 'http://localhost:8000'
const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' })
const r = {}
async function as(label) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } })
  await page.goto(`${BASE}/login`)
  await page.getByRole('button', { name: new RegExp(label) }).click()
  await page.waitForURL('**/dashboard')
  return page
}
const token = (page) =>
  page.evaluate(() => JSON.parse(sessionStorage.getItem('moodymate_session') ?? '{}').state?.token ?? null)
async function apiPost(page, path, body) {
  const tok = await token(page)
  return page.evaluate(
    async ([url, t, b]) => {
      const res = await fetch(url, { method: 'POST', headers: { Authorization: `Bearer ${t}`, 'Content-Type': 'application/json' }, body: JSON.stringify(b) })
      return { status: res.status, body: await res.json() }
    },
    [`${API}${path}`, tok, body],
  )
}
async function apiGet(page, path) {
  const tok = await token(page)
  return page.evaluate(
    async ([url, t]) => (await fetch(url, { headers: { Authorization: `Bearer ${t}` } })).json(),
    [`${API}${path}`, tok],
  )
}

// 1. data_steward proposes removing counterparty_id from instrument
const steward = await as('Data steward')
await steward.goto(`${BASE}/schemas`)
await steward.getByTestId('schema-row').filter({ hasText: 'instrument' }).getByRole('link').click()
await steward.getByRole('link', { name: 'Propose change' }).click()
await steward.getByTestId('field-row').first().waitFor()
await steward.getByLabel('Title').fill('Drop counterparty_id from instrument')
await steward.getByLabel('Rationale').fill('Counterparty moves to the position level.')
await steward.getByRole('button', { name: 'Remove counterparty_id' }).click()
r.editor_rows_after_remove = await steward.getByTestId('field-row').count()
await steward.screenshot({ path: `${OUT}/01-new-proposal.png`, fullPage: true })
await steward.getByRole('button', { name: 'Create proposal' }).click()
await steward.waitForURL('**/schemas/proposals/**')
await steward.getByTestId('submit-proposal').waitFor()
const proposalId = steward.url().split('/').pop()
// 2. Submit is disabled before the analysis, and the backend agrees (409)
r.submit_disabled_before_analysis = await steward.getByTestId('submit-proposal').isDisabled()
r.impact_missing_shown = await steward.getByTestId('impact-missing').isVisible()
r.backend_submit_before_analysis = await apiPost(steward, `/api/v1/schema-change-proposals/${proposalId}/submit`, {})
await steward.screenshot({ path: `${OUT}/02-draft-submit-disabled.png`, fullPage: true })
// 3. Run the impact analysis: BREAKING red badge and the dependents (FE-5.2)
await steward.getByRole('button', { name: 'Run impact analysis' }).click()
await steward.getByTestId('impact-panel').waitFor()
const overall = steward.getByTestId('impact-overall').getByTestId('compatibility-badge')
r.impact_overall = await overall.getAttribute('data-compatibility')
r.impact_overall_class = await overall.getAttribute('class')
r.impact_dependents = await steward.getByTestId('impact-dependent').allInnerTexts()
r.submit_enabled_after_analysis = await steward.getByTestId('submit-proposal').isEnabled()
await steward.screenshot({ path: `${OUT}/03-impact-analysis.png`, fullPage: true })
await steward.getByPlaceholder(/Regression evidence/).fill('Manual regression run, 2026-10-16')
await steward.getByTestId('submit-proposal').click()
await steward.getByTestId('awaiting-approver').waitFor()
r.after_submit_status = await steward.getByTestId('proposal-status').getAttribute('data-status')
r.proposer_sees = await steward.getByTestId('awaiting-approver').innerText()

// 4. approver finds it in the queue and approves
const approver = await as('Approver')
await approver.goto(`${BASE}/schemas/proposals`)
await approver.getByTestId('proposal-row').first().waitFor()
r.approver_queue = await approver.getByTestId('proposal-row').allInnerTexts()
await approver.screenshot({ path: `${OUT}/04-approver-queue.png`, fullPage: true })
await approver.getByRole('link', { name: 'Drop counterparty_id from instrument' }).click()
await approver.getByTestId('approve-proposal').waitFor()
await approver.getByLabel('Decision comment').fill('Consumers confirmed in the data forum.')
await approver.getByTestId('approve-proposal').click()
await approver.locator('[data-testid="proposal-status"][data-status="APPLIED"]').waitFor()
r.after_approval_status = 'APPLIED'
await approver.screenshot({ path: `${OUT}/05-applied.png`, fullPage: true })
const instrument = (await apiGet(approver, '/api/v1/schemas')).items.find((s) => s.code === 'instrument')
const versions = await apiGet(approver, `/api/v1/schemas/${instrument.id}/versions`)
r.versions_after_approval = versions.items.map((v) => `v${v.version} ${v.status}`)
const v1hash = versions.items.find((v) => v.version === 1).content_sha256

// 5. Revert: the steward proposes it, the approver approves it
await steward.goto(`${BASE}/schemas/proposals/${proposalId}`)
await steward.getByRole('button', { name: 'Propose revert' }).click()
await steward.waitForURL((u) => !u.pathname.endsWith(proposalId))
await steward.getByRole('button', { name: 'Run impact analysis' }).click()
await steward.getByTestId('impact-panel').waitFor()
r.revert_impact = await steward.getByTestId('impact-overall').getByTestId('compatibility-badge').getAttribute('data-compatibility')
await steward.getByTestId('submit-proposal').click()
await steward.getByTestId('awaiting-approver').waitFor()
const revertId = steward.url().split('/').pop()
await approver.goto(`${BASE}/schemas/proposals/${revertId}`)
await approver.getByTestId('approve-proposal').click()
await approver.locator('[data-testid="proposal-status"][data-status="APPLIED"]').waitFor()
const after = await apiGet(approver, `/api/v1/schemas/${instrument.id}/versions`)
r.versions_after_revert = after.items.map((v) => `v${v.version} ${v.status}`)
const active = after.items.filter((v) => v.status === 'ACTIVE')
r.active_hash_equals_original = active.length === 1 && active[0].content_sha256 === v1hash
await approver.goto(`${BASE}/schemas/${instrument.id}`)
await approver.getByTestId('version-row').first().waitFor()
await approver.screenshot({ path: `${OUT}/06-versions-after-revert.png`, fullPage: true })
console.log(JSON.stringify(r, null, 2))
await browser.close()
