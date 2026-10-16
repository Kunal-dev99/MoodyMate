"""BE-8.2 (D-15): the content-addressed artifact store is write-once and every read
re-verifies the hash. No database needed."""

import hashlib
import os
import stat
from pathlib import Path

import pytest

from app.core.artifacts import ArtifactIntegrityError, FilesystemArtifactStore


def test_artifact_store_is_write_once_and_detects_tampering(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path)

    def objects() -> list[Path]:
        return sorted(p for p in tmp_path.rglob("*") if p.is_file())

    data = b"ILLUSTRATIVE / SYNTHETIC,1\n"
    first, second = store.put(data), store.put(data)
    assert first.ref == second.ref and (first.created, second.created) == (True, False)
    assert first.sha256 == hashlib.sha256(data).hexdigest()
    assert store.get(first.ref) == data
    assert store.put(b"other").ref != first.ref
    assert len(objects()) == 2  # no temporary files left either
    mode = stat.S_IMODE(os.stat(store.path(first.ref)).st_mode)
    assert mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH) == 0  # written read-only

    # Tampering fails the read (without content in the error) and is never overwritten.
    path = store.path(first.ref)
    path.chmod(0o644)
    path.write_bytes(b"tampered bytes")
    with pytest.raises(ArtifactIntegrityError) as caught:
        store.get(first.ref)
    assert (caught.value.code, caught.value.status_code) == ("artifact_integrity_error", 500)
    assert "tampered" not in caught.value.message
    with pytest.raises(ArtifactIntegrityError):
        store.put(data)
    assert path.read_bytes() == b"tampered bytes"
    path.unlink()
    with pytest.raises(ArtifactIntegrityError):
        store.get(first.ref)  # missing
    for ref in ("../../etc/passwd", "sha256/aa/" + "a" * 63, "sha256/zz/" + "z" * 64, "/abs", ""):
        with pytest.raises(ArtifactIntegrityError):
            store.get(ref)
