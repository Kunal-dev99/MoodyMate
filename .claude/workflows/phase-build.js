export const meta = {
  name: 'phase-build',
  description: 'Build one delivery-plan phase with a parallel agent swarm: contract, build, resolve questions, review + e2e, fix, report',
  whenToUse: 'The user says "build Phase N". The coordinator has created the phase worktree and DB copies; it merges, opens the PR and merges on green CI afterwards.',
  phases: [
    { title: 'Contract', detail: 'backend exports the phase API to openapi.json first' },
    { title: 'Build', detail: 'backend, test writer and frontend split by page area, in parallel' },
    { title: 'Resolve', detail: 'decision-resolver rules on every open question' },
    { title: 'Verify', detail: 'read-only review alongside the e2e run' },
    { title: 'Fix', detail: 'MAJOR/MEDIUM findings and resolver actions' },
    { title: 'Report', detail: 'phase report, plan ticks, hard-stop list' },
  ],
}

// args: {
//   phase: 19,                                  // plan phase number
//   worktree: '/home/user/MoodyMate-wt/ws3-p19', // created by the coordinator
//   devDb: 'moodymate_ws3_p19',                  // DB copy for backend/.env
//   e2ePort: 19,                                 // API 80NN, web 30NN (two-digit suffix)
//   frontendAreas: [{name, owns}] (optional; default: one frontend agent owning all FE tasks)
// }
const A = args || {}
if (!A.phase || !A.worktree) throw new Error('args.phase and args.worktree are required')
const N = A.phase
const WT = A.worktree
const P = String(A.e2ePort || N).padStart(2, '0')
const areas = A.frontendAreas && A.frontendAreas.length
  ? A.frontendAreas
  : [{ name: 'all', owns: `every FE-${N}.x task, src/config/navigation.ts and src/config/features.ts` }]

const CTX = `Feature phase ${N} of docs/DELIVERY_PLAN.md ("### Phase ${N}"). Work ONLY in worktree ${WT}. ` +
  `Do NOT commit; the coordinator commits. Follow CLAUDE.md "Phase build protocol". Active decisions in docs/DECISIONS.md bind you. ` +
  `D-26: the backend suite stays under 50 collected tests; fold new checks into existing test functions as the plan's test budget says.`
const db = (s) => `postgresql+asyncpg://moodymate:moodymate@localhost:5432/moodymate_test_p${N}${s}`
const mkdb = (s) => `create it if missing: sudo -u postgres createdb -O moodymate moodymate_test_p${N}${s}`

const QUESTIONS = {
  type: 'object',
  properties: {
    summary: { type: 'string' },
    results: { type: 'string', description: 'real result lines of the checks run' },
    questions: { type: 'array', items: { type: 'string' }, description: 'open questions / decisions needed, one per item' },
  },
  required: ['summary', 'results', 'questions'],
}
const FINDINGS = {
  type: 'object',
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          severity: { type: 'string', enum: ['MAJOR', 'MEDIUM', 'LOW'] },
          area: { type: 'string', enum: ['backend', 'frontend'] },
          where: { type: 'string' },
          scenario: { type: 'string' },
          fix: { type: 'string' },
        },
        required: ['severity', 'area', 'where', 'scenario', 'fix'],
      },
    },
  },
  required: ['findings'],
}
const RULINGS = {
  type: 'object',
  properties: {
    rulings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          question: { type: 'string' },
          ruling: { type: 'string' },
          basis: { type: 'string', enum: ['existing-decision', 'safer-default', 'hard-stop'] },
          recordAs: { type: 'string' },
          action: { type: 'string', description: 'what the owning engineer changes; empty if nothing' },
          area: { type: 'string', enum: ['backend', 'frontend', 'docs', 'none'] },
        },
        required: ['question', 'ruling', 'basis', 'recordAs', 'action', 'area'],
      },
    },
  },
  required: ['rulings'],
}

// ---- Contract ---------------------------------------------------------------
phase('Contract')
const contract = await agent(
  `${CTX}\nTask: export the API CONTRACT for every BE-${N}.x endpoint FIRST — Pydantic schemas, declared routes with permissions ` +
  `(implementations may raise NotImplementedError for now) — then run \`python -m scripts.export_openapi\` in backend/ so ` +
  `backend/openapi.json lists every Phase ${N} route. Venv: backend/.venv; backend/.env points at ${A.devDb || `moodymate_ws3_p${N}`}. ` +
  `Return as soon as the contract is exported: list the routes and permissions.`,
  { agentType: 'backend-engineer', label: `contract p${N}`, phase: 'Contract' })
log(`Contract exported for Phase ${N}`)

// ---- Build (parallel) -------------------------------------------------------
phase('Build')
const builders = [
  () => agent(
    `${CTX}\nYou are the BACKEND ENGINEER. The contract is already exported:\n${contract}\n` +
    `Implement every BE-${N}.x task: services, routes, migration (additive; downgrade refuses while data exists; tagged seed; ` +
    `pre-numbered revision if a parallel phase may also add one — say which). You own backend/app/** except the phase's test file(s) ` +
    `owned by the test writer; edit shared tests (test_ws1_verification.py matrix/isolation/AUDITED) in small hunks. ` +
    `Tests: TEST_DATABASE_URL=${db('')} REQUIRE_DB=1 (${mkdb('')}). Run TARGETED tests only; migration round trip on a scratch DB ` +
    `you create and drop (moodymate_scratch_p${N}); ruff, ruff format --check, mypy, export_openapi --check.`,
    { agentType: 'backend-engineer', label: `backend p${N}`, phase: 'Build', schema: QUESTIONS }),
  () => agent(
    `${CTX}\nYou are the TEST WRITER. Write the phase's backend test(s) per the BE-${N}.x Done-whens and the plan's test budget ` +
    `(one new test function, or assertions folded into named existing functions). You edit ONLY those test files (helpers go inside ` +
    `them or in a new *_kit.py you own). Work from backend/openapi.json and re-run as the implementation lands. Report implementation ` +
    `gaps (expected vs actual, file:line) instead of fixing implementation files. TEST_DATABASE_URL=${db('b')} REQUIRE_DB=1 (${mkdb('b')}). ` +
    `Stop after about 35 minutes. Return the collected count (\`pytest --collect-only -q | tail -1\`).`,
    { agentType: 'backend-engineer', label: `tests p${N}`, phase: 'Build', schema: QUESTIONS }),
  ...areas.map((area, i) => () => agent(
    `${CTX}\nYou are FRONTEND ENGINEER "${area.name}". You own: ${area.owns}. Other frontend agents own the rest; touch only your files. ` +
    `Run \`npm run gen:api\` now and again whenever backend/openapi.json changes; use only generated types. Build the pages, vitest ` +
    `cases and the Playwright spec(s) your Done-whens need. Do NOT start servers or run Playwright in this step. Run lint, tsc, vitest.`,
    { agentType: 'frontend-engineer', label: `frontend p${N} ${area.name}`, phase: 'Build', schema: QUESTIONS })),
]
const built = (await parallel(builders)).filter(Boolean)
const openQs = built.flatMap(b => b.questions || [])
log(`Build done: ${built.length} agents, ${openQs.length} open questions`)

// ---- Resolve ----------------------------------------------------------------
phase('Resolve')
let rulings = []
if (openQs.length) {
  const r = await agent(
    `Phase ${N} build in worktree ${WT} (read-only). Rule on each open question below per your instructions; one ruling per question.\n` +
    openQs.map((q, i) => `${i + 1}. ${q}`).join('\n'),
    { agentType: 'decision-resolver', label: `resolve p${N}`, phase: 'Resolve', schema: RULINGS })
  rulings = (r && r.rulings) || []
}
const hardStops = rulings.filter(r => r.basis === 'hard-stop')
log(`${rulings.length} rulings (${hardStops.length} hard stops)`)

// ---- Verify: review alongside e2e --------------------------------------------
phase('Verify')
const [review, e2e] = await parallel([
  () => agent(
    `Quick single-pass READ-ONLY security and correctness review of Phase ${N} (\`git -C ${WT} diff\` plus untracked files under ` +
    `backend/ and frontend/). Do not edit, run servers, or touch databases. Check against the plan's Phase ${N} tasks and active ` +
    `decisions: tenant scoping (same 404 for foreign and random ids), RBAC, audited refusals, masking (D-33), Decimal only, ` +
    `deterministic hashes, append-only guards, downgrade refusal, tagged seeds, no raw values in logs/errors. Report MAJOR/MEDIUM/LOW ` +
    `findings with file:line, a concrete scenario and a fix (at most ~8).`,
    { agentType: 'general-purpose', label: `review p${N}`, phase: 'Verify', schema: FINDINGS }),
  () => agent(
    `${CTX}\nRun the Phase ${N} Playwright spec(s) (not the full suite — CI runs that) against the built backend: fresh DB ` +
    `moodymate_test_p${N}fe (dropdb/createdb owner moodymate, then alembic upgrade head with ${WT}/backend/.venv), ports ` +
    `E2E_API_PORT=80${P} E2E_WEB_PORT=30${P}, CORS_ORIGINS='["http://localhost:30${P}"]', own artifact dir, ` +
    `PW_CHROMIUM_PATH under /opt/pw-browsers (never playwright install), build with NEXT_PUBLIC_API_BASE_URL=http://localhost:80${P}. ` +
    `Fix spec-only fallout; report app bugs as questions, do not fix backend.`,
    { agentType: 'frontend-engineer', label: `e2e p${N}`, phase: 'Verify', schema: QUESTIONS }),
])
const findings = ((review && review.findings) || [])
const blocking = findings.filter(f => f.severity !== 'LOW')
const actions = rulings.filter(r => r.action && r.basis !== 'hard-stop')

// ---- Fix ----------------------------------------------------------------------
phase('Fix')
const fixFor = (area) => [
  ...blocking.filter(f => f.area === area).map(f => `[${f.severity}] ${f.where}: ${f.scenario} → fix: ${f.fix}`),
  ...actions.filter(r => r.area === area).map(r => `[ruling] ${r.question} → ${r.action}`),
  ...(area === 'backend' && e2e ? (e2e.questions || []).map(q => `[e2e] ${q}`) : []),
]
const fixes = (await parallel(['backend', 'frontend'].map(area => () => {
  const items = fixFor(area)
  if (!items.length) return Promise.resolve(null)
  return agent(
    `${CTX}\nApply these ${area} fixes minimally (fold any new assertions into existing test functions; stay under 50 tests):\n` +
    items.map(x => `- ${x}`).join('\n') +
    `\nRe-run the targeted tests, lint and type checks${area === 'backend' ? ', export_openapi --check' : ', and the Phase ' + N + ' spec once'}. Report result lines.`,
    { agentType: area === 'backend' ? 'backend-engineer' : 'frontend-engineer', label: `fix p${N} ${area}`, phase: 'Fix', schema: QUESTIONS })
}))).filter(Boolean)
log(`Fix round: ${fixes.length} agents, ${blocking.length} blocking findings, ${actions.length} ruling actions`)

// ---- Report -------------------------------------------------------------------
phase('Report')
const report = await agent(
  `In worktree ${WT}: RETURN the full text of docs/evidence/phase-${N}/REPORT.md as your answer (the coordinator writes the file; ` +
  `same style as the earlier phase reports): what was built per task, ` +
  `checks with real result lines, review outcome, open items. Tick each BE-${N}.x/FE-${N}.x in docs/DELIVERY_PLAN.md whose Done-when is ` +
  `evidenced. Record each non-hard-stop ruling as directed (DECISIONS.md dated amendment, or a report note), marked "auto-resolved". ` +
  `List LOW findings in the report. Do not edit source code.\n\n` +
  `BUILD:\n${built.map(b => b.summary + '\n' + b.results).join('\n---\n')}\n\nRULINGS:\n${JSON.stringify(rulings, null, 1)}\n\n` +
  `REVIEW FINDINGS:\n${JSON.stringify(findings, null, 1)}\n\nE2E:\n${e2e ? e2e.summary + '\n' + e2e.results : 'not run'}\n\n` +
  `FIXES:\n${fixes.map(f => f.summary + '\n' + f.results).join('\n---\n')}`,
  { agentType: 'solution-architect', label: `report p${N}`, phase: 'Report' })

return {
  phase: N,
  worktree: WT,
  hardStops: hardStops.map(h => ({ question: h.question, ruling: h.ruling })),
  autoResolved: rulings.filter(r => r.basis !== 'hard-stop').map(r => ({ question: r.question, ruling: r.ruling, recordAs: r.recordAs })),
  lowFindings: findings.filter(f => f.severity === 'LOW'),
  remainingQuestions: fixes.flatMap(f => f.questions || []),
  report,
  next: 'Coordinator: full backend suite once, commit, merge base, push, PR, merge on green CI; send hardStops to the user in one message.',
}
