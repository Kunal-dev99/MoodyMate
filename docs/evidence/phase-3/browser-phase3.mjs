// Phase 3 browser verification (FE-3.1, FE-3.2) against the real backend (dev mode).
import { chromium } from 'playwright'
const OUT = process.argv[2]
const BASE = 'http://localhost:3000'
const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' })
const r = {}
async function as(role, label) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
  await page.goto(`${BASE}/login`)
  await page.getByRole('button', { name: new RegExp(label) }).click()
  await page.waitForURL('**/dashboard')
  return page
}
// 1. A successful privileged action (platform_admin lists users)
let p = await as('platform_admin', 'Platform administrator')
let successCid = null
p.on('response', (res) => { if (res.url().endsWith('/api/v1/users?limit=100')) successCid = res.headers()['x-correlation-id'] })
await p.goto(`${BASE}/access/users`); await p.locator('table').waitFor(); await p.waitForTimeout(300)
await p.close()
// 2. A denied action (risk_analyst opens the users page)
p = await as('risk_analyst', 'Risk analyst')
let deniedCid = null
p.on('response', (res) => { if (res.url().includes('/api/v1/users') && res.status() === 403) deniedCid = res.headers()['x-correlation-id'] })
await p.goto(`${BASE}/access/users`); await p.getByText("You don't have access to this").waitFor(); await p.waitForTimeout(300)
// risk_analyst cannot see the audit page (no nav item, 403 state)
r.risk_analyst_nav_has_audit = (await p.locator('aside nav a').allInnerTexts()).includes('Audit')
await p.goto(`${BASE}/audit`); await p.getByText("You don't have access to this").waitFor()
r.risk_analyst_audit_page = 'forbidden'
await p.close()
r.success_cid = successCid; r.denied_cid = deniedCid
// 3. Auditor views the timeline: both events visible with their correlation IDs
p = await as('auditor', 'Auditor')
await p.goto(`${BASE}/audit`)
await p.getByTestId('audit-event').first().waitFor()
const cids = await p.getByTestId('correlation-id').allInnerTexts()
r.timeline_shows_success_cid = cids.includes(successCid)
r.timeline_shows_denied_cid = cids.includes(deniedCid)
const deniedRow = p.getByTestId('audit-event').filter({ hasText: deniedCid })
r.denied_row = await deniedRow.innerText()
await p.screenshot({ path: `${OUT}/01-audit-timeline-auditor.png`, fullPage: true })
// 4. FE-3.2: masked confidential detail for the auditor, with tooltip
const prov = p.getByTestId('audit-event').filter({ hasText: 'e2e-provision-fixture' })
const masked = prov.getByTestId('masked-value')
r.auditor_provision_row = await prov.innerText()
r.auditor_email_masked_text = await masked.innerText()
r.auditor_sees_raw_email = (await p.content()).includes('pat@example.test')
await masked.hover()
const tip = p.getByRole('tooltip')
await tip.waitFor()
r.tooltip = await tip.innerText()
await prov.screenshot({ path: `${OUT}/02-masked-value-tooltip.png` })
// 4b. Keyset paging (defect 4): page 2 never repeats page 1, though each read is audited
const page1 = await p.getByTestId('audit-event').evaluateAll((els) => els.map((e) => e.innerText))
r.page1_pager = await p.getByText(/\d+–\d+ of \d+/).innerText()
await Promise.all([
  p.waitForResponse((res) => res.url().includes('/api/v1/audit-events') && res.url().includes('before=')),
  p.getByRole('button', { name: 'Older' }).click(),
])
await p.getByText(/^21–/).waitFor()
const page2 = await p.getByTestId('audit-event').evaluateAll((els) => els.map((e) => e.innerText))
r.page2_pager = await p.getByText(/\d+–\d+ of \d+/).innerText()
r.page2_repeats_page1 = page2.some((t) => page1.includes(t))
await p.getByRole('button', { name: 'Newer' }).click()
await p.getByText(/^1–/).waitFor()
r.back_to_first_page = true
// 5. Filters + pagination
await p.getByLabel('Filter by outcome').selectOption('DENIED')
await p.getByRole('button', { name: 'Apply' }).click()
await p.waitForTimeout(800)
const outcomes = await p.getByTestId('audit-event').locator('div').first().allInnerTexts()
r.filtered_all_denied = (await p.getByTestId('audit-event').allInnerTexts()).every((t) => t.startsWith('DENIED'))
r.pager_text = await p.getByText(/\d+–\d+ of \d+/).innerText()
await p.screenshot({ path: `${OUT}/03-filter-denied.png`, fullPage: true })
// 5b. Exactly one page of matches (20 user.read events): Older must not lead to an empty page
await p.getByLabel('Filter by outcome').selectOption('')
await p.getByLabel('Filter by action').fill('user.read')
await p.getByRole('button', { name: 'Apply' }).click()
await p.getByText(/of 20$/).waitFor()
r.exact_page_pager = await p.getByText(/\d+–\d+ of \d+/).innerText()
r.exact_page_older_disabled = await p.getByRole('button', { name: 'Older' }).isDisabled()
await p.close()
// 6. platform_admin (clearance confidential) sees the same detail in clear
p = await as('platform_admin', 'Platform administrator')
await p.goto(`${BASE}/audit`)
const provAdmin = p.getByTestId('audit-event').filter({ hasText: 'e2e-provision-fixture' })
await provAdmin.waitFor()
r.admin_provision_row = await provAdmin.innerText()
r.admin_sees_clear_email = r.admin_provision_row.includes('pat@example.test')
await provAdmin.screenshot({ path: `${OUT}/04-clear-for-platform-admin.png` })
await p.close()
console.log(JSON.stringify(r, null, 2))
await browser.close()
