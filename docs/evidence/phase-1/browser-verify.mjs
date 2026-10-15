import { chromium } from 'playwright'
const OUT = process.argv[2]
const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' })
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
const results = {}
const consoleErrors = []
page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text()) })

// 1. Real backend through generated client
await page.goto('http://localhost:3000/dashboard')
await page.getByText('Operational').waitFor({ timeout: 15000 })
results.health_rendered = await page.locator('main').innerText()
await page.screenshot({ path: `${OUT}/01-dashboard-light.png`, fullPage: true })

// Verify-the-install checklist
const rgb = (sel, prop) => page.locator(sel).first().evaluate((el, p) => getComputedStyle(el)[p], prop)
results.body_bg = await rgb('body', 'backgroundColor')               // expect #FAFAF7 = rgb(250, 250, 247)
results.button_bg = await rgb('main button', 'backgroundColor')
results.button_radius = await rgb('main button', 'borderRadius')
results.logo_plate_bg = await rgb('header div.rounded-md', 'backgroundColor') // expect #15171A = rgb(21, 23, 26)
results.active_rail_bg = await rgb('aside nav a span.absolute', 'backgroundColor')
results.active_rail_width = await rgb('aside nav a span.absolute', 'width')
results.badge_bg = await page.getByText('Operational').evaluate((el) => getComputedStyle(el).backgroundColor)
results.breadcrumb = await page.locator('nav[aria-label="Breadcrumb"]').innerText()

// 2. Dark mode via ThemeToggle, persisted across reload with no flash
await page.locator('header button').last().click()
await page.getByRole('menuitem', { name: /dark/i }).click()
results.dark_class = await page.evaluate(() => document.documentElement.classList.contains('dark'))
await page.reload({ waitUntil: 'commit' })
results.dark_before_hydration = await page.evaluate(() => document.documentElement.classList.contains('dark'))
await page.getByText('Operational').waitFor()
results.logo_plate_bg_dark = await rgb('header div.rounded-md', 'backgroundColor')
await page.screenshot({ path: `${OUT}/02-dashboard-dark.png`, fullPage: true })
await page.evaluate(() => localStorage.setItem('fp_theme', 'light'))

// 3. Planned workspace + breadcrumb label
await page.goto('http://localhost:3000/schemas')
await page.getByText('This workspace is planned, not built').waitFor()
results.schemas_breadcrumb = await page.locator('nav[aria-label="Breadcrumb"]').innerText()
await page.screenshot({ path: `${OUT}/03-planned-workspace.png` })

// 4. Forced 500 with the backend's real envelope shape -> toast shows correlation ID
await page.route('**/api/v1/health', (route) => route.fulfill({
  status: 500,
  headers: { 'content-type': 'application/json', 'x-correlation-id': 'forced-500-corr-id', 'access-control-allow-origin': '*', 'access-control-expose-headers': 'X-Correlation-ID' },
  body: JSON.stringify({ code: 'internal_error', message: 'An unexpected error occurred. Quote the correlation ID when reporting it.', details: null, correlation_id: 'forced-500-corr-id', retryable: false }),
}))
await page.goto('http://localhost:3000/dashboard')
const toast = page.locator('ol li').filter({ hasText: 'Correlation ID: forced-500-corr-id' }).first()
await toast.waitFor({ timeout: 10000 })
results.toast_text = await toast.innerText()
results.error_state_text = await page.locator('main').innerText()
await page.screenshot({ path: `${OUT}/04-forced-500-toast.png` })

// 5. Backend down -> network error state
await page.unroute('**/api/v1/health')
await page.route('**/api/v1/health', (route) => route.abort('connectionrefused'))
await page.goto('http://localhost:3000/dashboard')
await page.getByText('Platform API unavailable').waitFor()
results.network_error_state = await page.locator('main').innerText()

results.console_errors = consoleErrors.filter((e) => !e.includes('Failed to load resource') && !e.includes('net::ERR'))
console.log(JSON.stringify(results, null, 2))
await browser.close()
