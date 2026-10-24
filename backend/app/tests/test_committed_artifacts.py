"""Committed artefacts and migration hygiene: the OpenAPI contract (D-05) and the synthetic
samples match the code that generates them; every migration is additive (D-01) and has a
downgrade (D-02). No database needed."""

import inspect
import json
from datetime import date

from alembic.config import Config
from alembic.script import ScriptDirectory
from openapi_spec_validator import validate

from scripts import check_audit_allowlist, export_openapi
from scripts.make_synthetic_samples import OUT, build


def test_committed_openapi_and_samples_are_current() -> None:
    rendered = export_openapi.render()
    spec = json.loads(rendered)
    assert spec["openapi"].startswith("3.1")
    validate(spec)
    assert "ErrorEnvelope" in spec["components"]["schemas"]
    assert export_openapi.OUT.read_text() == rendered, "run `python -m scripts.export_openapi`"
    for name, content in build().items():
        assert (
            OUT / name
        ).read_bytes() == content, f"{name}: run `python -m scripts.make_synthetic_samples`"
    # BE-28.3 (Q4 ruling): the five rate-limited routes declare 429 with Retry-After.
    for path, method in RATE_LIMITED_ROUTES:
        declared = spec["paths"][path][method]["responses"]["429"]
        assert "Retry-After" in declared["headers"], (method, path)
    _audit_allowlist_checker_gates()


RATE_LIMITED_ROUTES = (
    ("/api/v1/imports", "post"),
    ("/api/v1/imports/{import_id}/validate", "post"),
    ("/api/v1/scenarios/{scenario_id}/versions/{version}/validation", "get"),
    ("/api/v1/calculation-runs", "post"),
    ("/api/v1/scenarios/{scenario_id}/run", "post"),
)


def _npm_audit(*advisories: tuple[str, str]) -> dict[str, object]:
    """An `npm audit --json` report with one package per (GHSA id, severity)."""
    return {
        "vulnerabilities": {
            f"pkg-{n}": {
                "severity": severity,
                "via": [
                    "parent-pkg",
                    {
                        "source": n,
                        "severity": severity,
                        "url": f"https://github.com/advisories/{gh}",
                    },
                ],
            }
            for n, (gh, severity) in enumerate(advisories)
        }
    }


def _audit_allowlist_checker_gates() -> None:
    """BE-28.1 (Q7 ruling): an unlisted high/critical id fails, a listed one passes until its
    expiry, an expired entry fails, moderates do not gate, an unreadable report fails."""
    listed, unlisted, moderate = "GHSA-2464-fhr4-w9xw", "GHSA-2xp9-vwfh-vxw4", "GHSA-p293-qw3h-jr36"
    today = date(2026, 10, 24)
    allow = {"advisories": [{"id": listed, "expires": "2026-10-24", "reason": "build-time only"}]}
    check = check_audit_allowlist.problems
    assert check(_npm_audit((listed, "critical"), (moderate, "moderate")), allow, today) == []
    assert check(_npm_audit((listed, "high"), (unlisted, "critical")), allow, today) == [
        f"{unlisted} (critical) is not in the allowlist"
    ]
    assert check(_npm_audit(), allow, date(2026, 10, 25)) == [
        f"{listed} allowlist entry expired on 2026-10-24"
    ]
    assert check({"error": {"code": "ENOAUDIT"}}, allow, today) != []
    assert check(_npm_audit(), {"advisories": [{"id": listed}]}, today) != []  # no expiry
    assert check(_npm_audit(), {"advisories": [{"id": "CVE-1", "expires": "2030-01-01"}]}, today)


# D-01 exceptions approved by the user, each with its named decision and the one operation it
# allows: 0022 replaces the two schema_code CHECKs with wider ones (D-38).
D01_EXCEPTIONS = {"0022": ("D-38", "drop_constraint")}


def test_migrations_are_additive_and_reversible() -> None:
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    for rev in script.walk_revisions():
        assert hasattr(rev.module, "downgrade"), rev.revision
        # D-01: the module minus its docstring and downgrade() (so upgrade() and every helper
        # it calls) must be additive. Exceptions need a named decision.
        src = inspect.getsource(rev.module)
        for part in (rev.module.__doc__ or "", inspect.getsource(rev.module.downgrade)):
            src = src.replace(part, "")
        decision, allowed = D01_EXCEPTIONS.get(rev.revision, (None, None))
        assert getattr(rev.module, "D01_EXCEPTION", None) == decision, rev.revision
        for forbidden in ("drop_table", "drop_column", "drop_constraint", "DROP "):
            if forbidden != allowed:
                assert forbidden not in src, f"{rev.revision}: {forbidden} outside downgrade()"
