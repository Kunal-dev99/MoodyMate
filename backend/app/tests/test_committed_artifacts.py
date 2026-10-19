"""Committed artefacts and migration hygiene: the OpenAPI contract (D-05) and the synthetic
samples match the code that generates them; every migration is additive (D-01) and has a
downgrade (D-02). No database needed."""

import inspect
import json

from alembic.config import Config
from alembic.script import ScriptDirectory
from openapi_spec_validator import validate

from scripts import export_openapi
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
