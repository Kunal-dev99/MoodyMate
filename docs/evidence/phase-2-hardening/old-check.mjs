// Reproduces the pre-fix behaviour: the old safeNext() and where the browser resolves it.
const oldSafeNext = (raw) => (raw && raw.startsWith('/') && !raw.startsWith('//') ? raw : '/dashboard')
for (const raw of ['/\\evil.example', '/\t/evil.example']) {
  const kept = oldSafeNext(raw)
  console.log(JSON.stringify({ raw, old_check_returned: kept, browser_resolves_to: new URL(kept, 'http://localhost:3000').href }))
}
