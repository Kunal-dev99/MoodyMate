from pathlib import Path

APP = Path(__file__).resolve().parent.parent
MODULES = [
    "governance",
    "ingestion",
    "mapping",
    "domain",
    "rules",
    "calculations",
    "orchestration",
    "reconciliation",
    "lineage",
    "reporting",
    "ai",
]
FILES = {
    "router.py",
    "schemas.py",
    "models.py",
    "service.py",
    "repository.py",
    "events.py",
    "constants.py",
}


def test_every_module_has_the_standard_file_set() -> None:
    for m in MODULES:
        present = {p.name for p in (APP / m).glob("*.py")} - {"__init__.py"}
        assert present == FILES, f"{m}: {sorted(present ^ FILES)}"
