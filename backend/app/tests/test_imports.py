"""BE-8.4 (D-15, D-16, D-21, D-24 §4): POST /imports (multipart), GET /imports and
GET /imports/{import_id}. One upload is stored, parsed into source_record rows and RECEIVED
in one transaction; the byte limit is enforced while streaming; Idempotency-Key replays;
the synthetic attestation is required; every refused write is audited with its reason."""

import asyncio
import hashlib
import json
import uuid
from collections.abc import Iterator, MutableMapping
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from app.core.artifacts import FilesystemArtifactStore
from app.core.config import Settings
from app.main import create_app
from app.tests.conftest import dev_token, insert_tenant, sql
from app.tests.test_schema_change import token_for
from app.tests.test_sources import contract_body, source_body
from app.tests.xlsx_kit import table
from scripts.make_synthetic_samples import OUT

pytestmark = pytest.mark.db

IMPORTS = "/api/v1/imports"
MIB = 1024 * 1024
SAMPLES = {"CSV": "instrument.csv", "XLSX": "position.xlsx", "JSON": "cashflow_event.json"}
SCHEMAS = {"CSV": "instrument", "XLSX": "position", "JSON": "cashflow_event"}


@pytest.fixture
def steward(api: TestClient) -> dict[str, str]:
    return dev_token(api, "data_steward")


def seeded_source_id(db: str) -> str:
    return str(sql(db, "SELECT id FROM source_system WHERE code = 'core_banking'")[0]["id"])


def source_for(api: TestClient, db: str, steward: dict[str, str], fmt: str) -> str:
    """The seeded CSV source, or a new XLSX/JSON source (profile + contract v1)."""
    if fmt == "CSV":
        return seeded_source_id(db)
    body = source_body(
        db,
        code=f"{fmt.lower()}_feed",
        profile={
            "format": fmt,
            "owner": "Test data team",
            "default_classification": "confidential",
        },
        contract=contract_body(db, schema=SCHEMAS[fmt]),
    )
    r = api.post("/api/v1/sources", json=body, headers=steward)
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


def upload(
    api: TestClient,
    headers: dict[str, str],
    source_id: str,
    data: bytes,
    *,
    filename: str = "upload.bin",
    key: str | None = None,
    attested: bool | None = True,
    cid: str | None = None,
    **fields: str,
) -> Response:
    form: dict[str, str] = {"source_id": source_id, **fields}
    if attested is not None:
        form["synthetic_attested"] = "true" if attested else "false"
    extra = {"Idempotency-Key": key or uuid.uuid4().hex}
    if cid:
        extra["X-Correlation-ID"] = cid
    return api.post(
        IMPORTS,
        data=form,
        files={"file": (filename, data, "application/octet-stream")},
        headers={**headers, **extra},
    )


def cid() -> str:
    return f"test-{uuid.uuid4().hex[:10]}"


def events(db: str, correlation_id: str) -> list[Any]:
    return sql(db, "SELECT * FROM audit_event WHERE correlation_id = $1", correlation_id)


def counts(db: str) -> dict[str, int]:
    return {
        t: sql(db, f"SELECT count(*) AS n FROM {t}")[0]["n"]  # noqa: S608
        for t in ("import_job", "import_artifact", "import_batch", "source_record")
    }


def stored_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file()) if root.exists() else []


def assert_denied(db: str, correlation_id: str, reason: str) -> None:
    [event] = events(db, correlation_id)
    assert (event["outcome"], event["action"]) == ("DENIED", "import.create")
    assert event["resource_type"] == "import_job"
    assert json.loads(event["details"]) == {"reason": reason}


# --- one fixture per format reaches RECEIVED ------------------------------------------------------


@pytest.mark.parametrize("fmt", ["CSV", "XLSX", "JSON"])
def test_each_format_reaches_received_with_one_record_per_data_row(
    api: TestClient, db: str, steward: dict[str, str], artifact_dir: Path, fmt: str
) -> None:
    source = source_for(api, db, steward, fmt)
    data = (OUT / SAMPLES[fmt]).read_bytes()
    correlation = cid()
    r = upload(api, steward, source, data, filename=SAMPLES[fmt], cid=correlation)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "RECEIVED"
    assert body["row_count"] == 25  # the samples' data rows (marker and header excluded)
    assert body["format"] == fmt and body["source_id"] == source
    assert body["synthetic_attested"] is True and body["synthetic_marker"] is True
    assert body["data_class"] == "synthetic"
    assert body["artifact"]["sha256"] == hashlib.sha256(data).hexdigest()
    assert body["artifact"]["size_bytes"] == len(data)
    assert body["artifact"]["original_filename"] == SAMPLES[fmt]
    assert body["artifact"]["classification"] == "confidential"
    assert body["artifact"]["retention_state"] == "RETAINED"
    assert body["batches"] == [
        {
            "batch_number": 1,
            "sheet_name": "ILLUSTRATIVE SYNTHETIC" if fmt == "XLSX" else None,
            "row_count": 25,
            "column_count": 9 if fmt != "JSON" else 7,
        }
    ]
    # Pinned: the contract's target schema's ACTIVE version at creation.
    active = sql(
        db,
        "SELECT v.id FROM schema_version v JOIN schema_definition d ON d.id = v.schema_id"
        " WHERE d.code = $1 AND v.status = 'ACTIVE'",
        SCHEMAS[fmt],
    )[0]["id"]
    assert body["schema_version_id"] == str(active)
    assert (body["schema_code"], body["schema_version"]) == (SCHEMAS[fmt], 1)
    assert body["contract_version"] == 1
    assert body["correlation_id"] == correlation

    records = sql(
        db,
        "SELECT r.* FROM source_record r JOIN import_batch b ON b.id = r.batch_id"
        " WHERE b.import_job_id = $1 ORDER BY r.ordinal",
        uuid.UUID(body["id"]),
    )
    assert [r["ordinal"] for r in records] == list(range(1, 26))
    first = json.loads(records[0]["raw_values"])
    assert all(isinstance(v, str) for v in first.values())
    # The artifact is stored once, content-addressed, and reads back verified.
    ref = f"sha256/{body['artifact']['sha256'][:2]}/{body['artifact']['sha256']}"
    assert FilesystemArtifactStore(artifact_dir).get(ref) == data
    assert stored_files(artifact_dir) == [artifact_dir / ref]

    [event] = events(db, correlation)
    assert (event["action"], event["outcome"], event["resource_id"]) == (
        "import.create",
        "SUCCESS",
        body["id"],
    )
    details = json.loads(event["details"])
    assert details["row_count"] == 25 and details["status"] == "RECEIVED"
    assert details["synthetic_attested"] is True and details["data_class"] == "synthetic"

    # Reads.
    got = api.get(f"{IMPORTS}/{body['id']}", headers=dev_token(api, "auditor"))
    assert got.status_code == 200 and got.json() == body
    listed = api.get(IMPORTS, headers=steward).json()
    assert [i["id"] for i in listed["items"]] == [body["id"]] and listed["total"] == 1


def test_csv_records_keep_exact_text_and_locators(
    api: TestClient, db: str, steward: dict[str, str]
) -> None:
    data = b"# ILLUSTRATIVE / SYNTHETIC test\ninstrument_id,notional\nA,12345678901234.123456\n"
    r = upload(api, steward, seeded_source_id(db), data)
    assert r.status_code == 201, r.text
    [record] = sql(db, "SELECT * FROM source_record")
    assert json.loads(record["raw_values"]) == {
        "instrument_id": "A",
        "notional": "12345678901234.123456",
    }
    assert json.loads(record["locator"]) == {"line": 3}
    assert len(record["row_sha256"]) == 64


def test_unmarked_files_record_no_marker(api: TestClient, db: str, steward: dict[str, str]) -> None:
    r = upload(api, steward, seeded_source_id(db), b"instrument_id\nA\n")
    assert r.status_code == 201 and r.json()["synthetic_marker"] is False


# --- 413 while streaming, nothing left behind -----------------------------------------------------


def test_an_11_mib_upload_is_413_with_nothing_left_behind(
    api: TestClient, db: str, steward: dict[str, str], artifact_dir: Path
) -> None:
    correlation = cid()
    big = b"instrument_id\n" + b"x" * (11 * MIB)
    r = upload(api, steward, seeded_source_id(db), big, cid=correlation)
    assert r.status_code == 413, r.text
    assert r.json()["code"] == "payload_too_large"
    assert r.json()["details"] == {"limit": 10 * MIB}
    assert counts(db) == dict.fromkeys(counts(db), 0)
    assert stored_files(artifact_dir) == []
    assert_denied(db, correlation, "payload_too_large")


def test_just_over_the_file_limit_is_413_too(
    api: TestClient, db: str, steward: dict[str, str], artifact_dir: Path
) -> None:
    """Within the multipart framing allowance, the file itself is still bounded."""
    data = b"instrument_id\n" + b"x" * (10 * MIB - 14) + b"\n"
    assert len(data) == 10 * MIB + 1
    correlation = cid()
    r = upload(api, steward, seeded_source_id(db), data, cid=correlation)
    assert (r.status_code, r.json()["code"]) == (413, "payload_too_large")
    assert counts(db)["import_job"] == 0 and stored_files(artifact_dir) == []
    assert_denied(db, correlation, "payload_too_large")


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


def test_the_byte_limit_is_enforced_while_streaming(
    api: TestClient, db: str, steward: dict[str, str], artifact_dir: Path
) -> None:
    """A chunked 20 MiB body (no Content-Length) is refused after about 10 MiB is read."""
    chunks = list(_multipart(seeded_source_id(db), (b"x" * MIB for _ in range(20))))
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
    assert counts(db)["import_job"] == 0 and stored_files(artifact_dir) == []
    assert_denied(db, correlation, "payload_too_large")


def test_a_declared_oversize_body_is_refused_before_reading_it(
    api: TestClient, db: str, steward: dict[str, str], artifact_dir: Path
) -> None:
    chunks = list(_multipart(seeded_source_id(db), (b"x" * MIB for _ in range(11))))
    headers = {
        **steward,
        "Content-Type": "multipart/form-data; boundary=testboundary",
        "Content-Length": str(sum(len(c) for c in chunks)),
        "Idempotency-Key": uuid.uuid4().hex,
    }
    settings = Settings(ENV="test", DATABASE_URL=db, ARTIFACT_STORE_DIR=artifact_dir)
    status, body, consumed = _drive(settings, headers, chunks)
    assert (status, body["code"], consumed) == (413, "payload_too_large", 0)


def test_a_lowered_byte_limit_applies(db: str, artifact_dir: Path) -> None:
    settings = Settings(
        ENV="test", DATABASE_URL=db, ARTIFACT_STORE_DIR=artifact_dir, IMPORT_MAX_BYTES=1000
    )
    with TestClient(create_app(settings)) as api:
        steward = dev_token(api, "data_steward")
        r = upload(api, steward, seeded_source_id(db), b"instrument_id\n" + b"x" * 1000)
        assert (r.status_code, r.json()["details"]) == (413, {"limit": 1000})
        assert upload(api, steward, seeded_source_id(db), b"instrument_id\nA\n").status_code == 201


# --- Idempotency-Key (D-21) -----------------------------------------------------------------------


def test_a_repeated_key_with_the_same_bytes_returns_the_same_import(
    api: TestClient, db: str, steward: dict[str, str], artifact_dir: Path
) -> None:
    data = (OUT / "instrument.csv").read_bytes()
    first = upload(api, steward, seeded_source_id(db), data, key="upload-1")
    assert first.status_code == 201
    correlation = cid()
    again = upload(api, steward, seeded_source_id(db), data, key="upload-1", cid=correlation)
    assert again.status_code == 200, again.text
    assert again.headers["Idempotent-Replayed"] == "true"
    assert again.json()["id"] == first.json()["id"]
    assert again.json() == first.json()
    assert counts(db)["import_job"] == 1 and counts(db)["source_record"] == 25
    assert len(stored_files(artifact_dir)) == 1
    assert events(db, correlation) == []  # a replay changes nothing


def test_a_repeated_key_with_different_bytes_is_409(
    api: TestClient, db: str, steward: dict[str, str]
) -> None:
    first = upload(api, steward, seeded_source_id(db), b"instrument_id\nA\n", key="upload-2")
    assert first.status_code == 201
    correlation = cid()
    r = upload(
        api, steward, seeded_source_id(db), b"instrument_id\nB\n", key="upload-2", cid=correlation
    )
    assert (r.status_code, r.json()["code"]) == (409, "idempotency_conflict")
    assert r.json()["details"] == {"import_id": first.json()["id"]}
    assert counts(db)["import_job"] == 1
    assert_denied(db, correlation, "idempotency_conflict")
    [event] = events(db, correlation)
    assert event["resource_id"] == first.json()["id"]


@pytest.mark.parametrize(
    "change",
    [
        {"expected_row_count": "7"},
        {"control_totals": '{"notional": "1.00"}'},
        {"attested": None},
    ],
)
def test_a_repeated_key_with_other_declared_figures_is_409(
    api: TestClient, db: str, steward: dict[str, str], change: dict[str, Any]
) -> None:
    data = b"instrument_id\nA\n"
    assert upload(api, steward, seeded_source_id(db), data, key="k3").status_code == 201
    kwargs: dict[str, Any] = dict(change)
    if "attested" in kwargs:
        # Not attesting is refused before the key is looked at (D-16 §5).
        r = upload(api, steward, seeded_source_id(db), data, key="k3", attested=False)
        assert r.json()["code"] == "synthetic_attestation_required"
        return
    r = upload(api, steward, seeded_source_id(db), data, key="k3", **kwargs)
    assert (r.status_code, r.json()["code"]) == (409, "idempotency_conflict")


def test_keys_are_unique_per_actor(api: TestClient, db: str, steward: dict[str, str]) -> None:
    other = token_for(db, "dev:steward-two", ("data_steward",))
    a = upload(api, steward, seeded_source_id(db), b"instrument_id\nA\n", key="shared")
    b = upload(api, other, seeded_source_id(db), b"instrument_id\nB\n", key="shared")
    assert (a.status_code, b.status_code) == (201, 201)
    assert a.json()["id"] != b.json()["id"]


def test_concurrent_uploads_with_one_key_make_one_import(db: str, artifact_dir: Path) -> None:
    import threading
    from concurrent.futures import ThreadPoolExecutor

    settings = Settings(ENV="test", DATABASE_URL=db, ARTIFACT_STORE_DIR=artifact_dir)
    clients = [TestClient(create_app(settings)) for _ in range(4)]
    for c in clients:
        c.__enter__()
    try:
        steward = dev_token(clients[0], "data_steward")
        barrier = threading.Barrier(len(clients))
        source = seeded_source_id(db)

        def run(c: TestClient) -> Response:
            barrier.wait()
            return upload(c, steward, source, b"instrument_id\nA\n", key="raced")

        with ThreadPoolExecutor(len(clients)) as pool:
            results = list(pool.map(run, clients))
    finally:
        for c in clients:
            c.__exit__(None, None, None)
    assert sorted(r.status_code for r in results) == [200, 200, 200, 201], [r.text for r in results]
    assert len({r.json()["id"] for r in results}) == 1
    assert counts(db)["import_job"] == 1


def test_the_idempotency_key_is_required(api: TestClient, db: str, steward: dict[str, str]) -> None:
    correlation = cid()
    r = api.post(
        IMPORTS,
        data={"source_id": seeded_source_id(db), "synthetic_attested": "true"},
        files={"file": ("a.csv", b"instrument_id\nA\n", "text/csv")},
        headers={**steward, "X-Correlation-ID": correlation},
    )
    assert (r.status_code, r.json()["code"]) == (422, "validation_error")
    assert r.json()["details"][0]["loc"] == ["header", "Idempotency-Key"]
    assert events(db, correlation) == []


# --- the synthetic attestation (D-16 §5) ----------------------------------------------------------


@pytest.mark.parametrize("attested", [False, None])
def test_a_missing_attestation_under_synthetic_is_422(
    api: TestClient, db: str, steward: dict[str, str], artifact_dir: Path, attested: bool | None
) -> None:
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
    assert r.headers["X-Data-Class"] == "synthetic"
    assert counts(db)["import_job"] == 0 and stored_files(artifact_dir) == []
    assert_denied(db, correlation, "synthetic_attestation_required")


# --- authorization and tenancy --------------------------------------------------------------------


@pytest.mark.parametrize("role", ["auditor", "risk_analyst", "approver", "platform_admin"])
def test_without_import_write_is_403_with_a_denied_event(
    api: TestClient, db: str, role: str
) -> None:
    correlation = cid()
    r = upload(
        api, dev_token(api, role), seeded_source_id(db), b"instrument_id\nA\n", cid=correlation
    )
    assert r.status_code == 403
    assert r.json()["details"] == {"required_permission": "import:write"}
    [event] = events(db, correlation)
    assert (event["outcome"], event["action"]) == ("DENIED", "POST /api/v1/imports")
    assert counts(db)["import_job"] == 0


@pytest.mark.parametrize("role", ["auditor", "risk_analyst", "approver", "platform_admin"])
def test_readers_can_list_and_get(
    api: TestClient, db: str, steward: dict[str, str], role: str
) -> None:
    created = upload(api, steward, seeded_source_id(db), b"instrument_id\nA\n").json()
    headers = dev_token(api, role)
    assert api.get(f"{IMPORTS}/{created['id']}", headers=headers).status_code == 200
    assert api.get(IMPORTS, headers=headers).json()["total"] == 1


def test_cross_tenant_get_is_the_same_404_as_a_random_id(
    api: TestClient, db: str, steward: dict[str, str]
) -> None:
    created = upload(api, steward, seeded_source_id(db), b"instrument_id\nA\n").json()
    other = insert_tenant(db, f"other-{uuid.uuid4().hex[:6]}")
    outsider = token_for(db, "dev:import-outsider", ("data_steward", "auditor"), tenant_id=other)
    mine = api.get(f"{IMPORTS}/{created['id']}", headers=outsider)
    random = api.get(f"{IMPORTS}/{uuid.uuid4()}", headers=outsider)
    assert mine.status_code == random.status_code == 404
    assert {k: v for k, v in mine.json().items() if k != "correlation_id"} == {
        k: v for k, v in random.json().items() if k != "correlation_id"
    }
    assert api.get(IMPORTS, headers=outsider).json()["total"] == 0
    # Uploading to this tenant's source from the other tenant: the source is not found.
    correlation = cid()
    r = upload(api, outsider, seeded_source_id(db), b"instrument_id\nA\n", cid=correlation)
    assert (r.status_code, r.json()["code"]) == (404, "not_found")
    assert_denied(db, correlation, "not_found")
    assert counts(db)["import_job"] == 1


# --- refusals: each audited with its reason, nothing written --------------------------------------


@pytest.mark.parametrize(
    ("sample", "reason"),
    [
        ("instrument__non_utf8.csv", "encoding_not_utf8"),
        ("position.xlsx", "format_mismatch"),
        ("cashflow_event.json", "format_mismatch"),
    ],
)
def test_parse_refusals_are_audited_and_leave_nothing(
    api: TestClient, db: str, steward: dict[str, str], artifact_dir: Path, sample: str, reason: str
) -> None:
    correlation = cid()
    r = upload(api, steward, seeded_source_id(db), (OUT / sample).read_bytes(), cid=correlation)
    assert (r.status_code, r.json()["code"]) == (422, reason), r.text
    assert counts(db) == dict.fromkeys(counts(db), 0)
    assert stored_files(artifact_dir) == []
    assert_denied(db, correlation, reason)


def test_xlsm_content_named_xlsx_is_refused(
    api: TestClient, db: str, steward: dict[str, str]
) -> None:
    from app.tests.xlsx_kit import XLSM_MAIN, xlsx

    source = source_for(api, db, steward, "XLSX")
    data = xlsx([[("s", "a")], [("s", "1")]], main_type=XLSM_MAIN)
    r = upload(api, steward, source, data, filename="looks-fine.xlsx")
    assert (r.status_code, r.json()["code"]) == (422, "macro_enabled_workbook")
    assert upload(api, steward, source, table(["a"], ["1"]), filename="x.xlsm").status_code == 201


def test_unknown_control_total_columns_are_refused(
    api: TestClient, db: str, steward: dict[str, str]
) -> None:
    correlation = cid()
    r = upload(
        api,
        steward,
        seeded_source_id(db),
        b"instrument_id\nA\n",
        cid=correlation,
        control_totals='{"balance": "1.00"}',
    )
    assert (r.status_code, r.json()["code"]) == (422, "unknown_control_total_column")
    assert r.json()["details"] == {"columns": ["balance"]}
    assert_denied(db, correlation, "unknown_control_total_column")


def test_control_totals_are_kept_exactly_and_masked_by_classification(
    api: TestClient, db: str, steward: dict[str, str]
) -> None:
    r = upload(
        api,
        steward,
        seeded_source_id(db),
        b"instrument_id\nA\n",
        expected_row_count="1",
        control_totals='{"notional": "12345678901234.123456"}',
    )
    assert r.status_code == 201, r.text
    assert r.json()["control_totals"] == {"notional": "12345678901234.123456"}
    assert r.json()["expected_row_count"] == 1
    analyst = api.get(f"{IMPORTS}/{r.json()['id']}", headers=dev_token(api, "risk_analyst"))
    assert analyst.json()["control_totals"] == {
        "notional": {"masked": True, "classification": "confidential"}
    }


@pytest.mark.parametrize(
    ("form", "files", "loc"),
    [
        ({"synthetic_attested": "true"}, True, ["body", "source_id"]),
        ({"source_id": "not-a-uuid", "synthetic_attested": "true"}, True, ["body", "source_id"]),
        (
            {"source_id": "SEED", "synthetic_attested": "maybe"},
            True,
            ["body", "synthetic_attested"],
        ),
        ({"source_id": "SEED", "expected_row_count": "-1"}, True, ["body", "expected_row_count"]),
        ({"source_id": "SEED", "control_totals": "{not json"}, True, ["body", "control_totals"]),
        ({"source_id": "SEED", "control_totals": '{"notional": 1.5}'}, True, None),
        ({"source_id": "SEED", "control_totals": '{"notional": "1e5"}'}, True, None),
        ({"source_id": "SEED", "synthetic_attested": "true"}, False, ["body", "file"]),
    ],
)
def test_malformed_bodies_are_422_validation_error_and_not_audited(
    api: TestClient,
    db: str,
    steward: dict[str, str],
    form: dict[str, str],
    files: bool,
    loc: list[str] | None,
) -> None:
    form = {k: (seeded_source_id(db) if v == "SEED" else v) for k, v in form.items()}
    correlation = cid()
    r = api.post(
        IMPORTS,
        data=form,
        # Without the file, a part under another name keeps the body multipart.
        files={"file" if files else "attachment": ("a.csv", b"instrument_id\nA\n", "text/csv")},
        headers={**steward, "Idempotency-Key": uuid.uuid4().hex, "X-Correlation-ID": correlation},
    )
    assert (r.status_code, r.json()["code"]) == (422, "validation_error"), r.text
    if loc:
        assert r.json()["details"][0]["loc"][: len(loc)] == loc
    assert events(db, correlation) == []
    assert counts(db)["import_job"] == 0


@pytest.mark.parametrize(
    "body",
    [
        b"garbage without a boundary",
        b"--b\r\n\x00\x01bad header\r\n\r\nx\r\n--b--\r\n",
        b"--bXXXX",
        b"--b\r\nContent-Disposition: form-data\r\n\r\nno name\r\n--b--\r\n",
    ],
)
def test_broken_multipart_framing_is_422_not_500(
    api: TestClient, db: str, steward: dict[str, str], body: bytes
) -> None:
    correlation = cid()
    r = api.post(
        IMPORTS,
        content=body,
        headers={
            **steward,
            "Content-Type": "multipart/form-data; boundary=b",
            "Idempotency-Key": "k",
            "X-Correlation-ID": correlation,
        },
    )
    assert (r.status_code, r.json()["code"]) == (422, "validation_error"), r.text
    assert events(db, correlation) == []


def test_a_json_body_is_not_an_upload(api: TestClient, db: str, steward: dict[str, str]) -> None:
    r = api.post(
        IMPORTS,
        json={"source_id": seeded_source_id(db)},
        headers={**steward, "Idempotency-Key": "k"},
    )
    assert (r.status_code, r.json()["code"]) == (422, "validation_error")


@pytest.mark.parametrize("name", [b"a\x07b.csv", b"x" * 256 + b".csv"])
def test_unsafe_file_names_are_refused(
    api: TestClient, db: str, steward: dict[str, str], name: bytes
) -> None:
    """Built by hand: httpx would percent-encode the control character."""
    body = (
        b'--b\r\nContent-Disposition: form-data; name="source_id"\r\n\r\n'
        + seeded_source_id(db).encode()
        + b'\r\n--b\r\nContent-Disposition: form-data; name="synthetic_attested"\r\n\r\n'
        b'true\r\n--b\r\nContent-Disposition: form-data; name="file"; filename="'
        + name
        + b'"\r\n\r\ninstrument_id\nA\n\r\n--b--\r\n'
    )
    correlation = cid()
    r = api.post(
        IMPORTS,
        content=body,
        headers={
            **steward,
            "Content-Type": "multipart/form-data; boundary=b",
            "Idempotency-Key": uuid.uuid4().hex,
            "X-Correlation-ID": correlation,
        },
    )
    assert (r.status_code, r.json()["code"]) == (422, "validation_error"), r.text
    assert r.json()["details"][0]["loc"] == ["body", "file"]
    assert events(db, correlation) == [] and counts(db)["import_job"] == 0


def test_file_names_keep_only_their_base_name(
    api: TestClient, db: str, steward: dict[str, str]
) -> None:
    r = upload(
        api, steward, seeded_source_id(db), b"instrument_id\nA\n", filename="C:\\fakepath\\a.csv"
    )
    assert r.json()["artifact"]["original_filename"] == "a.csv"


def test_switch_off_refuses_uploads_with_an_audit_event(db: str, artifact_dir: Path) -> None:
    settings = Settings(
        ENV="test", DATABASE_URL=db, ARTIFACT_STORE_DIR=artifact_dir, INGESTION_ENABLED=False
    )
    with TestClient(create_app(settings)) as off:
        correlation = cid()
        r = upload(
            off, dev_token(off, "data_steward"), seeded_source_id(db), b"a\n1\n", cid=correlation
        )
        assert (r.status_code, r.json()["code"]) == (409, "ingestion_disabled")
        [event] = events(db, correlation)
        assert (event["outcome"], event["action"]) == ("DENIED", "POST /api/v1/imports")
        assert off.get(IMPORTS, headers=dev_token(off, "auditor")).status_code == 200
    assert counts(db)["import_job"] == 0 and stored_files(artifact_dir) == []


# --- GET /imports: pagination and filters ---------------------------------------------------------


def test_list_filters_by_source_and_status_newest_first(
    api: TestClient, db: str, steward: dict[str, str]
) -> None:
    seeded = seeded_source_id(db)
    other = source_for(api, db, steward, "JSON")
    a = upload(api, steward, seeded, b"instrument_id\nA\n").json()
    b = upload(api, steward, other, b'[{"cashflow_id": "1"}]').json()
    c = upload(api, steward, seeded, b"instrument_id\nC\n").json()
    page = api.get(IMPORTS, headers=steward).json()
    assert [i["id"] for i in page["items"]] == [c["id"], b["id"], a["id"]]
    by_source = api.get(IMPORTS, params={"source_id": seeded}, headers=steward).json()
    assert [i["id"] for i in by_source["items"]] == [c["id"], a["id"]] and by_source["total"] == 2
    received = api.get(IMPORTS, params={"status": "RECEIVED"}, headers=steward).json()
    assert received["total"] == 3
    failed = api.get(IMPORTS, params={"status": "FAILED"}, headers=steward).json()
    assert failed == {"items": [], "total": 0, "limit": 50, "offset": 0}
    paged = api.get(IMPORTS, params={"limit": 1, "offset": 1}, headers=steward).json()
    assert [i["id"] for i in paged["items"]] == [b["id"]] and paged["total"] == 3
    bad = api.get(IMPORTS, params={"status": "UPLOADED"}, headers=steward)
    assert bad.status_code == 422
