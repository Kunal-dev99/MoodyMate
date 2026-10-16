// Phase 4 browser verification (FE-4.1, FE-4.2) against the real backend (dev mode).
import { chromium } from 'playwright'
const OUT = process.argv[2]
const BASE = 'http://localhost:3000'
const PLATFORM = ['DRAFT', 'IN_REVIEW', 'APPROVED', 'ACTIVE', 'DEPRECATED', 'RETIRED', 'REJECTED']
const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' })
const r = {}
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
await page.goto(`${BASE}/login`)
await page.getByRole('button', { name: /Risk analyst/ }).click()
await page.waitForURL('**/dashboard')
// FE-4.1 catalogue
await page.locator('aside nav a', { hasText: 'Source & Schemas' }).waitFor()
r.nav_has_schemas = (await page.locator('aside nav a').allInnerTexts()).some((t) => t.includes('Source & Schemas'))
await page.goto(`${BASE}/schemas`)
await page.getByTestId('schema-row').first().waitFor()
const rows = page.getByTestId('schema-row')
r.catalogue_codes = await rows.evaluateAll((els) => els.map((e) => e.dataset.code))
r.catalogue_rows = await rows.allInnerTexts()
r.status_values = await page.getByTestId('status-pill').evaluateAll((els) => els.map((e) => e.dataset.status))
r.status_values_all_platform = r.status_values.every((s) => PLATFORM.includes(s))
r.provisional_badges = await page.getByText('Provisional', { exact: true }).count()
r.planned_banner_absent = (await page.getByText(/planned/i).count()) === 0
await page.screenshot({ path: `${OUT}/01-schema-catalogue.png`, fullPage: true })
// FE-4.2 detail + compare (instrument v1 -> v2 is the BE-4.3 fixture)
await rows.filter({ hasText: 'instrument' }).first().getByRole('link').click()
await page.waitForURL('**/schemas/**')
await page.getByTestId('diff-change').first().waitFor()
r.version_rows = await page.getByTestId('version-row').count()
r.diff_overall = await page.getByTestId('diff-overall').getByTestId('compatibility-badge').getAttribute('data-compatibility')
r.diff_changes = await page.getByTestId('diff-change').evaluateAll((els) =>
  els.map((e) => ({
    field: e.dataset.field,
    badge: e.querySelector('[data-testid="compatibility-badge"]').dataset.compatibility,
    text: e.innerText,
  })),
)
r.side_by_side_rows = await page.getByTestId('diff-row').count()
await page.screenshot({ path: `${OUT}/02-schema-compare.png`, fullPage: true })
// The fixture straight from the API, to compare badge by badge
const sid = page.url().split('/schemas/')[1]
r.api_fixture = await page.evaluate(async ([id]) => {
  const stored = JSON.parse(sessionStorage.getItem('moodymate_session') ?? '{}')
  const tok = stored.state?.token ?? null
  const res = await fetch(`http://localhost:8000/api/v1/schemas/${id}/diff?from=1&to=2`, {
    headers: tok ? { Authorization: `Bearer ${tok}` } : {},
  })
  return res.ok ? (await res.json()).changes.map((c) => ({ field: c.field, badge: c.compatibility })) : res.status
}, [sid])
const key = (c) => `${c.field}:${c.badge}`
r.ui_matches_api_fixture =
  Array.isArray(r.api_fixture) &&
  JSON.stringify(r.diff_changes.map(key).sort()) === JSON.stringify(r.api_fixture.map(key).sort())
// Same versions compared: identical
await page.getByLabel('Compare to version').selectOption('1')
await page.getByTestId('diff-overall').getByText('Identical').waitFor()
r.identical_when_same_version = true
await page.close()
console.log(JSON.stringify(r, null, 2))
await browser.close()
