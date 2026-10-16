"""Imports (Phase 8, D-15, D-16, D-21, D-24 §4): one upload per format reaches its end
state; the byte limit is enforced while streaming with nothing left behind; Idempotency-Key
replays and conflicts; the synthetic attestation is required; import and profile tables are
append-only."""

import asyncio
import hashlib
import json
import uuid
from collections.abc import Iterator, MutableMapping
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from fastapi.testclient import TestClient

from app.core.artifacts import FilesystemArtifactStore
from app.core.config import Settings
from app.main import create_app
from app.tests.conftest import dev_token, sql
from app.tests.helpers import (
    IMPORTS,
    MIB,
    SAMPLES,
    SCHEMAS,
    assert_denied,
    cid,
    counts,
    events,
    seeded_source_id,
    source_for,
    stored_files,
    upload,
)
from scripts.make_synthetic_samples import OUT

pytestmark = pytest.mark.db

IMMUTABLE = asyncpg.InsufficientPrivilegeError


def test_each_format_reaches_its_end_state(api: TestClient, db: str, artifact_dir: Path) -> None:
    steward = dev_token(api, "data_steward")
    for fmt, sample in SAMPLES.items():
        source = source_for(api, db, steward, fmt)
        data = (OUT / sample).read_bytes()
        correlation = cid()
        r = upload(api, steward, source, data, filename=sample, cid=correlation)
        assert r.status_code == 201, (fmt, r.text)
        body = r.json()
        # RECEIVED by the upload, then profiled; with no mapping before Phase 10 it ends
        # MAPPING_REQUIRED (D-21).
        assert body["status"] == "MAPPING_REQUIRED", fmt
        assert body["row_count"] == 25, fmt  # the samples' data rows (marker, header excluded)
        assert (body["format"], body["source_id"]) == (fmt, source)
        assert body["synthetic_attested"] is True and body["synthetic_marker"] is True
        assert body["data_class"] == "synthetic"
        assert body["artifact"]["sha256"] == hashlib.sha256(data).hexdigest()
        assert (body["schema_code"], body["schema_version"]) == (SCHEMAS[fmt], 1)
        assert body["batches"][0]["row_count"] == 25
        records = sql(
            db,
            "SELECT r.raw_values FROM source_record r JOIN import_batch b ON b.id = r.batch_id"
            " WHERE b.import_job_id = $1 ORDER BY r.ordinal",
            uuid.UUID(body["id"]),
        )
        assert len(records) == 25, fmt
        assert all(isinstance(v, str) for v in json.loads(records[0]["raw_values"]).values())
        # The artifact is stored once, content-addressed, and reads back verified.
        ref = f"sha256/{body['artifact']['sha256'][:2]}/{body['artifact']['sha256']}"
        assert FilesystemArtifactStore(artifact_dir).get(ref) == data
        actions = {(e["action"], e["outcome"]) for e in events(db, correlation)}
        assert actions == {("import.create", "SUCCESS"), ("import.profile", "SUCCESS")}, fmt
        assert api.get(f"{IMPORTS}/{body['id']}", headers=dev_token(api, "auditor")).json() == body
    assert len(stored_files(artifact_dir)) == 3
    assert api.get(IMPORTS, headers=steward).json()["total"] == 3


def _multipart(source_id: str, file_chunks: Iterator[bytes]) -> Iterator[bytes]:
    boundary = b"testboundary"
    yield (
        b"--" + boundary + b"\r\n"
        b'Content-Disposition: form-data; name="source_id"\r\n\r\n'
        + source_id.encode()
        + b"\r\n--"
        + boundary
        + b"\r\n"
        b'Content-Disposition: form-data; name="synthetic_attested"\r\n\r\ntrue\r\n--'
        + boundary
        + b"\r\n"
        b'Content-Disposition: form-data; name="file"; filename="big.csv"\r\n'
        b"Content-Type: text/csv\r\n\r\n"
    )
    yield from file_chunks
    yield b"\r\n--" + boundary + b"--\r\n"


def _drive(
    settings: Settings, headers: dict[str, str], chunks: list[bytes]
) -> tuple[int, dict[str, Any], int]:
    """Call the ASGI app directly, counting how many body chunks it pulled."""
    app = create_app(settings)

    async def run() -> tuple[int, dict[str, Any], int]:
        consumed = 0
        pending = iter(chunks)
        sent: list[MutableMapping[str, Any]] = []

        async def receive() -> dict[str, Any]:
            nonlocal consumed
            chunk = next(pending, None)
            if chunk is None:
                return {"type": "http.request", "body": b"", "more_body": False}
            consumed += 1
            return {"type": "http.request", "body": chunk, "more_body": True}

        async def send(message: MutableMapping[str, Any]) -> None:
            sent.append(message)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": IMPORTS,
            "raw_path": IMPORTS.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
            "client": ("testclient", 1),
            "server": ("testserver", 80),
        }
        try:
            await app(scope, receive, send)
        finally:
            await app.state.db.dispose()
        start = next(m for m in sent if m["type"] == "http.response.start")
        body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
        return start["status"], json.loads(body), consumed

    return asyncio.run(run())


def test_over_the_byte_limit_is_413_with_nothing_left_behind(
    api: TestClient, db: str, artifact_dir: Path
) -> None:
    steward = dev_token(api, "data_steward")
    source = seeded_source_id(db)
    correlation = cid()
    r = upload(api, steward, source, b"instrument_id\n" + b"x" * (11 * MIB), cid=correlation)
    assert (r.status_code, r.json()["code"]) == (413, "payload_too_large"), r.text
    assert r.json()["details"] == {"limit": 10 * MIB}
    assert_denied(db, correlation, "payload_too_large")

    # A chunked 20 MiB body (no Content-Length) is refused after about 10 MiB is read.
    chunks = list(_multipart(source, (b"x" * MIB for _ in range(20))))
    correlation = cid()
    headers = {
        **steward,
        "Content-Type": "multipart/form-data; boundary=testboundary",
        "Idempotency-Key": uuid.uuid4().hex,
        "X-Correlation-ID": correlation,
    }
    settings = Settings(ENV="test", DATABASE_URL=db, ARTIFACT_STORE_DIR=artifact_dir)
    status, body, consumed = _drive(settings, headers, chunks)
    assert (status, body["code"]) == (413, "payload_too_large")
    assert consumed <= 12 < len(chunks)  # the preamble plus at most 11 MiB of 20
    assert_denied(db, correlation, "payload_too_large")
    assert counts(db) == dict.fromkeys(counts(db), 0)
    assert stored_files(artifact_dir) == []


def test_idempotent_replay_and_conflict(api: TestClient, db: str, artifact_dir: Path) -> None:
    steward = dev_token(api, "data_steward")
    source = seeded_source_id(db)
    data = (OUT / "instrument.csv").read_bytes()
    first = upload(api, steward, source, data, key="upload-1")
    assert first.status_code == 201, first.text
    correlation = cid()
    again = upload(api, steward, source, data, key="upload-1", cid=correlation)
    assert again.status_code == 200, again.text
    assert again.headers["Idempotent-Replayed"] == "true"
    assert again.json() == first.json()
    assert events(db, correlation) == []  # a replay changes nothing (and is not re-profiled)
    assert counts(db)["import_job"] == 1 and counts(db)["source_record"] == 25
    assert sql(db, "SELECT count(*) AS n FROM data_profile_snapshot")[0]["n"] == 1
    assert len(stored_files(artifact_dir)) == 1

    other_figures: list[tuple[bytes, dict[str, Any]]] = [
        (data + b"# changed\n", {}),  # other bytes
        (data, {"expected_row_count": "7"}),  # same bytes, other declared figures
    ]
    for payload, fields in other_figures:
        correlation = cid()
        r = upload(api, steward, source, payload, key="upload-1", cid=correlation, **fields)
        assert (r.status_code, r.json()["code"]) == (409, "idempotency_conflict"), r.text
        assert r.json()["details"] == {"import_id": first.json()["id"]}
        assert_denied(db, correlation, "idempotency_conflict")
    assert counts(db)["import_job"] == 1


def test_the_synthetic_attestation_is_required(
    api: TestClient, db: str, artifact_dir: Path
) -> None:
    steward = dev_token(api, "data_steward")
    for attested in (False, None):
        correlation = cid()
        r = upload(
            api,
            steward,
            seeded_source_id(db),
            b"instrument_id\nA\n",
            attested=attested,
            cid=correlation,
        )
        assert (r.status_code, r.json()["code"]) == (422, "synthetic_attestation_required")
        assert_denied(db, correlation, "synthetic_attestation_required")
    assert counts(db)["import_job"] == 0 and stored_files(artifact_dir) == []


APPEND_ONLY = {
    "source_record": [
        'UPDATE source_record SET raw_values = \'{"a": "2"}\' WHERE id = $1',
        "UPDATE source_record SET updated_at = now() WHERE id = $1",
        "DELETE FROM source_record WHERE id = $1",
    ],
    "import_artifact": [
        "UPDATE import_artifact SET sha256 = repeat('b', 64) WHERE id = $1",
        "UPDATE import_artifact SET storage_ref = 'elsewhere' WHERE id = $1",
        "DELETE FROM import_artifact WHERE id = $1",
    ],
    "import_batch": [
        "UPDATE import_batch SET row_count = 2 WHERE id = $1",
        "DELETE FROM import_batch WHERE id = $1",
    ],
    "data_profile_snapshot": [
        "UPDATE data_profile_snapshot SET columns = '[]' WHERE id = $1",
        "UPDATE data_profile_snapshot SET content_sha256 = repeat('d', 64) WHERE id = $1",
        "DELETE FROM data_profile_snapshot WHERE id = $1",
    ],
    # An import_job's content is frozen and it is never deleted; only its status moves.
    "import_job": [
        "UPDATE import_job SET row_count = 2 WHERE id = $1",
        "UPDATE import_job SET data_class = 'client' WHERE id = $1",
        "UPDATE import_job SET synthetic_attested = false WHERE id = $1",
        "UPDATE import_job SET request_sha256 = repeat('b', 64) WHERE id = $1",
        "UPDATE import_job SET tenant_id = gen_random_uuid() WHERE id = $1",
        "DELETE FROM import_job WHERE id = $1",
    ],
}


def test_import_and_profile_tables_are_append_only(api: TestClient, db: str) -> None:
    r = upload(api, dev_token(api, "data_steward"), seeded_source_id(db), b"instrument_id\nA\n")
    assert r.status_code == 201, r.text
    for table, statements in APPEND_ONLY.items():
        [row] = sql(db, f"SELECT * FROM {table}")
        for statement in statements:
            with pytest.raises(IMMUTABLE, match="append-only|immutable"):
                sql(db, statement, row["id"])
            assert sql(db, f"SELECT * FROM {table}") == [row], statement
        with pytest.raises(IMMUTABLE, match="TRUNCATE"):
            sql(db, f"TRUNCATE {table} CASCADE")
    # Status moves only along the transition table: back from MAPPING_REQUIRED is refused.
    with pytest.raises(IMMUTABLE):
        sql(db, "UPDATE import_job SET status = 'RECEIVED'")
