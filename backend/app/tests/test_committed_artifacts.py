"""Committed artefacts and migration hygiene: the OpenAPI contract (D-05) and the synthetic
samples match the code that generates them; every migration is additive (D-01) and has a
downgrade (D-02). No database needed."""

import inspect
import json
from datetime import date
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from openapi_spec_validator import validate

from app.tests.observability_kit import check_triggers_doc
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
    # BE-33.5 (Q15 ruling): the contract only grows against the phase's merge-base copy
    # (backend/openapi.base.json); the checker itself refuses a removal and a new required
    # request field.
    base = json.loads(BASE_OPENAPI.read_text())
    assert additive_problems(base, spec) == []
    assert additive_problems(spec, base) != []  # the reverse diff removes things
    narrowed = json.loads(json.dumps(spec))
    narrowed["components"]["schemas"]["SnapshotCreate"]["required"].append("note")
    assert additive_problems(spec, narrowed) == [
        "/components/schemas/SnapshotCreate/required: new required request field note"
    ]
    # BE-34.7: docs/ops/TRIGGERS.md lists every trigger a migration creates, by migration.
    check_triggers_doc()


BASE_OPENAPI = export_openapi.OUT.with_name("openapi.base.json")
# Prose may change; every other value is part of the contract.
TEXT_KEYS = frozenset({"description", "summary", "title", "example", "examples"})


def _request_schemas(spec: dict[str, Any]) -> set[str]:
    """The component schemas a request body reaches ($ref closure)."""
    schemas, found = spec.get("components", {}).get("schemas", {}), set()
    todo = [
        op["requestBody"]
        for ops in spec["paths"].values()
        for op in ops.values()
        if isinstance(op, dict) and "requestBody" in op
    ]
    while todo:
        node = todo.pop()
        if isinstance(node, dict):
            ref = node.get("$ref", "")
            name = ref.rsplit("/", 1)[-1] if ref.startswith("#/components/schemas/") else None
            if name and name not in found:
                found.add(name)
                todo.append(schemas.get(name, {}))
            todo.extend(v for k, v in node.items() if k != "$ref")
        elif isinstance(node, list):
            todo.extend(node)
    return found


def additive_problems(base: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """What `current` removes or narrows relative to `base` (empty: additive only). Nothing
    may disappear (paths, operations, parameters, responses, schemas, properties); an enum
    may only widen; a parameter or request body may become optional, never required; a new
    parameter is optional; a request schema gains no required field and a response schema
    keeps every required one; any other value is unchanged."""
    requests = _request_schemas(current)
    problems: list[str] = []

    def walk(b: Any, c: Any, at: str, schema: str | None) -> None:
        if isinstance(b, dict) and isinstance(c, dict):
            for key, value in b.items():
                here = f"{at}/{key}"
                if key in TEXT_KEYS:
                    continue
                if key not in c:
                    problems.append(f"{here}: removed")
                elif key == "enum" and isinstance(value, list):
                    if not set(map(str, value)) <= set(map(str, c[key])):
                        problems.append(f"{here}: narrowed")
                elif key == "required" and isinstance(value, bool):
                    if c[key] and not value:
                        problems.append(f"{here}: became required")
                elif key == "required" and isinstance(value, list):
                    if schema in requests:
                        problems.extend(
                            f"{here}: new required request field {n}"
                            for n in c[key]
                            if n not in value
                        )
                    else:
                        problems.extend(
                            f"{here}: no longer required {n}" for n in value if n not in c[key]
                        )
                elif key == "parameters" and isinstance(value, list):
                    have = {(p["in"], p["name"]): p for p in c[key]}
                    for param in value:
                        found = have.get((param["in"], param["name"]))
                        if found is None:
                            problems.append(f"{here}/{param['name']}: removed")
                        else:
                            walk(param, found, f"{here}/{param['name']}", schema)
                    known = {(p["in"], p["name"]) for p in value}
                    problems.extend(
                        f"{here}/{p['name']}: new required parameter"
                        for p in c[key]
                        if (p["in"], p["name"]) not in known and p.get("required")
                    )
                else:
                    nested = key if at == "/components/schemas" else schema
                    walk(value, c[key], here, nested)
        elif isinstance(b, list) and isinstance(c, list) and len(b) == len(c):
            for n, (x, y) in enumerate(zip(b, c, strict=True)):
                walk(x, y, f"{at}/{n}", schema)
        elif b != c:
            problems.append(f"{at}: changed")

    walk(base, current, "", None)
    return problems


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
