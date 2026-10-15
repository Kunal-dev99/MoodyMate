// FE-2.3: post-sign-in redirect only to same-origin paths (real browser, dev mode).
import { chromium } from 'playwright'
const BASE = 'http://localhost:3000'
const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' })
const cases = ['/\\evil.example', '/\t/evil.example', '/%5Cevil.example', '//evil.example', '/%2F%2Fevil.example', 'https://evil.example/', '/access']
const results = []
for (const next of cases) {
  const page = await browser.newPage()
  const external = []
  page.on('request', (r) => { if (!/^http:\/\/(localhost|127\.0\.0\.1)/.test(r.url())) external.push(r.url()) })
  await page.goto(`${BASE}/login?next=${encodeURIComponent(next)}`)
  await page.getByTestId('dev-user-picker').waitFor()
  await page.getByRole('button', { name: /Auditor/ }).click()
  await page.waitForURL((u) => u.pathname === '/dashboard' || u.pathname === '/access' || !u.href.startsWith(BASE), { timeout: 15000 })
  await page.waitForTimeout(300)
  results.push({ next, landed: page.url(), stayed_on_origin: page.url().startsWith(BASE), external_requests: external })
  await page.close()
}
console.log(JSON.stringify(results, null, 2))
await browser.close()
