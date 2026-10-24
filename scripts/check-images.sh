#!/usr/bin/env bash
# BE-34.2 image checks (D-62 §5, D-50 §2; Phase 34 rulings Q2 and Q10). CI runs this in the
# `images` job after building both images; it runs the same way locally.
#
#   scripts/check-images.sh <image> [<image> ...]
#
# For each image:
#   * it does not run as root: `docker run --rm --entrypoint id <image> -u` is not 0;
#   * no env file was copied into its working directory;
#   * `docker history --no-trunc` matches none of TOKEN=, DSN=, PASSWORD=, KEY=, BEGIN.
# Then .env.production.example must hold placeholders only.
#
# One history line is exempt, in exactly this form: the official python base image's
# `ENV GPG_KEY=<40 hex digits>`. It is the public OpenPGP fingerprint that verifies the
# CPython sources, not a secret. Any other KEY= line fails.
set -uo pipefail
cd "$(dirname "$0")/.."

[ "$#" -ge 1 ] || { echo "usage: $0 <image> [<image> ...]" >&2; exit 2; }

pattern='TOKEN=|DSN=|PASSWORD=|KEY=|BEGIN'
exempt='^(/bin/sh -c #\(nop\) +)?ENV GPG_KEY=[0-9A-F]{40}$'
failed=0
fail() { echo "FAIL: $*" >&2; failed=1; }

for image in "$@"; do
  echo "== ${image}"
  uid="$(docker run --rm --entrypoint id "$image" -u)" || { fail "${image}: id -u did not run"; continue; }
  echo "uid: ${uid}"
  [ -n "$uid" ] && [ "$uid" != "0" ] || fail "${image} runs as root"

  envfiles="$(docker run --rm --entrypoint sh "$image" -c 'ls -A . | grep -E "^\.env" || true')"
  [ -z "$envfiles" ] || fail "${image}: env files in the image: ${envfiles}"

  history="$(docker history --no-trunc --format '{{.CreatedBy}}' "$image")" \
    || { fail "${image}: docker history failed"; continue; }
  echo "history lines: $(printf '%s\n' "$history" | wc -l)"
  hits="$(printf '%s\n' "$history" | grep -E "$pattern" | grep -Ev "$exempt" || true)"
  exempted="$(printf '%s\n' "$history" | grep -Ec "$exempt" || true)"
  echo "exempt base-image GPG_KEY lines: ${exempted}"
  if [ -n "$hits" ]; then
    # Name the matching keyword, never the line (it may hold the secret).
    fail "${image}: docker history matches $(printf '%s\n' "$hits" | grep -oE "$pattern" | sort -u | tr '\n' ' ')"
  else
    echo "OK: no secret-like entry in docker history"
  fi
done

# .env.production.example (Q10): placeholders only.
example=.env.production.example
echo "== ${example}"
images_failed="$failed"
failed=0
if [ ! -f "$example" ]; then
  fail "${example} is missing"
else
  grep -q 'BEGIN' "$example" && fail "${example} contains BEGIN"
  # A secret-like name (commented or not) has an empty value or one <placeholder>.
  while IFS= read -r line; do
    value="${line#*=}"
    [ -z "$value" ] || [[ "$value" =~ ^\<[^\<\>]+\>$ ]] || fail "${example}: ${line%%=*} has a value"
  done < <(grep -E '^#? *[A-Z_]*(TOKEN|DSN|PASSWORD|SECRET|KEY)[A-Z_]*=' "$example")
  # A credential inside a URL is a placeholder too.
  while IFS= read -r line; do
    [[ "$line" =~ ://[^:/@]+:\<[^\<\>]+\>@ ]] || fail "${example}: ${line%%=*} carries a credential"
  done < <(grep -E '://[^/@ ]+:[^/@ ]*@' "$example")
  grep -Eq '^FORWARDED_ALLOW_IPS=\*' "$example" && fail "${example}: FORWARDED_ALLOW_IPS is *"
  grep -Eq '^METRICS_ENABLED=false$' "$example" || fail "${example}: METRICS_ENABLED is not false"
  grep -Eq '^ACCESS_LOG_ENABLED=true$' "$example" || fail "${example}: ACCESS_LOG_ENABLED is not true"
  grep -Eq '^ERROR_TRACKER_DSN=.+' "$example" && fail "${example}: ERROR_TRACKER_DSN is set"
  [ "$failed" -eq 0 ] && echo "OK: placeholders only"
fi

[ "$failed" -eq 0 ] && [ "$images_failed" -eq 0 ] || exit 1
echo "OK: every image runs as non-root and has no secret in its history"
