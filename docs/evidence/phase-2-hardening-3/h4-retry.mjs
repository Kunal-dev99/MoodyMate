// FE-2.5: a retryable 503 while loading identity keeps the session and offers Try again.
import { chromium } from 'playwright'
const OUT = process.argv[2]
const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' })
const page = await browser.newPage({ viewport: { width: 1280, height: 800 } })
const r = {}
await page.goto('http://localhost:3000/login')
await page.getByRole('button', { name: /Auditor/ }).click()
await page.waitForURL('**/dashboard')
const token = () => page.evaluate(() => JSON.parse(sessionStorage.getItem('moodymate_session') ?? '{}')?.state?.token ?? null)
r.token_before = (await token())?.slice(0, 12)
let failed = 0
await page.route('**/api/v1/me', (route) => {
  if (failed++ === 0) {
    return route.fulfill({
      status: 503,
      headers: { 'content-type': 'application/json', 'access-control-allow-origin': 'http://localhost:3000', 'access-control-expose-headers': 'X-Correlation-ID', 'x-correlation-id': 'rotation-503' },
      body: JSON.stringify({ code: 'auth_provider_unavailable', message: 'Sign-in is temporarily unavailable. Try again shortly.', details: null, correlation_id: 'rotation-503', retryable: true }),
    })
  }
  return route.continue()
})
await page.goto('http://localhost:3000/access')
await page.getByRole('button', { name: 'Try again' }).waitFor({ timeout: 10000 })
r.unavailable_screen = await page.locator('body').innerText()
r.token_after_503 = (await token())?.slice(0, 12)
r.session_kept = r.token_after_503 === r.token_before
await page.screenshot({ path: `${OUT}/01-retryable-unavailable.png` })
await page.getByRole('button', { name: 'Try again' }).click()
await page.getByTestId('my-permissions').waitFor({ timeout: 10000 })
r.after_retry_url = page.url()
r.after_retry_permissions = (await page.getByTestId('my-permissions').innerText()).split('\n').filter(Boolean)
r.me_requests = failed
await page.screenshot({ path: `${OUT}/02-after-try-again.png` })
console.log(JSON.stringify(r, null, 2))
await browser.close()
