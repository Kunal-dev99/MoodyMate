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


def test_migrations_are_additive_and_reversible() -> None:
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    for rev in script.walk_revisions():
        assert hasattr(rev.module, "downgrade"), rev.revision
        # D-01: upgrade() must be additive. Exceptions need a named decision.
        src = inspect.getsource(rev.module.upgrade)
        for forbidden in ("drop_table", "drop_column", "drop_constraint", "DROP "):
            assert forbidden not in src, f"{rev.revision}: {forbidden} in upgrade()"
