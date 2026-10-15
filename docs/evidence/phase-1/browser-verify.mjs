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
// Primary button exactly as components/ui/button.tsx default variant + size renders it
results.primary_button = await page.evaluate(() => {
  const b = document.createElement('button')
  b.className = 'inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium ring-offset-background transition-colors bg-primary text-primary-foreground hover:bg-primary/90 shadow-sm h-9 px-4 py-2'
  b.textContent = 'Primary'
  document.querySelector('main').appendChild(b)
  const cs = getComputedStyle(b)
  const out = { background: cs.backgroundColor, color: cs.color, borderRadius: cs.borderRadius, boxShadow: cs.boxShadow, rootRadiusToken: getComputedStyle(document.documentElement).getPropertyValue('--radius').trim() }
  b.remove()
  return out
})
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

// 5. Backend down -> network error state
await page.unroute('**/api/v1/health')
await page.route('**/api/v1/health', (route) => route.abort('connectionrefused'))
await page.goto('http://localhost:3000/dashboard')
await page.getByText('Platform API unavailable').waitFor()
results.network_error_state = await page.locator('main').innerText()

results.console_errors = consoleErrors.filter((e) => !e.includes('Failed to load resource') && !e.includes('net::ERR'))
console.log(JSON.stringify(results, null, 2))
await browser.close()
