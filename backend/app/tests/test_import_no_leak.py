"""BE-8.6: no raw value leaks. Fixtures full of a sentinel string (an accepted file with
malformed rows, and files that fail to parse in every adapter) are uploaded. The sentinel
reaches source_record (proving the fixtures carry it) and nowhere else: not the captured
logs at DEBUG, not any response body, not the audit details of the imports.

BE-14.6 (Phase 8 review D3): an accepted workbook with an out-of-range date, whose serial
openpyxl quotes in a warning, is uploaded with the app's own warning filter in place and
warnings printed to stderr as outside pytest: the serial never reaches stderr."""

import json
import logging
import sys
import uuid
import warnings
from pathlib import Path
from typing import Any, TextIO

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.logging import JsonFormatter
from app.ingestion.adapters import HARD_LIMITS, AdapterOptions, parse_upload
from app.ingestion.adapters.xlsx_adapter import silence_openpyxl_warnings
from app.ingestion.constants import SourceFormat
from app.main import create_app
from app.tests.conftest import dev_token, sql
from app.tests.helpers import IMPORTS, seeded_source_id, source_for, upload
from app.tests.xlsx_kit import Cell, amplified, rewrite, xlsx

pytestmark = pytest.mark.db

SENTINEL = "ZZQ-SENTINEL-4471"
S = SENTINEL
# A date-formatted serial far beyond Excel's dates: openpyxl's warning quotes it.
DATE_SENTINEL = "4471447144714471"


def accepted_csv() -> bytes:
    """Every header and cell carries the sentinel, with ragged, blank and quoted rows."""
    header = f"instrument_id,{S}_a,{S}_b"
    rows = [
        f"{S}-1,{S}-x,{S}-y",
        f"{S}-2,{S}-short",  # missing a cell
        f"{S}-3,{S}-x,{S}-y,{S}-extra,{S}-extra2",  # extra cells
        "",  # an interior blank row
        f'{S}-4,"{S} quoted, with comma","{S} multi\nline"',
    ]
    return (
        "# ILLUSTRATIVE / SYNTHETIC sentinel fixture\n" + header + "\n" + "\n".join(rows) + "\n"
    ).encode()


def _broken_sheet() -> bytes:
    sheet = f'<worksheet><sheetData><row r="1"><c t="inlineStr"><is><t>{S}</t>'.encode()
    return rewrite(xlsx([[("s", "a")]]), "xl/worksheets/sheet1.xml", sheet)


def failing_files() -> list[tuple[str, bytes, str]]:
    """(format, bytes, expected error code): one parse failure of each kind."""
    long = S * 300  # > 4,000 characters
    return [
        ("CSV", f"instrument_id\n{long}\n".encode(), "cell_too_long"),
        ("CSV", f'instrument_id,b\n{S},"{S}"x\n'.encode(), "csv_malformed"),
        ("CSV", f"{S},{S}\n1,2\n".encode(), "invalid_header"),
        ("CSV", f"instrument_id\n{S}é\n".encode("cp1252"), "encoding_not_utf8"),
        ("CSV", f"instrument_id\n{S}\x00\n".encode(), "invalid_character"),
        ("CSV", f'[{{"{S}": "{S}"}}]'.encode(), "format_mismatch"),
        ("JSON", f'[{{"a": {{"{S}": "{S}"}}}}]'.encode(), "json_nested_value"),
        ("JSON", f'[{{"a": "{S}"'.encode(), "json_malformed"),
        ("JSON", f'{{"marker": "{S}", "records": []}}'.encode(), "json_invalid_shape"),
        ("JSON", f'[{{"{S}": "1", "{S}": "2"}}]'.encode(), "invalid_header"),
        ("XLSX", xlsx([[("s", "a")], [("s", long)]]), "cell_too_long"),
        ("XLSX", xlsx([[("s", S), ("s", S)], [("s", "1")]]), "invalid_header"),
        ("XLSX", _broken_sheet(), "xlsx_invalid"),
        ("XLSX", xlsx([[("s", S)], [("s", S)]])[:-40], "unsupported_file_type"),
        # D-25 §1 (Phase 8 review D1): one shared string decoding past 50 MiB.
        ("XLSX", amplified((S * 300)[:4000], HARD_LIMITS.max_decoded_chars), "content_too_large"),
    ]


def out_of_range_date_xlsx() -> bytes:
    header: list[Cell] = [("s", "instrument_id"), ("s", f"{S}_d")]
    return xlsx([header, [("s", f"{S}-9"), ("d", DATE_SENTINEL)]])


def _to_stderr(
    message: Warning | str,
    category: type[Warning],
    filename: str,
    lineno: int,
    file: TextIO | None = None,
    line: str | None = None,
) -> None:
    """Print a warning as Python does outside pytest (which records them instead)."""
    sys.stderr.write(warnings.formatwarning(message, category, filename, lineno, line))


def test_no_sentinel_in_logs_error_bodies_or_audit(
    db: str, tmp_path: Path, caplog: pytest.LogCaptureFixture, capfd: pytest.CaptureFixture[str]
) -> None:
    settings = Settings(
        ENV="test", DATABASE_URL=db, ARTIFACT_STORE_DIR=tmp_path / "artifacts", LOG_LEVEL="DEBUG"
    )
    bodies: list[str] = []
    correlations: list[str] = []
    app = create_app(settings)  # configure_logging replaces the root handlers...
    logging.getLogger().addHandler(caplog.handler)  # ...so capture alongside the app's own
    caplog.set_level(logging.DEBUG)
    with TestClient(app) as api:
        steward = dev_token(api, "data_steward")
        analyst = dev_token(api, "risk_analyst")
        sources = {fmt: source_for(api, db, steward, fmt) for fmt in ("XLSX", "JSON")}
        sources["CSV"] = seeded_source_id(db)

        def send(fmt: str, data: bytes, **kw: Any) -> Any:
            correlation = f"leak-{uuid.uuid4().hex[:10]}"
            correlations.append(correlation)
            r = upload(api, steward, sources[fmt], data, cid=correlation, **kw)
            bodies.append(r.text)
            return r

        ok = send("CSV", accepted_csv())
        assert ok.status_code == 201, ok.text
        assert ok.json()["row_count"] == 5
        for fmt, data, code in failing_files():
            r = send(fmt, data)
            assert (r.status_code, r.json()["code"]) == (422, code), (fmt, code, r.text)
        # D3: without the app's filter, openpyxl's warning would quote the serial...
        with warnings.catch_warnings(record=True) as seen:
            warnings.simplefilter("always")
            options = AdapterOptions()
            parse_upload(out_of_range_date_xlsx(), SourceFormat.XLSX, options, HARD_LIMITS)
        assert any(DATE_SENTINEL in str(w.message) for w in seen)
        # ...and with it, on the request path, nothing reaches stderr (checked below).
        with warnings.catch_warnings():
            warnings.simplefilter("always")
            warnings.showwarning = _to_stderr
            silence_openpyxl_warnings()  # as installed when the app imports the adapter
            r = send("XLSX", out_of_range_date_xlsx())
        assert r.status_code == 201, r.text
        # Request-level refusals with the same file: replay conflict, attestation, size.
        r = send("CSV", accepted_csv(), key="leak-key")
        assert r.status_code == 201
        r = send("CSV", accepted_csv() + f"{S}-5\n".encode(), key="leak-key")
        assert r.json()["code"] == "idempotency_conflict"
        assert send("CSV", accepted_csv(), attested=False).status_code == 422
        r = send("CSV", accepted_csv() + S.encode() * (11 * 1024 * 1024 // len(S)))
        assert r.status_code == 413
        # Broken multipart framing around the sentinel (python-multipart warns on it).
        for raw in (f"--b\r\n{S}\x00\r\n".encode(), f"--b{S}".encode()):
            r = api.post(
                IMPORTS,
                content=raw,
                headers={
                    **steward,
                    "Content-Type": "multipart/form-data; boundary=b",
                    "Idempotency-Key": "k",
                },
            )
            assert (r.status_code, r.json()["code"]) == (422, "validation_error")
            bodies.append(r.text)
        items = json.loads(api.get(IMPORTS, headers=steward).text)["items"]
        assert len(items) == 3  # D-25 §3: a refused upload persists no import
        for i in items:
            bodies.append(api.get(f"{IMPORTS}/{i['id']}", headers=steward).text)
            # The profile shows column names (headers) and, to a cleared role, values; a
            # role not cleared for the source's class sees neither values nor bounds.
            masked = api.get(f"{IMPORTS}/{i['id']}/profile", headers=analyst).json()
            assert masked["columns"] and all(
                t["value"] == {"masked": True, "classification": "confidential"}
                and c["min"] == c["max"] == t["value"]
                for c in masked["columns"]
                for t in c["top_values"]
            )
        bodies.append(api.get(IMPORTS, headers=steward).text)

    # The fixture really is full of the sentinel, and it was stored as source records.
    stored = sql(db, "SELECT raw_values::text AS v, extra_values::text AS e FROM source_record")
    assert stored and all(S in row["v"] for row in stored if row["v"] != "{}")
    assert any(S in row["e"] for row in stored)

    # Error and success bodies.
    assert all(S not in body for body in bodies)

    # Logs: every captured record, formatted exactly as the app formats it, plus raw output.
    formatter = JsonFormatter()
    logged = [formatter.format(rec) for rec in caplog.records]
    logged += [rec.getMessage() for rec in caplog.records]
    out, err = capfd.readouterr()
    assert logged, "nothing was logged: the check would be vacuous"
    assert all(S not in line for line in logged)
    assert S not in out and S not in err
    assert all(DATE_SENTINEL not in text for text in (*logged, *bodies, out, err))

    # Audit details of every import request (success and DENIED).
    events = sql(
        db,
        "SELECT action, outcome, details::text AS d FROM audit_event"
        " WHERE correlation_id = ANY($1::text[])",
        correlations,
    )
    assert {e["outcome"] for e in events} == {"SUCCESS", "DENIED"}
    assert len([e for e in events if e["action"] == "import.create"]) == len(correlations)
    # Phase 9: the accepted uploads were profiled in the same requests (the profile holds
    # the sentinel's columns and values), and the profile's audit holds none of them.
    assert len([e for e in events if e["action"] == "import.profile"]) == 3
    profiles = sql(db, "SELECT columns::text AS c FROM data_profile_snapshot")
    assert profiles and all(S in p["c"] for p in profiles)
    assert all(S not in e["d"] and DATE_SENTINEL not in e["d"] for e in events)
    refused = [e for e in events if e["outcome"] == "DENIED" and "content_too_large" in e["d"]]
    assert len(refused) == 1, "the content_too_large refusal is audited DENIED"
