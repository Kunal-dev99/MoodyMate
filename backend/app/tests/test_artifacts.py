"""BE-8.2 (D-15): a content-addressed, write-once artifact store whose reads re-verify the
hash; its root is ARTIFACT_STORE_DIR, required outside local/test and outside the repo.
D-16 §1: the IMPORT_MAX_* settings may lower the hard ceilings, never raise them."""

import hashlib
import os
import stat
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.artifacts import ArtifactIntegrityError, FilesystemArtifactStore
from app.core.config import IMPORT_HARD_LIMITS, REPO_ROOT, Settings

OIDC: dict[str, Any] = {
    "AUTH_MODE": "oidc",
    "OIDC_ISSUER": "https://idp.example",
    "OIDC_AUDIENCE": "moodymate-api",
    "OIDC_CLIENT_ID": "moodymate-web",
    "DATA_CLASS": "synthetic",
}


def objects(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file())


# --- the store ------------------------------------------------------------------------------


def test_same_bytes_twice_give_one_object_and_the_same_ref(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path)
    first = store.put(b"ILLUSTRATIVE / SYNTHETIC,1\n")
    second = store.put(b"ILLUSTRATIVE / SYNTHETIC,1\n")
    assert first.ref == second.ref
    assert first.sha256 == hashlib.sha256(b"ILLUSTRATIVE / SYNTHETIC,1\n").hexdigest()
    assert (first.created, second.created) == (True, False)
    assert len(objects(tmp_path)) == 1
    assert store.get(first.ref) == b"ILLUSTRATIVE / SYNTHETIC,1\n"


def test_different_bytes_give_different_objects(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path)
    assert store.put(b"a").ref != store.put(b"b").ref
    assert len(objects(tmp_path)) == 2


def test_objects_are_written_read_only(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path)
    ref = store.put(b"payload").ref
    mode = stat.S_IMODE(os.stat(store.path(ref)).st_mode)
    assert mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH) == 0


def test_a_tampered_object_fails_the_read(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path)
    ref = store.put(b"original bytes").ref
    path = store.path(ref)
    path.chmod(0o644)
    path.write_bytes(b"tampered bytes")
    with pytest.raises(ArtifactIntegrityError) as caught:
        store.get(ref)
    assert caught.value.code == "artifact_integrity_error"
    assert caught.value.status_code == 500
    assert "tampered" not in caught.value.message  # no content in the error


def test_an_existing_object_is_never_rewritten(tmp_path: Path) -> None:
    """A tampered object is not silently replaced (or reused) by a later put."""
    store = FilesystemArtifactStore(tmp_path)
    ref = store.put(b"original bytes").ref
    path = store.path(ref)
    path.chmod(0o644)
    path.write_bytes(b"tampered bytes")
    with pytest.raises(ArtifactIntegrityError):
        store.put(b"original bytes")
    assert path.read_bytes() == b"tampered bytes"


def test_a_missing_object_fails_the_read(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path)
    ref = store.put(b"gone").ref
    store.path(ref).unlink()
    with pytest.raises(ArtifactIntegrityError):
        store.get(ref)


@pytest.mark.parametrize(
    "ref",
    ["../../etc/passwd", "sha256/aa/" + "a" * 63, "sha256/zz/" + "z" * 64, "/abs/path", ""],
)
def test_malformed_refs_are_refused(tmp_path: Path, ref: str) -> None:
    with pytest.raises(ArtifactIntegrityError):
        FilesystemArtifactStore(tmp_path).get(ref)


def test_no_temporary_files_are_left(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path)
    for i in range(3):
        store.put(f"object {i}".encode())
        store.put(f"object {i}".encode())
    assert all(p.parent.name != ".tmp" for p in objects(tmp_path))
    assert len(objects(tmp_path)) == 3


# --- ARTIFACT_STORE_DIR (D-13, D-15) ---------------------------------------------------------


@pytest.mark.parametrize("env", ["staging", "production"])
def test_startup_is_refused_without_the_setting(env: str) -> None:
    with pytest.raises(ValidationError, match=f"ARTIFACT_STORE_DIR is required when ENV={env}"):
        Settings(ENV=env, **OIDC)


@pytest.mark.parametrize("env", ["staging", "production"])
def test_deployments_start_with_the_setting(env: str, tmp_path: Path) -> None:
    settings = Settings(ENV=env, ARTIFACT_STORE_DIR=tmp_path, **OIDC)
    assert settings.artifact_store_dir == tmp_path


@pytest.mark.parametrize("env", ["local", "test"])
def test_local_and_test_default_outside_the_repo(env: str) -> None:
    root = Settings(ENV=env).artifact_store_dir
    assert root.is_absolute() and not root.resolve().is_relative_to(REPO_ROOT)


@pytest.mark.parametrize("env", ["local", "test", "staging"])
@pytest.mark.parametrize("inside", [".", "backend", "backend/samples/artifacts"])
def test_the_store_may_not_sit_inside_the_repo(env: str, inside: str) -> None:
    extra = OIDC if env == "staging" else {}
    with pytest.raises(ValidationError, match="ARTIFACT_STORE_DIR must be outside the repository"):
        Settings(ENV=env, ARTIFACT_STORE_DIR=REPO_ROOT / inside, **extra)


def test_the_store_must_be_an_absolute_path() -> None:
    with pytest.raises(ValidationError, match="ARTIFACT_STORE_DIR must be an absolute path"):
        Settings(ENV="test", ARTIFACT_STORE_DIR=Path("relative/artifacts"))


# --- IMPORT_MAX_* (D-16 §1) ------------------------------------------------------------------


def test_hard_ceilings_are_d16() -> None:
    assert IMPORT_HARD_LIMITS == {
        "IMPORT_MAX_BYTES": 10 * 1024 * 1024,
        "IMPORT_MAX_ROWS": 50_000,
        "IMPORT_MAX_COLUMNS": 200,
        "IMPORT_MAX_CELL_CHARS": 4_000,
        "IMPORT_MAX_XLSX_UNCOMPRESSED_BYTES": 50 * 1024 * 1024,
        "IMPORT_MAX_XLSX_RATIO": 100,
    }
    settings = Settings(ENV="test")
    assert {k: getattr(settings, k) for k in IMPORT_HARD_LIMITS} == IMPORT_HARD_LIMITS


@pytest.mark.parametrize("name", sorted(IMPORT_HARD_LIMITS))
def test_settings_may_lower_a_ceiling(name: str) -> None:
    assert getattr(Settings.model_validate({"ENV": "test", name: 1}), name) == 1


@pytest.mark.parametrize("name", sorted(IMPORT_HARD_LIMITS))
def test_settings_may_not_raise_a_ceiling(name: str) -> None:
    with pytest.raises(ValidationError, match=f"{name} may only lower the hard ceiling"):
        Settings.model_validate({"ENV": "test", name: IMPORT_HARD_LIMITS[name] + 1})


@pytest.mark.parametrize("name", sorted(IMPORT_HARD_LIMITS))
def test_limits_must_be_positive(name: str) -> None:
    with pytest.raises(ValidationError, match=f"{name} must be at least 1"):
        Settings.model_validate({"ENV": "test", name: 0})
