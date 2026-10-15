// FE-1.4 against a REAL backend 500 (backend app with a raising DB ping; no browser stub).
import { chromium } from 'playwright'
const OUT = process.argv[2]
const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' })
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
let backendCid = null
page.on('response', (r) => { if (r.url().endsWith('/api/v1/health')) backendCid = { status: r.status(), header: r.headers()['x-correlation-id'] } })
let routed = false
await page.route('**/api/v1/health', (route) => { routed = true; route.continue() }) // pass-through only, records that no stub exists
await page.goto('http://localhost:3000/dashboard')
const toast = page.locator('ol li').filter({ hasText: 'Correlation ID:' }).first()
await toast.waitFor({ timeout: 15000 })
const text = await toast.innerText()
await toast.screenshot({ path: `${OUT}/05-real-500-toast.png` })
await page.screenshot({ path: `${OUT}/06-real-500-page.png` })
const toastCid = text.match(/Correlation ID: (\S+)/)?.[1]
console.log(JSON.stringify({ request_passed_through_unmodified: routed, backend_response: backendCid, toast_text: text, toast_cid: toastCid, toast_cid_matches_backend_header: toastCid === backendCid?.header }, null, 2))
await browser.close()
