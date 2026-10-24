#!/usr/bin/env bash
# FE-34.1 CI step: the frontend image build must FAIL without NEXT_PUBLIC_API_BASE_URL and
# SUCCEED with it. Prints both exit codes so the CI log carries the evidence.
#
#   frontend/scripts/check-image-build-arg.sh [image-tag] [api-base-url]
set -uo pipefail
cd "$(dirname "$0")/.."

tag="${1:-moodymate-frontend:ci}"
api="${2:-https://api.example.test}"
log="$(mktemp)"
trap 'rm -f "$log"' EXIT

echo "== FE-34.1 (1/2): image build WITHOUT NEXT_PUBLIC_API_BASE_URL (must fail)"
docker build --progress=plain -t "${tag}-noarg" . >"$log" 2>&1
code=$?
tail -n 20 "$log"
echo "exit code without the arg: ${code}"
if [ "$code" -eq 0 ]; then
  echo "FAIL: the image built without NEXT_PUBLIC_API_BASE_URL" >&2
  exit 1
fi
if ! grep -q "build arg NEXT_PUBLIC_API_BASE_URL is required" "$log"; then
  echo "FAIL: the build failed for another reason, not the missing arg" >&2
  exit 1
fi
echo "OK: expected failure (missing NEXT_PUBLIC_API_BASE_URL)"

echo "== FE-34.1 (2/2): image build WITH NEXT_PUBLIC_API_BASE_URL=${api} (must succeed)"
docker build --progress=plain --build-arg "NEXT_PUBLIC_API_BASE_URL=${api}" -t "$tag" .
code=$?
echo "exit code with the arg: ${code}"
[ "$code" -eq 0 ] || { echo "FAIL: the image did not build with the arg" >&2; exit 1; }

user="$(docker image inspect --format '{{.Config.User}}' "$tag")"
echo "image user: ${user:-<root>}"
case "$user" in
  ""|root|0|0:*|root:*) echo "FAIL: the image runs as root" >&2; exit 1 ;;
esac
echo "OK: image built with the arg and runs as non-root"
