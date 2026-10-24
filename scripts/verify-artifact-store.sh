#!/usr/bin/env bash
# Re-hash a restored artifact store against `import_artifact` (BE-34.7, D-15;
# docs/ops/BACKUP_RESTORE.md). Read-only: it changes neither the database nor the store.
#
#   scripts/verify-artifact-store.sh <database> <store-dir>
#
# Connection settings come from the usual libpq variables (PGHOST, PGPORT, PGUSER,
# PGPASSWORD). For every `import_artifact` row, the object at <store-dir>/<storage_ref> must
# exist, its SHA-256 must equal `sha256` and its size must equal `size_bytes`. Objects with no
# row are listed. It prints refs and counts only, never content.
#
# Exit 0 when every row has its object with the right hash and size; 1 when any object is
# missing or does not match (a failed restore: never repair it silently); 2 on bad usage.
set -uo pipefail
export LC_ALL=C   # one sort order for the two lists compared below

[ "$#" -eq 2 ] || { echo "usage: $0 <database> <store-dir>" >&2; exit 2; }
db="$1"
store="$2"
[ -d "$store" ] || { echo "FAIL: ${store} is not a directory" >&2; exit 2; }

rows="$(mktemp)"
refs="$(mktemp)"
objects="$(mktemp)"
trap 'rm -f "$rows" "$refs" "$objects"' EXIT

psql -X -q -At -F '|' -d "$db" -v ON_ERROR_STOP=1 \
  -c "SELECT storage_ref, sha256, size_bytes FROM import_artifact ORDER BY storage_ref" >"$rows" \
  || { echo "FAIL: cannot read import_artifact from ${db}" >&2; exit 1; }

checked=0 ok=0 missing=0 bad_hash=0 bad_size=0
while IFS='|' read -r ref sha size; do
  [ -n "$ref" ] || continue
  checked=$((checked + 1))
  printf '%s\n' "$ref" >>"$refs"
  object="${store}/${ref}"
  if [ ! -f "$object" ]; then
    echo "missing: ${ref}"
    missing=$((missing + 1))
    continue
  fi
  actual="$(sha256sum "$object" | cut -d' ' -f1)"
  bytes="$(stat -c %s "$object")"
  if [ "$actual" != "$sha" ] || [ "${ref##*/}" != "$sha" ]; then
    echo "hash mismatch: ${ref}"
    bad_hash=$((bad_hash + 1))
  elif [ "$bytes" != "$size" ]; then
    echo "size mismatch: ${ref} (${bytes} bytes, row says ${size})"
    bad_size=$((bad_size + 1))
  else
    ok=$((ok + 1))
  fi
done <"$rows"

# Objects with no row (for example a refused upload's object). Listed, not a failure.
if [ -d "${store}/sha256" ]; then
  (cd "$store" && find sha256 -type f ! -name '.tmp-*' | sort) >"$objects"
fi
orphans="$(sort "$refs" | comm -13 - "$objects")"
orphan_count="$(printf '%s' "$orphans" | grep -c . || true)"
[ -z "$orphans" ] || printf 'object without a row: %s\n' $orphans

echo "rows checked: ${checked}; ok: ${ok}; missing: ${missing}; hash mismatch: ${bad_hash}; size mismatch: ${bad_size}; objects without a row: ${orphan_count}"
if [ $((missing + bad_hash + bad_size)) -ne 0 ]; then
  echo "FAIL: the artifact store does not match import_artifact" >&2
  exit 1
fi
echo "OK: every import_artifact row has its object, with the right SHA-256 and size"
