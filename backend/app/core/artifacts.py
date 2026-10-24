"""Raw artifact storage (BE-8.2, D-15).

`ArtifactStore` is the interface; `FilesystemArtifactStore` is the local implementation
until object storage exists (D-12). An S3-compatible store is added later behind the same
interface.

* Content-addressed: the ref is derived from the SHA-256 of the bytes
  (`sha256/<2 hex>/<64 hex>`), so storing the same bytes twice gives one object and one ref.
* Write-once: an existing object is never rewritten. A new object is written to a temporary
  file and hard-linked into place, which fails rather than replaces if the name exists, and
  is left read-only.
* Every read (and every put that finds the object already there) re-verifies the hash; a
  mismatch or a missing object fails closed with `artifact_integrity_error`.
* The application never deletes an object (D-20).

Errors never carry content, only the ref.
"""

import hashlib
import os
import re
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from app.core.errors import ApiError

_REF = re.compile(r"^sha256/([0-9a-f]{2})/([0-9a-f]{64})$")
_READ_ONLY = 0o444


class ArtifactIntegrityError(ApiError):
    """A stored object is missing or no longer matches its hash (fail closed)."""

    def __init__(self, ref: str, reason: str) -> None:
        super().__init__(
            500,
            "artifact_integrity_error",
            "A stored artifact failed its integrity check.",
            {"reason": reason},
        )
        self.ref = ref


@dataclass(frozen=True)
class StoredArtifact:
    ref: str
    sha256: str
    size: int
    created: bool  # False when the same bytes were already stored


def ref_for(sha256: str) -> str:
    return f"sha256/{sha256[:2]}/{sha256}"


class ArtifactStore(ABC):
    @abstractmethod
    def put(self, data: bytes) -> StoredArtifact:
        """Store `data` once; return its content-addressed ref."""

    @abstractmethod
    def get(self, ref: str) -> bytes:
        """The object's bytes, after re-verifying its hash."""

    @abstractmethod
    def ready(self) -> bool:
        """Whether the store can take a `put` now (BE-34.5 readiness). Writes nothing, and
        never raises: any failure is `False`."""


class FilesystemArtifactStore(ArtifactStore):
    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, ref: str) -> Path:
        match = _REF.match(ref)
        if match is None or match.group(2)[:2] != match.group(1):
            raise ArtifactIntegrityError(ref, "malformed_ref")
        return self.root / ref

    def _verified(self, ref: str) -> bytes:
        path = self.path(ref)
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            raise ArtifactIntegrityError(ref, "missing") from None
        if hashlib.sha256(data).hexdigest() != ref.rsplit("/", 1)[1]:
            raise ArtifactIntegrityError(ref, "hash_mismatch")
        return data

    def get(self, ref: str) -> bytes:
        return self._verified(ref)

    def ready(self) -> bool:
        """The root is a writable directory, or (created on first upload) does not exist yet
        under a writable directory."""
        try:
            path = self.root
            while not path.exists():
                if path.parent == path:
                    return False
                path = path.parent
            return path.is_dir() and os.access(path, os.W_OK | os.X_OK)
        except OSError:
            return False

    def put(self, data: bytes) -> StoredArtifact:
        sha = hashlib.sha256(data).hexdigest()
        ref = ref_for(sha)
        target = self.path(ref)
        if target.exists():
            self._verified(ref)  # never reuse (or overwrite) a corrupted object
            return StoredArtifact(ref, sha, len(data), created=False)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, _READ_ONLY)
            try:
                os.link(tmp, target)  # atomic, and never replaces an existing name
                created = True
            except FileExistsError:
                created = False  # a concurrent put of the same bytes won
        finally:
            os.unlink(tmp)
        if not created:
            self._verified(ref)
        return StoredArtifact(ref, sha, len(data), created=created)
