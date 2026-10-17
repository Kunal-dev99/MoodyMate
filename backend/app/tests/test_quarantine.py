"""Phase 11 (BE-11.4, BE-11.5): quarantine review, retry and the import manifest, in two
essential tests.

4. GET /quarantine/{batch_id} masks values by classification (auditor), risk_analyst gets 403
   on both endpoints, another tenant gets the same 404 as a random id; every legal D-20
   disposition succeeds and every illegal one is 409 (audited); a retry import resolves the
   matching RETRY_READY items (CORRECTED -> RESOLVED, or back to RETRY_READY); the
   quarantine_item row count never decreases.
5. The manifest on GET /imports/{id}: its keys and values after validation, unchanged after a
   later mapping version is activated and a schema change proposal is applied; the import's
   mapping pin cannot move to a version that is not ACTIVE (0014 trigger).
"""

import json
import uuid
from typing import Any

import asyncpg
import pytest
from fastapi.testclient import TestClient

from app.ingestion.constants import MANUAL_QUARANTINE_MOVES, QuarantineStatus
from app.ingestion.schemas import ImportManifest
from app.tests.conftest import dev_token, insert_tenant, sql
from app.tests.helpers import (
    FIELD_KEYS,
    IMPORTS,
    INSTRUMENT_RULES,
    MAPPINGS,
    PROPOSALS,
    activate_version,
    cid,
    events,
    import_sample,
    instrument_mapping,
    mrule,
    sample,
    schema_id,
    seeded_source_id,
    token_for,
    upload,
    validate_import,
)

pytestmark = pytest.mark.db

Q = QuarantineStatus
HEADER = (
    "instrument_id,instrument_type,currency,counterparty_id,product_family,notional,"
    "interest_rate,maturity_date,book_code"
)
SPILL = "SPILL-9d1c"  # an extra cell: the row is REJECTED (EXTRA_CELLS)


def quarantine_count(db: str) -> int:
    return int(sql(db, "SELECT count(*) AS n FROM quarantine_item")[0]["n"])


# --- 4. Quarantine read, dispositions, retry (BE-11.4) ------------------------------------------


def test_quarantine_masking_dispositions_and_retry(api: TestClient, db: str) -> None:
    steward, approver = dev_token(api, "data_steward"), dev_token(api, "approver")
    auditor, analyst = dev_token(api, "auditor"), dev_token(api, "risk_analyst")
    clean = import_sample(api, steward, db, sample("instrument.csv"))
    instrument_mapping(api, db, steward, approver, clean["id"])
    assert validate_import(api, steward, clean["id"]).json()["status"] == "COMPLETED"

    # The mixed-errors fixture plus one row with a cell beyond the header.
    data = sample("instrument__mixed_errors.csv") + (
        f"INS-000026,LOAN,EUR,CP-1111,RETAIL,1.00,1.0000,2030-01-01,BK-01,{SPILL}\n".encode()
    )
    held = import_sample(api, steward, db, data)
    body = validate_import(api, steward, held["id"]).json()
    assert body["status"] == "QUARANTINED"
    assert body["manifest"]["quality"] | {"row_count": 26} == {
        "row_count": 26,
        "accepted_records": 20,
        "quarantined_records": 5,
        "rejected_records": 1,
        "warnings": 0,
    }
    batch = body["batches"][0]["id"]
    base = f"/api/v1/quarantine/{batch}"
    counted = [quarantine_count(db)]
    assert counted == [6]

    def seen() -> None:
        counted.append(quarantine_count(db))
        assert counted[-1] >= counted[-2], counted  # never decreases

    # Read: by row, with reasons; steward sees values, auditor sees them masked by class.
    view = api.get(base, headers=steward).json()
    rows = {i["row"]: i for i in view["items"]}
    assert {r: (i["status"], i["reason_codes"]) for r, i in rows.items()} == {
        3: ("OPEN", ["INVALID_DATE"]),
        4: ("OPEN", ["SCALE_EXCEEDED"]),
        5: ("OPEN", ["DUPLICATE_BUSINESS_KEY"]),
        6: ("OPEN", ["INVALID_CURRENCY_CODE"]),
        8: ("OPEN", ["REQUIRED_VALUE_MISSING"]),
        26: ("REJECTED", ["EXTRA_CELLS"]),
    }
    assert view["status_counts"] == {"OPEN": 5, "REJECTED": 1} and view["total"] == 6
    assert rows[4]["values"]["notional"] == "510544.10001" and rows[26]["extra_values"] == [SPILL]
    assert rows[4]["owner"] and rows[4]["allowed_dispositions"] == ["UNDER_REVIEW"]
    masked = {"masked": True, "classification": "confidential"}
    for reader in (auditor, approver):
        r = api.get(base, headers=reader)
        assert r.status_code == 200, r.text
        by_row = {i["row"]: i for i in r.json()["items"]}
        if reader is auditor:  # cleared up to internal
            assert by_row[4]["values"]["notional"] == masked
            assert by_row[4]["values"]["counterparty_id"] == masked
            assert by_row[26]["extra_values"] == [masked]
            assert SPILL not in r.text and "510544.10001" not in r.text
        assert by_row[4]["values"]["currency"] == "GBP"  # public
        assert by_row[4]["values"]["instrument_id"] == "INS-000004"  # internal
    page = api.get(base, params={"status": "OPEN", "limit": 2, "offset": 1}, headers=steward)
    assert [i["row"] for i in page.json()["items"]] == [4, 5] and page.json()["total"] == 5

    # risk_analyst: 403 on both endpoints; another tenant: the same 404 as a random id.
    item = {row: i["id"] for row, i in rows.items()}
    move = f"{base}/items/{item[3]}/disposition"
    assert api.get(base, headers=analyst).status_code == 403
    assert api.post(move, json={"status": "UNDER_REVIEW"}, headers=analyst).status_code == 403
    outsider = token_for(
        db, "dev:p11-outsider", ("data_steward",), tenant_id=insert_tenant(db, "p11-outsider")
    )
    mine = api.get(base, headers=outsider)
    absent = api.get(f"/api/v1/quarantine/{uuid.uuid4()}", headers=outsider)
    assert mine.status_code == absent.status_code == 404
    assert {k: v for k, v in mine.json().items() if k != "correlation_id"} == {
        k: v for k, v in absent.json().items() if k != "correlation_id"
    }
    r = api.post(move, json={"status": "UNDER_REVIEW"}, headers=outsider)
    assert r.status_code == 404
    seen()

    # Every legal manual move succeeds (audited); every other target is 409 (audited) and
    # changes nothing. CORRECTED and RESOLVED come only from a retry.
    def status_of(row: int) -> str:
        return str(
            api.get(base, headers=steward).json()["items"][[3, 4, 5, 6, 8, 26].index(row)]["status"]
        )

    def refuse_all_illegal(row: int) -> None:
        current = Q(status_of(row))
        for to in Q:
            if (current, to) in MANUAL_QUARANTINE_MOVES:
                continue
            c = cid()
            r = api.post(
                f"{base}/items/{item[row]}/disposition",
                json={"status": to},
                headers={**steward, "X-Correlation-ID": c},
            )
            assert (r.status_code, r.json()["code"]) == (409, "invalid_transition"), (current, to)
            [denied] = events(db, c)
            assert denied["outcome"] == "DENIED" and "invalid_transition" in denied["details"]
            assert status_of(row) == current
        seen()

    def dispose(row: int, to: Q) -> None:
        before = status_of(row)
        legal.add((before, to.value))
        c = cid()
        r = api.post(
            f"{base}/items/{item[row]}/disposition",
            json={"status": to, "comment": "Checked with the source owner."},
            headers={**steward, "X-Correlation-ID": c},
        )
        assert r.status_code == 200 and r.json()["status"] == to, r.text
        [event] = events(db, c)
        assert (event["action"], event["outcome"]) == ("quarantine.disposition", "SUCCESS")
        details = json.loads(event["details"])
        assert details == {
            "batch_id": batch,
            "import_id": held["id"],
            "from": before,
            "to": to.value,
            "comment": details["comment"],  # INTERNAL: masked or clear by the reader's role
        }
        seen()

    legal: set[tuple[str, str]] = set()
    walks = {
        3: [Q.UNDER_REVIEW, Q.OPEN, Q.UNDER_REVIEW, Q.REJECTED],
        4: [Q.UNDER_REVIEW, Q.RETRY_READY],
        6: [Q.UNDER_REVIEW, Q.RETRY_READY],
        8: [Q.UNDER_REVIEW, Q.RETRY_READY],
    }
    for row, steps in walks.items():
        for to in steps:
            if row == 3:
                refuse_all_illegal(row)
            dispose(row, to)
    refuse_all_illegal(3)  # REJECTED (by review)
    refuse_all_illegal(4)  # RETRY_READY
    refuse_all_illegal(26)  # REJECTED (at validation)
    assert legal == {(a.value, b.value) for a, b in MANUAL_QUARANTINE_MOVES}

    # Retries: refused for an import that held no rows, and for an unknown import.
    c = cid()
    r = upload(
        api,
        steward,
        seeded_source_id(db),
        sample("instrument.csv"),
        cid=c,
        retry_of_import_id=clean["id"],
    )
    assert (r.status_code, r.json()["code"]) == (409, "retry_not_applicable")
    assert [e["outcome"] for e in events(db, c)] == ["DENIED"]
    r = upload(
        api,
        steward,
        seeded_source_id(db),
        sample("instrument.csv"),
        retry_of_import_id=str(uuid.uuid4()),
    )
    assert r.status_code == 404

    # A retry with row 4's key corrected and row 6's key still wrong; row 8's key absent.
    fixed = f"{HEADER}\nINS-000004,LOAN,GBP,CP-7600,TREASURY,510544.10,5.7181,2029-04-10,BK-12\n"
    still = "INS-000006,DEPOSIT,EURO,CP-6589,TREASURY,27988.62,9.4752,2034-11-09,BK-27\n"
    retry = import_sample(api, steward, db, (fixed + still).encode(), retry_of_import_id=held["id"])
    assert retry["retry_of_import_id"] == held["id"]
    c = cid()
    r = validate_import(api, steward, retry["id"], **{"X-Correlation-ID": c})
    assert r.status_code == 200 and r.json()["status"] == "QUARANTINED", r.text
    seen()
    after = {i["row"]: i for i in api.get(base, headers=steward).json()["items"]}
    assert (after[4]["status"], after[4]["resolved_by_import_id"]) == ("RESOLVED", retry["id"])
    assert (after[6]["status"], after[6]["resolved_by_import_id"]) == ("RETRY_READY", None)
    assert (after[8]["status"], after[3]["status"], after[26]["status"]) == (
        "RETRY_READY",
        "REJECTED",
        "REJECTED",
    )
    actions = sorted(e["action"] for e in events(db, c))
    assert actions == [
        "import.validate",
        "quarantine.correct",
        "quarantine.correct",
        "quarantine.resolve",
        "quarantine.retry_ready",
    ]
    refuse_all_illegal(4)  # RESOLVED is final
    # Nothing was deleted: the original's items plus the retry's one held row.
    assert counted[-1] == 7 and counted == sorted(counted)
    assert sql(db, "SELECT count(*) AS n FROM source_record")[0]["n"] == 25 + 26 + 2

    # Review P11-6: with INGESTION_ENABLED off, validate and dispositions are refused (409,
    # audited DENIED) and the quarantine stays readable.
    from app.core.config import Settings
    from app.main import create_app

    with TestClient(
        create_app(Settings(ENV="test", DATABASE_URL=db, INGESTION_ENABLED=False))
    ) as off:
        steward_off = dev_token(off, "data_steward")
        item = next(iter(rows.values()))
        for path, body in (
            (f"/api/v1/imports/{held['id']}/validate", None),
            (f"{base}/items/{item['id']}/disposition", {"status": "UNDER_REVIEW"}),
        ):
            c = f"p11-off-{uuid.uuid4().hex[:8]}"
            r = off.post(path, json=body, headers={**steward_off, "X-Correlation-ID": c})
            assert (r.status_code, r.json()["code"]) == (409, "ingestion_disabled"), path
            assert [e["outcome"] for e in events(db, c)] == ["DENIED"], path
        assert off.get(base, headers=steward_off).status_code == 200


# --- 5. The manifest, stable after later changes (BE-11.5) ----------------------------------


def test_manifest_is_stable_after_mapping_and_schema_changes(api: TestClient, db: str) -> None:
    steward, approver = dev_token(api, "data_steward"), dev_token(api, "approver")
    dry_run = import_sample(api, steward, db, sample("instrument.csv"))
    mapping_v1 = instrument_mapping(api, db, steward, approver, dry_run["id"])
    first = import_sample(api, steward, db, sample("instrument__mixed_errors.csv"))
    validated = validate_import(api, steward, first["id"]).json()
    detail = api.get(f"{IMPORTS}/{first['id']}", headers=dev_token(api, "risk_analyst")).json()
    manifest = detail["manifest"]
    assert manifest == validated["manifest"]

    assert set(manifest) == set(ImportManifest.model_fields)
    profile = sql(db, "SELECT * FROM validation_profile")[0]
    contract = sql(
        db, "SELECT * FROM source_contract WHERE id = $1", uuid.UUID(detail["source_contract_id"])
    )[0]
    expected: dict[str, Any] = {
        "import_id": first["id"],
        "status": "QUARANTINED",
        "source_id": seeded_source_id(db),
        "source_code": "core_banking",
        "source_contract_id": detail["source_contract_id"],
        "source_contract_version": 1,
        "source_contract_sha256": contract["content_sha256"],
        "schema_id": str(schema_id(db, "instrument")),
        "schema_code": "instrument",
        "schema_version_id": detail["schema_version_id"],
        "schema_version": 1,
        "mapping_id": mapping_v1["mapping_id"],
        "mapping_code": "core_instrument",
        "mapping_version_id": mapping_v1["id"],
        "mapping_version": 1,
        "mapping_sha256": mapping_v1["content_sha256"],
        "validation_profile": {
            "id": str(profile["id"]),
            "code": "default",
            "version": 1,
            "content_sha256": profile["content_sha256"],
            "provisional": True,
        },
        "validator_version": 1,
        "artifact": {
            "sha256": detail["artifact"]["sha256"],
            "size_bytes": detail["artifact"]["size_bytes"],
            "classification": "confidential",
        },
        "quality": {
            "row_count": 25,
            "accepted_records": 20,
            "quarantined_records": 5,
            "rejected_records": 0,
            "warnings": 0,
        },
    }
    assert {k: manifest[k] for k in expected} == expected
    stored = sql(db, "SELECT * FROM import_manifest")[0]
    assert manifest["manifest_sha256"] == stored["manifest_sha256"]
    assert (stored["accepted_count"], stored["quarantined_count"]) == (20, 5)

    def unchanged(step: str) -> None:
        now = api.get(f"{IMPORTS}/{first['id']}", headers=steward).json()
        assert now["manifest"] == manifest, step
        assert now["mapping_version_id"] == mapping_v1["id"], step
        assert sql(db, "SELECT count(*) AS n FROM canonical_record")[0]["n"] == 20, step

    # A later mapping version is activated: v1 is DEPRECATED, the manifest stands.
    rules = [
        mrule("instrument_type", "instrument_type", "UPPER")
        if r["source_column"] == "instrument_type"
        else r
        for r in INSTRUMENT_RULES
    ]
    r = api.post(
        f"{MAPPINGS}/{mapping_v1['mapping_id']}/versions", json={"rules": rules}, headers=steward
    )
    assert r.status_code == 201, r.text
    activate_version(api, steward, approver, r.json(), dry_run["id"])
    statuses = {
        v["version"]: v["status"]
        for v in api.get(f"{MAPPINGS}/{mapping_v1['mapping_id']}", headers=steward).json()[
            "versions"
        ]
    }
    assert statuses == {1: "DEPRECATED", 2: "ACTIVE"}
    unchanged("mapping v2 active")

    # The 0014 pin trigger: an import pins only an ACTIVE version of its own contract.
    later = import_sample(api, steward, db, sample("instrument.csv"))
    assert later["status"] == "RECEIVED"
    with pytest.raises(asyncpg.InsufficientPrivilegeError, match="must pin an ACTIVE mapping"):
        sql(
            db,
            "UPDATE import_job SET mapping_version_id = $1 WHERE id = $2",
            uuid.UUID(mapping_v1["id"]),
            uuid.UUID(later["id"]),
        )

    # A schema change proposal is applied: the pinned schema version is DEPRECATED.
    sid = schema_id(db, "instrument")
    versions = api.get(f"/api/v1/schemas/{sid}/versions", headers=steward).json()["items"]
    v1 = next(v for v in versions if v["version"] == 1)
    fields = [
        {k: f[k] for k in FIELD_KEYS} for f in v1["fields"] if f["name"] != "legacy_book_code"
    ]
    proposal = api.post(
        PROPOSALS,
        json={"schema_id": str(sid), "title": "Drop legacy book code", "fields": fields},
        headers=steward,
    ).json()
    pbase = f"{PROPOSALS}/{proposal['id']}"
    assert api.post(f"{pbase}/impact-analysis", headers=steward).status_code == 200
    assert api.post(f"{pbase}/submit", json={}, headers=steward).status_code == 200
    r = api.post(f"{pbase}/approval", json={"decision": "APPROVE"}, headers=approver)
    assert r.status_code == 200, r.text
    versions = api.get(f"/api/v1/schemas/{sid}/versions", headers=steward).json()["items"]
    assert next(v for v in versions if v["version"] == 1)["status"] == "DEPRECATED"
    unchanged("schema change applied")
