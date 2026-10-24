"""Security checks (BE-35.7) not already held by WS1, test_adapters or test_auth:

* malformed archives are refused with an upload refusal, never a crash or a partial parse
  (the zip-bomb ratio, uncompressed size, entity expansion and macro cases stay in
  test_adapters.py::test_bad_files_and_limits_are_refused_with_their_own_code);
* formula-like cells are kept as the exact text, never evaluated or rewritten;
* CR/LF in `Idempotency-Key` is a 422 with nothing written, and an unsafe `X-Correlation-ID`
  is never echoed into a response header (a fresh id replaces it); and no `$`-anchored
  validator takes a trailing newline (`fullmatch`): rule enum values and bucket codes, the
  S3 bucket and prefix settings, artifact refs.
"""

import io
import json
import uuid
import zipfile
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.artifacts import ArtifactIntegrityError, FilesystemArtifactStore
from app.core.config import Settings
from app.ingestion.adapters import AdapterError, AdapterOptions, parse_upload
from app.ingestion.adapters.base import HARD_LIMITS
from app.ingestion.constants import SourceFormat, UploadRefusal
from app.rules.constants import ParameterProblem
from app.rules.service import check_parameters, check_schema
from app.tests.conftest import dev_token, sql
from app.tests.helpers import IMPORTS, events, seeded_source_id, upload, without_rate_limits
from app.tests.xlsx_kit import Cell, rewrite, xlsx

CSV, XLSX, JSON = SourceFormat.CSV, SourceFormat.XLSX, SourceFormat.JSON
SHEET = "xl/worksheets/sheet1.xml"

# CSV/formula injection payloads (OWASP): each must come back byte for byte as text.
FORMULA_LIKE = (
    "=1+1",
    "+1+1",
    "-1+1",
    "@SUM(A1:A2)",
    '=HYPERLINK("https://evil.test","x")',
    "=cmd|' /C calc'!A0",
    "\t=1+1",
    "=1+1\r",
)


def _zip(parts: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        for name, data in parts.items():
            zf.writestr(name, data)
    return out.getvalue()


def test_malformed_archives_are_refused_with_an_upload_refusal() -> None:
    good = xlsx([[("s", "a")], [("s", "1")]])
    with zipfile.ZipFile(io.BytesIO(good)) as zf:
        parts = {n: zf.read(n) for n in zf.namelist()}
    malformed = {
        "truncated archive": good[: len(good) // 2],
        "no central directory": good[: good.rfind(b"PK\x01\x02")],
        "corrupt after the magic": b"PK\x03\x04" + b"\xff" * 512,
        "empty archive": _zip({}),
        "sheet XML truncated": rewrite(good, SHEET, parts[SHEET][: len(parts[SHEET]) // 2]),
        "sheet XML not XML": rewrite(good, SHEET, b"\x00\x01binary"),
        "workbook part missing": _zip({k: v for k, v in parts.items() if k != "xl/workbook.xml"}),
        "nested archive as the sheet": rewrite(good, SHEET, good),
        "path traversal entry": _zip({**parts, "../../etc/evil.xml": b"<x/>"}),
        "DTD in the sheet": rewrite(
            good,
            SHEET,
            b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]>'
            + parts[SHEET].split(b"?>", 1)[1],
        ),
    }
    codes = {}
    for name, data in malformed.items():
        try:
            parsed = parse_upload(data, XLSX, AdapterOptions(), HARD_LIMITS)
        except AdapterError as exc:
            assert isinstance(exc.code, UploadRefusal), name
            codes[name] = exc.code
        else:  # an extra, ignored entry is acceptable only if nothing of it is read
            assert name == "path traversal entry", (name, parsed.rows)
            assert [r.values for r in parsed.rows] == [{"a": "1"}]
    assert "passwd" not in json.dumps({k: str(v) for k, v in codes.items()})
    assert set(codes) >= set(malformed) - {"path traversal entry"}, codes


def test_formula_like_cells_are_stored_as_text() -> None:
    def csv_cell(value: str) -> str:
        return '"' + value.replace('"', '""') + '"'

    csv = ("a\n" + "\n".join(csv_cell(v) for v in FORMULA_LIKE) + "\n").encode()
    parsed = parse_upload(csv, CSV, AdapterOptions(), HARD_LIMITS)
    assert [r.values["a"] for r in parsed.rows] == list(FORMULA_LIKE)
    as_json = json.dumps([{"a": v} for v in FORMULA_LIKE]).encode()
    parsed = parse_upload(as_json, JSON, AdapterOptions(), HARD_LIMITS)
    assert [r.values["a"] for r in parsed.rows] == list(FORMULA_LIKE)
    # XLSX: a formula-like string cell is that string, not a formula.
    rows: list[list[Cell]] = [[("s", "a")]]
    rows.extend([("s", v.strip())] for v in FORMULA_LIKE[:6])
    book = xlsx(rows)
    parsed = parse_upload(book, XLSX, AdapterOptions(), HARD_LIMITS)
    assert [r.values["a"] for r in parsed.rows] == [v.strip() for v in FORMULA_LIKE[:6]]


@pytest.mark.db
def test_crlf_in_idempotency_key_or_correlation_id_is_refused(
    api: TestClient, db: str, artifact_dir: Path
) -> None:
    without_rate_limits(api)  # the rate limits themselves: test_auth.py (BE-28.3)
    steward = dev_token(api, "data_steward")
    source = seeded_source_id(db)
    jobs = "SELECT count(*) AS n FROM import_job"
    before = sql(db, jobs)[0]["n"]
    for key in ("k\r\nX-Injected: 1", "k\nX-Injected: 1", "k\r", "k\x00", "k k", "k\t"):
        r = upload(api, steward, source, b"a\n1\n", key=key)
        assert r.status_code == 422, (repr(key), r.text)
        assert set(r.json()) >= {"code", "message", "details", "correlation_id"}
        assert "X-Injected" not in r.headers and "X-Injected" not in r.text, repr(key)
    assert sql(db, jobs)[0]["n"] == before  # nothing written

    for unsafe in ("c\r\nSet-Cookie: session=x", "c\nX-Injected: 1", "c\r", "c" * 500):
        r = api.get("/api/v1/me", headers={**steward, "X-Correlation-ID": unsafe})
        assert r.status_code == 200, r.text
        served = r.headers["X-Correlation-ID"]
        assert served != unsafe and uuid.UUID(served), repr(unsafe)  # a fresh id instead
        assert "set-cookie" not in r.headers and "X-Injected" not in r.headers
    # The refused id reaches no audit row either: a write under it is audited by the fresh id.
    r = upload(api, steward, source, b"a\n1\n", cid="c\r\nX-Injected: 1")
    assert r.status_code == 201, r.text
    served = r.headers["X-Correlation-ID"]
    assert "import.create" in [e["action"] for e in events(db, served)]
    assert (
        sql(db, "SELECT count(*) AS n FROM audit_event WHERE correlation_id LIKE '%Injected%'")[0][
            "n"
        ]
        == 0
    )
    assert IMPORTS == "/api/v1/imports"

    # Q2 ruling: a `$`-anchored pattern takes no trailing newline (`fullmatch`, not `match`).
    invalid_code = ParameterProblem.INVALID_CODE
    assert check_schema({"kind": "ENUM", "values": ["A_1"]}) == []
    assert [(p.path, p.code) for p in check_schema({"kind": "ENUM", "values": ["A_1\n"]})] == [
        ("values", invalid_code)
    ]
    for code, problems in (("B01", []), ("B01\n", [("buckets[0].code", invalid_code)])):
        bucket = {"code": code, "lower_exclusive": -1, "upper_inclusive": None}
        _, found = check_parameters({"kind": "DAY_BUCKETS"}, {"buckets": [bucket]}, frozenset())
        assert [(p.path, p.code) for p in found] == problems, repr(code)
    s3: dict[str, Any] = {
        "ENV": "test",
        "DATABASE_URL": db,
        "ARTIFACT_STORE_KIND": "s3",
        "ARTIFACT_S3_BUCKET": "moodymate-raw",
        "ARTIFACT_S3_PREFIX": "raw/",
    }
    assert Settings(**s3).ARTIFACT_S3_BUCKET == "moodymate-raw"
    for setting in ("ARTIFACT_S3_BUCKET", "ARTIFACT_S3_PREFIX"):
        with pytest.raises(ValidationError, match=setting):
            Settings(**{**s3, setting: s3[setting] + "\n"})
    ref = "sha256/ab/" + "ab" * 32
    store = FilesystemArtifactStore(artifact_dir)
    assert store.path(ref) == artifact_dir / ref
    with pytest.raises(ArtifactIntegrityError) as refused_ref:
        store.path(ref + "\n")
    assert refused_ref.value.details == {"reason": "malformed_ref"}
