// FE-6.1 evidence: the environment banner in local dev (backend ENV=local), on the sign-in
// page and in the app shell, plus the X-Data-Class header the API returns.
import { chromium } from 'playwright'
const OUT = process.argv[2]
const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' })
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
const r = {}
const res = await page.request.get('http://localhost:8000/api/v1/health')
r.health_x_data_class = res.headers()['x-data-class']
await page.goto('http://localhost:3000/login')
await page.getByTestId('environment-banner').waitFor()
r.login_banner = await page.getByTestId('environment-banner').innerText()
await page.screenshot({ path: `${OUT}/01-banner-login.png` })
await page.getByRole('button', { name: /Data steward/ }).click()
await page.waitForURL('**/dashboard')
await page.getByTestId('environment-banner').waitFor()
r.app_banner = await page.getByTestId('environment-banner').innerText()
await page.goto('http://localhost:3000/schemas')
await page.getByTestId('schema-row').first().waitFor()
r.banner_still_visible_on_schemas = await page.getByTestId('environment-banner').isVisible()
await page.screenshot({ path: `${OUT}/02-banner-app.png` })
console.log(JSON.stringify(r, null, 2))
await browser.close()
