"""BE-8.5 (D-06, D-21): the import_job transition table lives in ingestion/constants.py and
the service accepts exactly its transitions. The database trigger is walked against the same
table in test_import_tables.py."""

import pytest

from app.core.errors import ApiError
from app.ingestion.constants import IMPORT_TRANSITIONS, ImportStatus
from app.ingestion.models import ImportJob
from app.ingestion.service import move

PAIRS = [(a, b) for a in ImportStatus for b in ImportStatus]


def test_the_table_covers_every_status() -> None:
    assert set(IMPORT_TRANSITIONS) == set(ImportStatus)
    assert all(targets <= set(ImportStatus) for targets in IMPORT_TRANSITIONS.values())


def test_the_status_set_is_d21() -> None:
    assert [s.value for s in ImportStatus] == [
        "CREATED",
        "RECEIVED",
        "PROFILING",
        "MAPPING_REQUIRED",
        "VALIDATING",
        "COMPLETED",
        "COMPLETED_WITH_WARNINGS",
        "QUARANTINED",
        "FAILED",
        "CANCELLED",
    ]


@pytest.mark.parametrize(("start", "target"), PAIRS, ids=[f"{a}->{b}" for a, b in PAIRS])
def test_service_accepts_only_the_tables_transitions(
    start: ImportStatus, target: ImportStatus
) -> None:
    job = ImportJob(status=start)
    if target in IMPORT_TRANSITIONS[start]:
        move(job, target)
        assert job.status == target
    else:
        with pytest.raises(ApiError) as caught:
            move(job, target)
        assert (caught.value.status_code, caught.value.code) == (409, "invalid_status")
        assert caught.value.details == {"from": start.value, "to": target.value}
        assert job.status == start


def test_outcomes_are_final_and_reachable() -> None:
    outcomes = {
        ImportStatus.COMPLETED,
        ImportStatus.COMPLETED_WITH_WARNINGS,
        ImportStatus.QUARANTINED,
        ImportStatus.FAILED,
        ImportStatus.CANCELLED,
    }
    assert {s for s, t in IMPORT_TRANSITIONS.items() if not t} == outcomes
    reachable = {ImportStatus.CREATED}
    frontier = [ImportStatus.CREATED]
    while frontier:
        for nxt in IMPORT_TRANSITIONS[frontier.pop()] - reachable:
            reachable.add(nxt)
            frontier.append(nxt)
    assert reachable == set(ImportStatus)


def test_upload_and_profiling_paths() -> None:
    """D-21: upload sets RECEIVED; profiling returns to RECEIVED or MAPPING_REQUIRED."""
    assert ImportStatus.RECEIVED in IMPORT_TRANSITIONS[ImportStatus.CREATED]
    assert IMPORT_TRANSITIONS[ImportStatus.PROFILING] >= {
        ImportStatus.RECEIVED,
        ImportStatus.MAPPING_REQUIRED,
    }
    assert ImportStatus.QUARANTINED in IMPORT_TRANSITIONS[ImportStatus.VALIDATING]
