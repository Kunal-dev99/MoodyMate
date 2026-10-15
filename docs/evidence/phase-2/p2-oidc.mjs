// Phase 2 browser verification, AUTH_MODE=oidc against a real local OIDC provider
// (navikt/mock-oauth2-server, authorization code + PKCE) and the real backend.
import { chromium } from 'playwright'
import { execFileSync } from 'node:child_process'
const OUT = process.argv[2]
const BASE = 'http://localhost:3000'
const results = {}
const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' })
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
const idpRequests = []
page.on('request', (r) => { if (r.url().startsWith('http://localhost:8080/')) idpRequests.push(`${r.method()} ${r.url().split('?')[0]}`) })

await page.goto(`${BASE}/dashboard`)
await page.waitForURL('**/login?next=%2Fdashboard')
await page.getByRole('button', { name: 'Sign in with SSO' }).waitFor()
results.dev_picker_rendered = await page.getByTestId('dev-user-picker').count()
await page.screenshot({ path: `${OUT}/06-oidc-sign-in.png` })

await page.getByRole('button', { name: 'Sign in with SSO' }).click()
await page.waitForURL('http://localhost:8080/default/authorize**')
const authorizeUrl = new URL(page.url())
results.authorize_request = {
  response_type: authorizeUrl.searchParams.get('response_type'),
  client_id: authorizeUrl.searchParams.get('client_id'),
  code_challenge_method: authorizeUrl.searchParams.get('code_challenge_method'),
  has_code_challenge: !!authorizeUrl.searchParams.get('code_challenge'),
  redirect_uri: authorizeUrl.searchParams.get('redirect_uri'),
}
await page.screenshot({ path: `${OUT}/07-idp-login-page.png` })
await page.locator('input[name="username"]').fill('olivia')
await page.locator('input[type="submit"], button[type="submit"]').first().click()
await page.waitForURL('**/dashboard', { timeout: 20000 })
await page.getByText('Operational').waitFor()
results.landed_on = page.url()

const token = await page.evaluate(() => JSON.parse(sessionStorage.getItem('moodymate_session')).state)
const [h, p] = token.token.split('.').slice(0, 2).map((s) => JSON.parse(Buffer.from(s, 'base64url').toString()))
results.session_source = token.source
results.access_token = { alg: h.alg, iss: p.iss, aud: p.aud, sub: p.sub }

// Self-provisioned (ENV=local) with NO roles: default deny
results.nav_no_roles = await page.locator('aside nav a').allInnerTexts()
await page.goto(`${BASE}/access`)
await page.getByTestId('my-permissions').waitFor()
results.access_no_roles = await page.locator('main').innerText()
await page.screenshot({ path: `${OUT}/08-oidc-user-no-roles.png`, fullPage: true })

// Grant a role server-side; the UI reflects it after reload
const sql = `INSERT INTO user_role (user_id, role_id, created_by) SELECT u.id, r.id, 'e2e' FROM user_account u, role r WHERE u.idp_issuer='http://localhost:8080/default' AND u.idp_subject='olivia' AND r.code='data_steward'`
results.grant = execFileSync('su', ['postgres', '-c', `psql -d moodymate_ws1_p2 -tAc "${sql}"`]).toString().trim()
await page.reload()
await page.getByTestId('my-permissions').waitFor()
results.permissions_after_grant = (await page.getByTestId('my-permissions').innerText()).split('\n').filter(Boolean)
results.nav_after_grant = await page.locator('aside nav a').allInnerTexts()
await page.screenshot({ path: `${OUT}/09-oidc-user-data-steward.png`, fullPage: true })
results.idp_requests = [...new Set(idpRequests)]
console.log(JSON.stringify(results, null, 2))
await browser.close()
