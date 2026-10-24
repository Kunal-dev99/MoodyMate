"""Gate `npm audit --omit=dev --json` on the advisory allowlist (BE-28.1, D-55 §4).

Fails (exit 1) on any high or critical advisory whose GHSA id is not in the allowlist, on any
expired allowlist entry (listed or not), and on an audit report it cannot read (an npm error,
no `vulnerabilities`): it fails closed. Moderate and low findings are recorded in
docs/security/ADVISORIES.md and do not gate.

The allowlist (frontend/audit-allowlist.json) is
    {"advisories": [{"id": "GHSA-xxxx-xxxx-xxxx", "expires": "YYYY-MM-DD", "reason": "..."}]}
An entry is valid up to and including its `expires` date.

Usage (from backend/):
    npm --prefix ../frontend audit --omit=dev --json > audit.json || true
    python -m scripts.check_audit_allowlist audit.json ../frontend/audit-allowlist.json
"""

import json
import re
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

GATED = ("high", "critical")
_GHSA = re.compile(r"GHSA(?:-[23456789cfghjmpqrvwx]{4}){3}")


def findings(audit: dict[str, Any]) -> dict[str, str]:
    """Advisory id -> highest severity, for every high or critical advisory in the report."""
    if "error" in audit or not isinstance(audit.get("vulnerabilities"), dict):
        raise ValueError("not an npm audit report (npm error or no `vulnerabilities`)")
    found: dict[str, str] = {}
    for package, vulnerability in audit["vulnerabilities"].items():
        for via in vulnerability.get("via", []):
            if isinstance(via, str):
                continue  # a dependency path; the advisory itself is listed under its package
            severity = str(via.get("severity", "critical")).lower()  # unknown: assume worst
            if severity not in GATED:
                continue
            match = _GHSA.search(str(via.get("url", "")))
            advisory = match.group(0) if match else f"npm-advisory-{via.get('source', package)}"
            if found.get(advisory) != "critical":
                found[advisory] = severity
    return found


def load_allowlist(data: dict[str, Any]) -> dict[str, date]:
    """GHSA id -> expiry date. A malformed entry is an error, never silently skipped."""
    entries = data.get("advisories")
    if not isinstance(entries, list):
        raise ValueError("allowlist: `advisories` must be a list")
    allowed: dict[str, date] = {}
    for entry in entries:
        advisory = entry.get("id") if isinstance(entry, dict) else None
        if not isinstance(advisory, str) or not _GHSA.fullmatch(advisory):
            raise ValueError(f"allowlist: entry without a GHSA id: {entry!r}")
        try:
            allowed[advisory] = date.fromisoformat(str(entry.get("expires")))
        except ValueError as exc:
            raise ValueError(f"allowlist: {advisory} has no valid `expires` date") from exc
    return allowed


def problems(audit: dict[str, Any], allowlist: dict[str, Any], today: date) -> list[str]:
    """Every reason to fail; empty when the audit passes the allowlist."""
    try:
        found = findings(audit)
        allowed = load_allowlist(allowlist)
    except (ValueError, AttributeError, TypeError) as exc:
        return [str(exc)]
    out = [
        f"{advisory} ({severity}) is not in the allowlist"
        for advisory, severity in sorted(found.items())
        if advisory not in allowed
    ]
    out += [
        f"{advisory} allowlist entry expired on {expires.isoformat()}"
        for advisory, expires in sorted(allowed.items())
        if expires < today
    ]
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m scripts.check_audit_allowlist AUDIT_JSON ALLOWLIST_JSON")
        return 2
    try:
        audit, allowlist = (json.loads(Path(p).read_text()) for p in argv)
    except (OSError, ValueError) as exc:
        print(f"cannot read the audit report or the allowlist: {exc}")
        return 1
    found = problems(audit, allowlist, datetime.now(UTC).date())
    for problem in found:
        print(problem)
    if not found:
        print("npm audit: no unlisted high or critical advisory, no expired allowlist entry")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
