"""BE-8.2 (D-15): the content-addressed artifact store is write-once and every read
re-verifies the hash. No database needed.

Phase 33 (BE-33.6, D-59 §6), folded in (+0): the same checks loop over the filesystem store and
`S3ArtifactStore` (over an in-memory fake of the boto3-style client calls it makes); neither
store deletes."""

import hashlib
import io
import os
import stat
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from app.core.artifacts import ArtifactIntegrityError, FilesystemArtifactStore


class FakeS3Client:
    """The boto3-style calls an S3 store needs; delete is refused (D-15: never deleted)."""

    class exceptions:  # noqa: N801 - boto3's client.exceptions shape
        class NoSuchKey(Exception):
            pass

        class ClientError(Exception):
            pass

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.missing_buckets: set[str] = set()

    def put_object(self, *, Bucket: str, Key: str, Body: Any, **kw: Any) -> dict[str, Any]:  # noqa: N803
        data = Body if isinstance(Body, bytes) else Body.read()
        if kw.get("IfNoneMatch") == "*" and (Bucket, Key) in self.objects:
            raise self.exceptions.ClientError("PreconditionFailed")
        self.objects[(Bucket, Key)] = data
        return {"ETag": hashlib.md5(data, usedforsecurity=False).hexdigest()}

    def get_object(self, *, Bucket: str, Key: str, **_: Any) -> dict[str, Any]:  # noqa: N803
        if (Bucket, Key) not in self.objects:
            raise self.exceptions.NoSuchKey(Key)
        data = self.objects[(Bucket, Key)]
        return {"Body": io.BytesIO(data), "ContentLength": len(data)}

    def head_object(self, *, Bucket: str, Key: str, **_: Any) -> dict[str, Any]:  # noqa: N803
        if (Bucket, Key) not in self.objects:
            raise self.exceptions.NoSuchKey(Key)
        return {"ContentLength": len(self.objects[(Bucket, Key)])}

    def head_bucket(self, *, Bucket: str, **_: Any) -> dict[str, Any]:  # noqa: N803
        if Bucket in self.missing_buckets:
            raise self.exceptions.ClientError("NotFound")
        return {}

    def delete_object(self, **_: Any) -> None:
        raise AssertionError("an artifact store never deletes (D-15)")

    delete_objects = delete_object


def test_artifact_store_is_write_once_and_detects_tampering(tmp_path: Path) -> None:
    from app.core.artifacts import S3ArtifactStore  # BE-33.6 (here: collection never breaks)

    fs = FilesystemArtifactStore(tmp_path)
    s3 = FakeS3Client()
    bucket = "moodymate-artifacts-test"

    def s3_key(ref: str) -> tuple[str, str]:
        [key] = [k for k in s3.objects if k[1].endswith(ref)]
        return key

    def fs_tamper(ref: str) -> None:
        path = fs.path(ref)
        path.chmod(0o644)
        path.write_bytes(b"tampered bytes")

    def s3_tamper(ref: str) -> None:
        s3.objects[s3_key(ref)] = b"tampered bytes"

    def fs_stored(ref: str) -> bytes:
        return fs.path(ref).read_bytes()

    def s3_stored(ref: str) -> bytes:
        return s3.objects[s3_key(ref)]

    def fs_remove(ref: str) -> None:
        fs.path(ref).unlink()

    def s3_remove(ref: str) -> None:
        del s3.objects[s3_key(ref)]

    Hook = Callable[[str], Any]
    stores: list[tuple[str, Any, Callable[[], int], Hook, Hook, Hook]] = [
        (
            "filesystem",
            fs,
            lambda: sum(1 for p in tmp_path.rglob("*") if p.is_file()),
            fs_tamper,
            fs_stored,
            fs_remove,
        ),
        (
            "s3",
            S3ArtifactStore(client=s3, bucket=bucket),
            lambda: len(s3.objects),
            s3_tamper,
            s3_stored,
            s3_remove,
        ),
    ]
    s3.missing_buckets.add(bucket)  # an unreachable or forbidden bucket is not ready
    assert not S3ArtifactStore(client=s3, bucket=bucket).ready()
    s3.missing_buckets.clear()
    for kind, store, objects, tamper, stored, remove in stores:
        assert store.ready(), kind  # BE-34.5: the readiness check of either kind
        data = b"ILLUSTRATIVE / SYNTHETIC,1\n"
        first, second = store.put(data), store.put(data)
        assert first.ref == second.ref and (first.created, second.created) == (True, False), kind
        assert first.sha256 == hashlib.sha256(data).hexdigest(), kind
        assert store.get(first.ref) == data, kind
        assert store.put(b"other").ref != first.ref, kind
        assert objects() == 2, kind  # no temporary objects left either
        if kind == "filesystem":
            mode = stat.S_IMODE(os.stat(fs.path(first.ref)).st_mode)
            assert mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH) == 0  # written read-only
        else:
            assert {b for b, _ in s3.objects} == {bucket}

        # Tampering fails the read (without content in the error) and is never overwritten.
        tamper(first.ref)
        with pytest.raises(ArtifactIntegrityError) as caught:
            store.get(first.ref)
        assert (caught.value.code, caught.value.status_code) == ("artifact_integrity_error", 500)
        assert "tampered" not in caught.value.message, kind
        with pytest.raises(ArtifactIntegrityError):
            store.put(data)
        assert stored(first.ref) == b"tampered bytes", kind
        remove(first.ref)
        with pytest.raises(ArtifactIntegrityError):
            store.get(first.ref)  # missing
        for ref in (
            "../../etc/passwd",
            "sha256/aa/" + "a" * 63,
            "sha256/zz/" + "z" * 64,
            "/abs",
            "",
        ):
            with pytest.raises(ArtifactIntegrityError):
                store.get(ref)

    # The `s3` extra the S3 store's startup refusal names exists, at the first boto3 whose
    # PutObject takes IfNoneMatch (botocore >= 1.35.2; D-59 §6).
    pyproject = tomllib.loads((Path(__file__).resolve().parents[2] / "pyproject.toml").read_text())
    assert pyproject["project"]["optional-dependencies"]["s3"] == ["boto3>=1.35.2,<2"]
