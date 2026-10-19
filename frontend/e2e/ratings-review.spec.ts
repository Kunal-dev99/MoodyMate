import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { expect, test, type APIRequestContext } from '@playwright/test'
import type { components } from '../src/api/schema'
import { API, SAMPLES, bearer, signIn, uploadSample } from './support'

/**
 * Feature 04 Phase 20 browser check against the real stack (backend ENV=test, synthetic).
 * FE-20.2: the ratings review queue at /domain/ratings.
 *   - risk_analyst (the proposer, without rating:approve) sees their proposal IN_REVIEW in the
 *     queue with no Approve button, and a direct POST to the approval route gets a backend 403
 *     (the permission check answers before self-approval, as FE-16.2 was built).
 *   - approver approves it with the required comment; the server returns it ACTIVE, and the
 *     counterparty's Ratings panel (FE-20.1) shows it ACTIVE with the approver as decider.
 *
 * Setup (not under test) goes through the API: a counterparty (an existing one, or the synthetic
 * counterparty sample projected on a new source as ratings-panel.spec.ts does) and one proposal
 * by risk_analyst. Its effective date is unique to the run and far in the future, so it never
 * governs a reporting date another spec uses. Every payload type is the generated one
 * (src/api/schema.d.ts). Needs a backend with RATING_CHANGE_ENABLED and CREDIT_PROJECTION_ENABLED
 * on (their defaults), a frontend built with NEXT_PUBLIC_RATINGS_UI and
 * NEXT_PUBLIC_CREDIT_DOMAIN_UI on (their defaults) and a disposable database (D-03).
 */

type Schemas = components['schemas']

test.describe.configure({ mode: 'serial' })

const FILE = 'counterparty.csv'

/** The header row of the sample (the file's columns, not an API payload); `#` lines are the D-24 marker. */
const sampleColumns = (): string[] =>
  readFileSync(join(SAMPLES, FILE), 'utf8')
    .split(/\r?\n/)
    .find((line) => line.trim() !== '' && !line.startsWith('#'))!
    .split(',')
    .map((c) => c.trim())

/** A projected counterparty: the first listed one, or the synthetic sample projected on a new source. */
async function aCounterparty(request: APIRequestContext, steward: Record<string, string>): Promise<Schemas['CounterpartySummary']> {
  const existing: Schemas['CounterpartyPage'] = await (await request.get(`${API}/api/v1/counterparties?limit=1`, { headers: steward })).json()
  if (existing.items.length > 0) return existing.items[0]

  const stamp = `e2e_rating_review_${Date.now()}`
  const schemas: Schemas['SchemaList'] = await (await request.get(`${API}/api/v1/schemas`, { headers: steward })).json()
  const schema = schemas.items.find((s) => s.code === 'counterparty')
  expect(schema, 'migration 0022 seeds the counterparty schema').toBeTruthy()
  const columns = sampleColumns()
  const body: Schemas['SourceCreate'] = {
    code: stamp,
    name: 'Ratings review counterparties (FE-20.2 e2e)',
    kind: 'FILE_DROP',
    profile: { format: 'CSV', encoding: 'UTF-8', owner: 'Credit data team (e2e)', default_classification: 'confidential' },
    contract: {
      schema_id: schema!.id,
      expected_columns: columns.map((name) => ({ name, required: true })),
      business_key_columns: ['counterparty_id'],
      control_total_columns: [],
    },
  }
  const created = await request.post(`${API}/api/v1/sources`, { headers: steward, data: body })
  expect(created.status(), await created.text()).toBe(201)
  const source: Schemas['SourceDetail'] = await created.json()
  const uploaded: Schemas['ImportDetail'] = await uploadSample(request, steward, source.id, FILE)
  const versions: Schemas['SchemaVersionList'] = await (await request.get(`${API}/api/v1/schemas/${schema!.id}/versions`, { headers: steward })).json()
  const pinned = versions.items.find((v) => v.id === uploaded.schema_version_id)
  expect(pinned, 'the import pins a listed schema version').toBeTruthy()
  const types = new Map(pinned!.fields.map((f) => [f.name, f.data_type]))
  const rules: Schemas['RuleIn'][] = columns.map((column) => {
    const type = types.get(column)
    let transform: Schemas['RuleIn']['transform'] = { code: 'COPY', params: {} }
    if (type === 'DECIMAL') transform = { code: 'PARSE_DECIMAL' }
    else if (type === 'DATE') transform = { code: 'PARSE_DATE', params: { format: 'YYYY-MM-DD' } }
    return { source_column: column, target_field: column, column_policy: 'MAP', null_policy: 'ALLOW_NULL', transform }
  })
  const mapping: Schemas['MappingCreate'] = { code: stamp, name: 'Ratings review counterparties (FE-20.2 e2e)', source_contract_id: source.current_contract!.id, rules }
  const m = await request.post(`${API}/api/v1/mappings`, { headers: steward, data: mapping })
  expect(m.status(), await m.text()).toBe(201)
  const version: Schemas['MappingVersionOut'] = (await m.json()).versions[0]
  const dry = await request.post(`${API}/api/v1/imports/${uploaded.id}/mappings/validate`, { headers: steward, data: { mapping_version_id: version.id } })
  expect(dry.status(), await dry.text()).toBe(201)
  const base = `${API}/api/v1/mappings/${version.mapping_id}/versions/${version.version}`
  expect((await request.post(`${base}/submit`, { headers: steward, data: {} })).status()).toBe(200)
  const approved = await request.post(`${base}/approval`, { headers: await bearer(request, 'approver'), data: { decision: 'APPROVE' } })
  expect(approved.status(), await approved.text()).toBe(200)
  const validated = await request.post(`${API}/api/v1/imports/${uploaded.id}/validate`, { headers: steward })
  expect(validated.status(), await validated.text()).toBe(200)
  const projected = await request.post(`${API}/api/v1/imports/${uploaded.id}/projection`, { headers: steward })
  expect(projected.status(), await projected.text()).toBe(201)
  const listed: Schemas['CounterpartyPage'] = await (await request.get(`${API}/api/v1/counterparties?import_id=${uploaded.id}`, { headers: steward })).json()
  expect(listed.items.length).toBeGreaterThan(0)
  return listed.items[0]
}

/** An ISO date in 2090-2099, unique enough per run: it never governs a reporting date of another spec. */
function futureDate(): string {
  const n = Date.now()
  const year = 2090 + (n % 10)
  const month = `${1 + (Math.floor(n / 10) % 12)}`.padStart(2, '0')
  const day = `${1 + (Math.floor(n / 120) % 28)}`.padStart(2, '0')
  return `${year}-${month}-${day}`
}

let proposal: Schemas['ObligorRatingOut']

test('setup: risk_analyst proposes a rating for a counterparty (API)', async ({ request }) => {
  const counterparty = await aCounterparty(request, await bearer(request, 'data_steward'))
  const analyst = await bearer(request, 'risk_analyst')
  const vocab: Schemas['CreditVocabularyList'] = await (await request.get(`${API}/api/v1/credit-vocabularies`, { headers: analyst })).json()
  const grade = vocab.items.find((i) => i.vocabulary === 'rating_grade' && i.code === 'G04') ?? vocab.items.find((i) => i.vocabulary === 'rating_grade')
  expect(grade, 'migration 0022 seeds rating grades').toBeTruthy()
  const body: Schemas['ObligorRatingCreate'] = {
    grade: grade!.code,
    effective_from: futureDate(),
    rationale: 'FE-20.2 e2e: synthetic proposal for the review queue (illustrative, not a credit judgement on real data).',
  }
  const r = await request.post(`${API}/api/v1/counterparties/${counterparty.counterparty_id}/ratings`, { headers: analyst, data: body })
  expect(r.status(), await r.text()).toBe(201)
  proposal = await r.json()
  expect(proposal.status).toBe('IN_REVIEW')
})

test('FE-20.2: the proposer (risk_analyst) sees the proposal IN_REVIEW with no Approve button; a direct POST gets a backend 403', async ({ page, request }) => {
  await signIn(page, 'Risk analyst')
  const listed = page.waitForResponse((r) => r.request().method() === 'GET' && r.url().startsWith(`${API}/api/v1/obligor-ratings?`))
  await page.goto('/domain/ratings')
  const queueResponse = await listed
  expect(queueResponse.status()).toBe(200)
  expect(new URL(queueResponse.url()).searchParams.get('status')).toBe('IN_REVIEW')
  const queue: Schemas['ObligorRatingPage'] = await queueResponse.json()
  expect(queue.items.map((i) => i.rating_id)).toContain(proposal.rating_id)

  const row = page.locator(`[data-testid="review-row"][data-id="${proposal.rating_id}"]`)
  await expect(row).toHaveAttribute('data-status', 'IN_REVIEW')
  await expect(row.getByTestId('review-status')).toHaveText('In review')
  await expect(row.getByTestId('review-effective-from')).toHaveText(proposal.effective_from)
  await expect(row.getByTestId('review-proposer')).toContainText('(you)')
  await expect(row).toHaveAttribute('data-gate', 'self_approval')
  await expect(row.getByTestId('review-self-approval')).toBeVisible()
  // risk_analyst holds rating:propose, not rating:approve: no Approve or Reject anywhere in the queue.
  await expect(page.getByTestId('rating-approve')).toHaveCount(0)
  await expect(page.getByTestId('rating-reject')).toHaveCount(0)

  // The hidden button is cosmetic (D-04): the server refuses the decision itself. The role lacks
  // rating:approve, so the permission check answers (403 forbidden) before self-approval (409).
  const analyst = await bearer(request, 'risk_analyst')
  const decision: Schemas['ObligorRatingDecision'] = { decision: 'APPROVE', comment: 'FE-20.2 e2e: a direct POST by the proposer.' }
  const refused = await request.post(`${API}/api/v1/obligor-ratings/${proposal.rating_id}/approval`, { headers: analyst, data: decision })
  expect(refused.status(), await refused.text()).toBe(403)
  const envelope: Schemas['ErrorEnvelope'] = await refused.json()
  expect(envelope.code).toBe('forbidden')
  const still: Schemas['ObligorRatingOut'] = await (await request.get(`${API}/api/v1/obligor-ratings/${proposal.rating_id}`, { headers: analyst })).json()
  expect(still.status).toBe('IN_REVIEW')
})

test('FE-20.2: the approver approves with a required comment, and the counterparty shows the rating ACTIVE', async ({ page }) => {
  await signIn(page, 'Approver')
  await page.goto('/domain/ratings')
  const row = page.locator(`[data-testid="review-row"][data-id="${proposal.rating_id}"]`)
  await expect(row).toHaveAttribute('data-gate', 'allowed')
  await row.getByTestId('rating-approve').click()

  const dialog = page.getByTestId('rating-decision-dialog')
  await expect(dialog).toHaveAttribute('data-decision', 'APPROVE')
  // The comment is required: nothing can be sent while it is empty.
  await expect(dialog.getByTestId('rating-decision-confirm')).toBeDisabled()
  await dialog.getByTestId('rating-decision-comment').fill('   ')
  await dialog.getByTestId('rating-decision-comment').blur()
  await expect(dialog.getByTestId('rating-decision-comment-problem')).toHaveText('A comment is required.')
  await expect(dialog.getByTestId('rating-decision-confirm')).toBeDisabled()

  const comment = 'FE-20.2 e2e: rationale checked against the synthetic financials.'
  await dialog.getByTestId('rating-decision-comment').fill(comment)
  await expect(dialog.getByTestId('rating-decision-comment-count')).toHaveText(`${comment.length}/1000`)
  const posted = page.waitForResponse((r) => r.request().method() === 'POST' && r.url() === `${API}/api/v1/obligor-ratings/${proposal.rating_id}/approval`)
  await dialog.getByTestId('rating-decision-confirm').click()
  const response = await posted
  expect(response.status(), await response.text()).toBe(200)
  const decided: Schemas['ObligorRatingOut'] = await response.json()
  expect(decided.status).toBe('ACTIVE')
  expect(decided.decided_by).toBeTruthy()
  await expect(dialog).toBeHidden()

  // The queue shows what the server returned, and the version leaves the IN_REVIEW list.
  await expect(page.getByTestId('review-outcome')).toHaveAttribute('data-status', 'ACTIVE')
  await expect(page.getByTestId('review-outcome-status')).toHaveText('Active')
  await expect(row).toHaveCount(0)
  await page.getByTestId('review-status-filter').selectOption('ACTIVE')
  await expect(page.locator(`[data-testid="review-row"][data-id="${proposal.rating_id}"]`)).toHaveAttribute('data-status', 'ACTIVE')
  if (process.env.DOMAIN_SCREENSHOT) await page.screenshot({ path: `${process.env.DOMAIN_SCREENSHOT}/ratings-review.png`, fullPage: true })

  // The counterparty's Ratings panel (FE-20.1) reads it ACTIVE from the server, decided by the approver.
  const detailUrl = `${API}/api/v1/counterparties/${proposal.counterparty_id}`
  const read = page.waitForResponse((r) => r.request().method() === 'GET' && r.url() === detailUrl)
  await page.getByTestId('review-outcome-counterparty').click()
  const detail: Schemas['CounterpartyDetail'] = await (await read).json()
  expect(detail.ratings.find((r) => r.rating_id === proposal.rating_id)?.status).toBe('ACTIVE')
  const panelRow = page.getByTestId('ratings-panel').locator(`[data-testid="rating-row"][data-id="${proposal.rating_id}"]`)
  await expect(panelRow).toHaveAttribute('data-status', 'ACTIVE')
  await expect(panelRow.getByTestId('rating-status')).toHaveText('Active')
  await expect(panelRow.getByTestId('rating-decider')).toContainText(decided.decided_by!)
})
