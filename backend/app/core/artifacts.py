"""Raw artifact storage (BE-8.2, D-15).

`ArtifactStore` is the interface. `FilesystemArtifactStore` is the local implementation (the
default); `S3ArtifactStore` (Phase 33, BE-33.6, D-59 §6) keeps the same semantics in an
S3-compatible bucket, selected by ARTIFACT_STORE_KIND=s3 (`build_artifact_store`).

* Content-addressed: the ref is derived from the SHA-256 of the bytes
  (`sha256/<2 hex>/<64 hex>`), so storing the same bytes twice gives one object and one ref.
* Write-once: an existing object is never rewritten. A new object is written to a temporary
  file and hard-linked into place, which fails rather than replaces if the name exists, and
  is left read-only.
* Every read (and every put that finds the object already there) re-verifies the hash; a
  mismatch or a missing object fails closed with `artifact_integrity_error`.
* The application never deletes an object (D-20).

Errors never carry content, only the ref. The S3 store never logs or echoes its credentials:
they live only inside the client, which `build_artifact_store` creates from SecretStr settings.
"""

import hashlib
import logging
import os
import re
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from app.core.config import Settings
from app.core.errors import ApiError

# Always `fullmatch` (BE-35.7): with `match`, `$` also accepts a trailing newline.
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


def _checked(ref: str) -> str:
    """The ref, when well-formed; else `malformed_ref` (nothing outside the store is named)."""
    match = _REF.fullmatch(ref)
    if match is None or match.group(2)[:2] != match.group(1):
        raise ArtifactIntegrityError(ref, "malformed_ref")
    return ref


class FilesystemArtifactStore(ArtifactStore):
    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, ref: str) -> Path:
        return self.root / _checked(ref)

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


# --- S3-compatible store (BE-33.6, D-59 §6) ----------------------------------------------------


class S3Client(Protocol):
    """The boto3 S3 client calls the store makes (and a test fake provides). There is no
    delete: the store never deletes (D-15, D-20)."""

    @property
    def exceptions(self) -> Any: ...

    # boto3's own parameter names (hence N803).
    def put_object(self, *, Bucket: str, Key: str, Body: Any, **kwargs: Any) -> Any: ...  # noqa: N803

    def get_object(self, *, Bucket: str, Key: str, **kwargs: Any) -> Any: ...  # noqa: N803

    def head_object(self, *, Bucket: str, Key: str, **kwargs: Any) -> Any: ...  # noqa: N803

    def head_bucket(self, *, Bucket: str, **kwargs: Any) -> Any: ...  # noqa: N803


class ArtifactStoreUnavailable(ApiError):
    """The object store refused or failed a call (other than "no such object"): fail closed,
    retryable. Neither the bucket's answer nor any credential is echoed."""

    def __init__(self, ref: str) -> None:
        super().__init__(
            503,
            "artifact_store_unavailable",
            "The artifact store could not be reached. Retry later.",
            retryable=True,
        )
        self.ref = ref


_MISSING_CODES = frozenset({"404", "NoSuchKey", "NotFound"})


def _error_code(exc: BaseException) -> str | None:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = response.get("Error", {}).get("Code")
        return str(code) if code is not None else None
    return None


class S3ArtifactStore(ArtifactStore):
    """D-15 semantics in a bucket: the key is `<prefix><ref>` (content-addressed); a put
    first looks for the object (an existing one is re-verified, never rewritten) and then
    writes with `If-None-Match: *`, so a concurrent writer of the same key cannot replace it;
    every read re-verifies the hash. Nothing is deleted."""

    def __init__(self, client: S3Client, bucket: str, prefix: str = "") -> None:
        self._client = client
        self.bucket = bucket
        self.prefix = prefix

    def __repr__(self) -> str:  # never the client (it holds the credentials)
        return f"S3ArtifactStore(bucket={self.bucket!r}, prefix={self.prefix!r})"

    def key(self, ref: str) -> str:
        return f"{self.prefix}{_checked(ref)}"

    def _missing(self, exc: BaseException) -> bool:
        no_such_key = getattr(self._client.exceptions, "NoSuchKey", None)
        if isinstance(no_such_key, type) and isinstance(exc, no_such_key):
            return True
        return _error_code(exc) in _MISSING_CODES

    def _exists(self, ref: str) -> bool:
        try:
            self._client.head_object(Bucket=self.bucket, Key=self.key(ref))
        except ArtifactIntegrityError:
            raise
        except Exception as exc:
            if self._missing(exc):
                return False
            raise ArtifactStoreUnavailable(ref) from None
        return True

    def _verified(self, ref: str) -> bytes:
        key = self.key(ref)
        try:
            data = self._client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except Exception as exc:
            if self._missing(exc):
                raise ArtifactIntegrityError(ref, "missing") from None
            raise ArtifactStoreUnavailable(ref) from None
        if not isinstance(data, bytes) or hashlib.sha256(data).hexdigest() != ref[-64:]:
            raise ArtifactIntegrityError(ref, "hash_mismatch")
        return data

    def get(self, ref: str) -> bytes:
        return self._verified(ref)

    def ready(self) -> bool:
        """The bucket answers (HeadBucket; BE-34.5 readiness). Writes nothing; any failure,
        including missing access, is `False`, and nothing from the answer is kept."""
        try:
            self._client.head_bucket(Bucket=self.bucket)
        except Exception:  # noqa: BLE001
            return False
        return True

    def put(self, data: bytes) -> StoredArtifact:
        sha = hashlib.sha256(data).hexdigest()
        ref = ref_for(sha)
        if self._exists(ref):
            self._verified(ref)  # never reuse (or overwrite) a corrupted object
            return StoredArtifact(ref, sha, len(data), created=False)
        try:
            self._client.put_object(
                Bucket=self.bucket, Key=self.key(ref), Body=data, IfNoneMatch="*"
            )
        except Exception:
            # Refused: most likely a concurrent put of the same bytes won (412). The object
            # must then be there and verify; anything else fails closed.
            if not self._exists(ref):
                raise ArtifactStoreUnavailable(ref) from None
            self._verified(ref)
            return StoredArtifact(ref, sha, len(data), created=False)
        return StoredArtifact(ref, sha, len(data), created=True)


def _boto3_client(settings: Settings) -> S3Client:  # pragma: no cover - needs the s3 extra
    """A boto3 S3 client from the settings (the `s3` extra). Credentials: the access key pair
    when set, else boto3's default chain. botocore's own debug logs are kept off."""
    try:
        import boto3  # type: ignore[import-not-found, import-untyped, unused-ignore]
    except ImportError:
        raise RuntimeError(
            "ARTIFACT_STORE_KIND=s3 needs boto3: install the backend's `s3` extra"
        ) from None
    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.getLogger("boto3").setLevel(logging.WARNING)
    key_id, secret = settings.ARTIFACT_S3_ACCESS_KEY_ID, settings.ARTIFACT_S3_SECRET_ACCESS_KEY
    credentials: dict[str, str] = {}
    if key_id is not None and secret is not None:
        credentials = {
            "aws_access_key_id": key_id.get_secret_value(),
            "aws_secret_access_key": secret.get_secret_value(),
        }
    raw = boto3.client(
        "s3",
        region_name=settings.ARTIFACT_S3_REGION,
        endpoint_url=settings.ARTIFACT_S3_ENDPOINT_URL,
        **credentials,
    )
    # put() needs PutObject's IfNoneMatch (botocore >= 1.35.2, the `s3` extra's floor). An
    # older client would refuse every put, so the app refuses to start instead.
    if "IfNoneMatch" not in raw.meta.service_model.operation_model("PutObject").input_shape.members:
        raise RuntimeError(
            "ARTIFACT_STORE_KIND=s3 needs botocore >= 1.35.2 (PutObject IfNoneMatch): "
            "install the backend's `s3` extra"
        )
    client: S3Client = raw
    return client


def build_artifact_store(settings: Settings) -> ArtifactStore:
    """The store ARTIFACT_STORE_KIND selects (default filesystem, D-02)."""
    if settings.ARTIFACT_STORE_KIND == "s3":
        assert settings.ARTIFACT_S3_BUCKET is not None  # Settings validated it (D-13)
        return S3ArtifactStore(
            _boto3_client(settings), settings.ARTIFACT_S3_BUCKET, settings.ARTIFACT_S3_PREFIX
        )
    return FilesystemArtifactStore(settings.artifact_store_dir)
