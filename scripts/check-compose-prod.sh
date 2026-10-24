#!/usr/bin/env bash
# BE-34.2 compose `prod` check (D-62 §5; Phase 34 rulings Q2, Q9 and Q11). CI runs this in the
# `images` job; it runs the same way locally.
#
#   scripts/check-compose-prod.sh
#
# It fills .env.production.example's placeholders with synthetic values (ENV=staging and
# DATA_CLASS=synthetic, D-14) into a throwaway env file, and brings the `prod` profile up in its
# own compose project, against a fresh `db` container of that project (never a shared
# database). It checks that:
#   * the one-shot `migrate` exits 0 and `alembic current` is the head;
#   * /api/v1/health/ready answers 200 and the frontend's / answers 200 after redirects;
#   * the API and frontend containers run as non-root;
#   * port 9100 is not published, and metrics answer only on the internal network (401 without
#     the token, 200 with it), not on the default network;
#   * a compose build of the frontend stops when NEXT_PUBLIC_API_BASE_URL is empty.
# It then removes the project (containers, networks, volumes) and the env file.
#
# Host ports: MOODYMATE_API_PORT (8000), MOODYMATE_WEB_PORT (3000), MOODYMATE_DB_PORT (5432).
# Images: built through compose (`up --build`), tagged MOODYMATE_IMAGE_TAG. PRODCHECK_BUILD=0
# runs the already-built moodymate-*:$MOODYMATE_IMAGE_TAG images instead (`up --no-build`).
set -uo pipefail
cd "$(dirname "$0")/.."

export COMPOSE_PROJECT_NAME="${PRODCHECK_PROJECT:-moodymate-prodcheck}"
export MOODYMATE_IMAGE_TAG="${MOODYMATE_IMAGE_TAG:-prodcheck}"
export MOODYMATE_API_PORT="${MOODYMATE_API_PORT:-8000}"
export MOODYMATE_WEB_PORT="${MOODYMATE_WEB_PORT:-3000}"
export MOODYMATE_DB_PORT="${MOODYMATE_DB_PORT:-5432}"
export NEXT_PUBLIC_API_BASE_URL="${NEXT_PUBLIC_API_BASE_URL:-https://api.example.test}"
envfile="$(mktemp)"
body="$(mktemp)"
buildlog="$(mktemp)"
export MOODYMATE_ENV_FILE="$envfile"

dc() { docker compose --profile prod "$@"; }
cleanup() {
  dc down -v --remove-orphans >/dev/null 2>&1
  rm -f "$envfile" "$body" "$buildlog"
}
trap cleanup EXIT
failed=0
fail() { echo "FAIL: $*" >&2; failed=1; }
die() { echo "FAIL: $*" >&2; dc logs --no-color --tail 40 >&2; exit 1; }

# The example's placeholders, filled with synthetic values (the token is random per run).
token="$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n')"
[ -n "${GITHUB_ACTIONS:-}" ] && echo "::add-mask::${token}"
cursor_key="$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n')"
[ -n "${GITHUB_ACTIONS:-}" ] && echo "::add-mask::${cursor_key}"
sed -e 's/^ENV=production$/ENV=staging/' \
    -e 's/<client-or-synthetic>/synthetic/' \
    -e 's#<db-user>:<db-password>@<db-host>:5432/<db-name>#moodymate:moodymate@db:5432/moodymate_prodcheck#' \
    -e 's/<idp-host>/idp.example.test/; s/<realm>/moodymate/' \
    -e 's/<api-audience>/moodymate-api/; s/<frontend-client-id>/moodymate-web/' \
    -e "s#https://<frontend-host>#http://localhost:${MOODYMATE_WEB_PORT}#" \
    -e 's/<proxy-ip>/127.0.0.1/' \
    -e 's/^METRICS_ENABLED=false$/METRICS_ENABLED=true/' \
    -e "s/^# METRICS_TOKEN=.*/METRICS_TOKEN=${token}/" \
    -e "s/<random-32-plus-characters-from-secret-store>/${cursor_key}/" \
    .env.production.example >"$envfile"
left="$(grep -v '^#' "$envfile" | grep -o '<[^>]*>' | sort -u | tr '\n' ' ')"
[ -z "$left" ] || die "placeholders left unfilled: ${left}"

echo "== compose config (prod profile)"
dc config --quiet || die "docker compose --profile prod config failed"
arg="$(dc config --format json | python3 -c \
  'import json, sys; print(json.load(sys.stdin)["services"]["frontend"]["build"]["args"]["NEXT_PUBLIC_API_BASE_URL"])')"
echo "frontend build arg NEXT_PUBLIC_API_BASE_URL: ${arg}"
[ "$arg" = "$NEXT_PUBLIC_API_BASE_URL" ] || fail "compose does not pass NEXT_PUBLIC_API_BASE_URL"
echo "OK: config is valid"

echo "== db (a fresh container of project ${COMPOSE_PROJECT_NAME}) and a scratch database"
docker compose up -d --wait db || die "db did not become healthy"
docker compose exec -T db createdb -U moodymate moodymate_prodcheck || die "createdb failed"

echo "== up: migrate (one-shot), api, frontend"
# `up -d` honours depends_on: the API starts only after `migrate` exits 0, and the frontend
# only once the API is healthy. (`--wait` is not used: how it treats a one-shot service that
# has exited differs between compose versions.) Then wait for the frontend's own healthcheck.
build_flag=--build
[ "${PRODCHECK_BUILD:-1}" = "0" ] && build_flag=--no-build
echo "images: moodymate-backend:${MOODYMATE_IMAGE_TAG}, moodymate-frontend:${MOODYMATE_IMAGE_TAG} (${build_flag})"
dc up -d "$build_flag" || die "the prod profile did not come up"
frontend_id="$(dc ps -q frontend)"
health=""
for _ in $(seq 1 90); do
  health="$(docker inspect -f '{{.State.Health.Status}}' "$frontend_id" 2>/dev/null)"
  [ "$health" = "healthy" ] && break
  sleep 2
done
echo "frontend health: ${health}"
[ "$health" = "healthy" ] || die "the frontend did not become healthy"

migrate_exit="$(docker inspect -f '{{.State.ExitCode}}' "$(dc ps -a -q migrate)")"
echo "migrate exit code: ${migrate_exit}"
[ "$migrate_exit" = "0" ] || fail "migrate exited ${migrate_exit}"
current="$(dc run --rm --no-deps -T migrate alembic current 2>/dev/null | tail -n 1)"
echo "alembic current: ${current}"
[[ "$current" == *"(head)"* ]] || fail "alembic current is not the head"

code="$(curl -sS -o "$body" -w '%{http_code}' "http://localhost:${MOODYMATE_API_PORT}/api/v1/health/ready")"
echo "GET /api/v1/health/ready: ${code} $(cat "$body")"
[ "$code" = "200" ] || fail "ready is not 200"
web="$(curl -fsS -L -o /dev/null -w '%{http_code}' "http://localhost:${MOODYMATE_WEB_PORT}/")"
echo "GET / (frontend, curl -L): ${web}"
[ "$web" = "200" ] || fail "frontend / is not 200"

for svc in api frontend; do
  uid="$(dc exec -T "$svc" id -u)"
  echo "${svc} uid: ${uid}"
  [ -n "$uid" ] && [ "$uid" != "0" ] || fail "${svc} runs as root"
done
echo "access log line: $(dc logs --no-color api | grep -m1 '"app.access"' | sed 's/^[^{]*//')"

echo "== metrics (D-62 §2): internal network only, never published"
published="$(dc port api 9100 2>/dev/null || true)"
echo "published 9100: ${published:-none}"
[ -z "$published" ] || fail "port 9100 is published"
api_ports="$(docker inspect -f '{{range $p, $b := .NetworkSettings.Ports}}{{$p}} {{end}}' "$(dc ps -q api)")"
echo "api container ports: ${api_ports}"
case " $api_ports " in *" 9100/tcp "*) fail "9100 is a container port mapping" ;; esac
internal="$(docker run --rm --network "${COMPOSE_PROJECT_NAME}_metrics" -e T="$token" \
  --entrypoint python "moodymate-backend:${MOODYMATE_IMAGE_TAG}" -c '
import os, urllib.error, urllib.request
def get(headers):
    req = urllib.request.Request("http://api-metrics:9100/metrics", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, ""
no_token, _ = get({})
with_token, text = get({"Authorization": "Bearer " + os.environ["T"]})
print(no_token, with_token, "moodymate_http_requests_total" in text)
')"
echo "from the internal network (no token, token, series present): ${internal}"
[ "$internal" = "401 200 True" ] || fail "metrics on the internal network: ${internal}"
outside="$(dc exec -T frontend node -e "fetch('http://api:9100/metrics').then(r => console.log('reachable ' + r.status), e => console.log('unreachable ' + ((e.cause && e.cause.code) || e.message)))")"
echo "from the default network (frontend -> api:9100): ${outside}"
[[ "$outside" == unreachable* ]] || fail "metrics reachable from the default network"

echo "== frontend build through compose with an empty NEXT_PUBLIC_API_BASE_URL (must fail)"
NEXT_PUBLIC_API_BASE_URL= dc build frontend >"$buildlog" 2>&1
code=$?
echo "exit code without the arg: ${code}"
grep -m1 -o 'build arg NEXT_PUBLIC_API_BASE_URL is required' "$buildlog" \
  || fail "the build did not stop for the missing arg"
[ "$code" -ne 0 ] || fail "the frontend built without NEXT_PUBLIC_API_BASE_URL"

[ "$failed" -eq 0 ] || { dc logs --no-color --tail 40 >&2; exit 1; }
echo "OK: compose prod is up, migrated, ready and non-root; metrics stay internal"
