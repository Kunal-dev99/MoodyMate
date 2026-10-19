"""Credit risk data in the domain (BE-19.1, CR-01, FC-01; D-37, D-38).

(a) Seeded ACTIVE schemas `counterparty` v1 and `credit_exposure` v1 for the default tenant
    (ILLUSTRATIVE / PROVISIONAL, as 0007), with their `schema_dependency` rows.
(b) D-38 (a D-01 exception approved by the user, 2026-10-19): the two `schema_code` CHECKs,
    on `domain_projection` and `dataset_snapshot_member`, are replaced by wider ones under
    the same names, in this one transaction. No row changes; every existing row satisfies
    the wider CHECK, and an unknown code is still refused.
(c) `counterparty`, `counterparty_version`, `credit_exposure` and `credit_exposure_version`:
    stable identities per (tenant, source system, business key) and one version row per
    accepted canonical record (D-37 §2, D-29 §3). Append-only, TRUNCATE included, with the
    0017 insert checks (same tenant and source; a version repeats its canonical record's
    import, source record, ordinal, schema version and payload hash). Amounts NUMERIC,
    dates DATE, days past due INTEGER, the default flag BOOLEAN.
(d) `credit_vocabulary` (D-37 §5): append-only, seeded for the default tenant with neutral
    grade codes (G01..G08, D; no rating-agency symbol), the credit segments, facility types
    and collateral types. PROVISIONAL / ILLUSTRATIVE.

Every seed row carries `seeded_by = '0022_credit_domain'` (D-11).

The downgrade refuses while any credit projection, credit domain row, snapshot member or
import of the new codes, or untagged vocabulary row exists (D-29: domain rows are kept).
Then it restores the narrow CHECKs, drops the new tables and removes only the tagged seed.

Revision ID: 0022
Revises: 0021 (pre-numbered for Phase 19; Phase 20 takes 0023_credit_permissions)
Create Date: 2026-10-19

D-01: additive, except the D-38 CHECK widening (the named exception). D-02: the downgrade
restores the prior schema.
"""

import hashlib
import json
import uuid
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The named D-01 exception this migration's upgrade relies on (test_committed_artifacts).
D01_EXCEPTION = "D-38"

TAG = "0022_credit_domain"
ACTOR = "system:migration"
TENANT = "00000000-0000-4000-8000-000000000001"
NS = uuid.UUID("6f1c2a52-6d0e-4f0b-9a35-3c1a8b5e7d10")  # 0003's namespace for seed IDs
OWNER = "Data governance (data_steward), provisional"
ILLUSTRATIVE = "ILLUSTRATIVE seed for Feature 04 (D-37); not client-approved."

# Frozen copies of the code's enums and seeds (test_domain keeps them equal).
OLD_SCHEMA_CODES = ("instrument", "position", "cashflow_event")
SCHEMA_CODES = ("counterparty", "credit_exposure")  # added by this migration
VOCABULARY_KINDS = ("rating_grade", "credit_segment", "facility_type", "collateral_type")
CHECKED = ("domain_projection", "dataset_snapshot_member")  # the two widened CHECKs


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def f(
    name: str,
    data_type: str,
    required: bool,
    classification: str,
    *,
    unit: str | None = None,
    precision: int | None = None,
    scale: int | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "data_type": data_type,
        "required": required,
        "unit": unit,
        "precision": precision,
        "scale": scale,
        "classification": classification,
        "extension_eligible": False,
        "description": description,
    }


# D-37 §1 with the D-43 §1 classifications.
COUNTERPARTY_V1 = [
    f("counterparty_id", "STRING", True, "internal", description="Stable source identity."),
    f("counterparty_name", "STRING", True, "confidential"),
    f("segment", "STRING", True, "internal", description="credit_segment vocabulary."),
    f("country", "STRING", True, "public", unit="ISO 3166-1 alpha-2"),
]
CREDIT_EXPOSURE_V1 = [
    f("exposure_id", "STRING", True, "internal", description="Stable source identity."),
    f("counterparty_id", "STRING", True, "internal", description="The obligor's key."),
    f("instrument_id", "STRING", False, "internal", description="Not read by v1 modules."),
    f("as_of_date", "DATE", True, "internal", description="Business date of the exposure."),
    f("currency", "STRING", True, "public", unit="ISO 4217"),
    f("facility_type", "STRING", True, "internal", description="facility_type vocabulary."),
    f("drawn_amount", "DECIMAL", True, "confidential", unit="currency", precision=20, scale=2),
    f("undrawn_amount", "DECIMAL", False, "confidential", unit="currency", precision=20, scale=2),
    f("days_past_due", "INTEGER", True, "internal", unit="days"),
    f("default_flag", "BOOLEAN", True, "internal"),
    f("origination_date", "DATE", False, "internal"),
    f("origination_grade", "STRING", False, "internal", description="rating_grade vocabulary."),
    f("maturity_date", "DATE", True, "internal"),
    f("effective_interest_rate", "DECIMAL", True, "internal", unit="fraction", precision=9,
      scale=6, description="A fraction (0.05 = 5%)."),  # fmt: skip
    f("collateral_type", "STRING", False, "internal", description="collateral_type vocabulary."),
    f("collateral_value", "DECIMAL", False, "confidential", unit="currency", precision=20,
      scale=2),  # fmt: skip
]
SCHEMAS = [
    (
        "counterparty",
        "Counterparty",
        "Obligor: identity, name, credit segment and country.",
        COUNTERPARTY_V1,
    ),
    (
        "credit_exposure",
        "Credit exposure",
        "A credit facility of one counterparty: amounts, delinquency, grades and collateral.",
        CREDIT_EXPOSURE_V1,
    ),
]
# (schema, dependent type, dependent ref, dependent schema code, description)
DEPENDENCIES = [
    ("counterparty", "SCHEMA", "credit_exposure", "credit_exposure",
     "Credit exposures reference counterparties."),  # fmt: skip
    ("counterparty", "MODULE", "credit_ecl (planned, Feature 04)", None, "Credit ECL module."),
    ("credit_exposure", "MODULE", "credit_ecl (planned, Feature 04)", None, "Credit ECL module."),
]
# (vocabulary, code, name, rank, default grade): D-37 §5, D-42. Neutral codes only.
VOCABULARY: list[tuple[str, str, str, int | None, bool | None]] = [
    *((("rating_grade", f"G0{i}", f"Grade {i} (illustrative)", i, False)) for i in range(1, 9)),
    ("rating_grade", "D", "Default (illustrative)", 9, True),
    ("credit_segment", "CORPORATE", "Corporate (illustrative)", None, None),
    ("credit_segment", "SME", "Small and medium enterprise (illustrative)", None, None),
    ("credit_segment", "RETAIL", "Retail (illustrative)", None, None),
    ("credit_segment", "SOVEREIGN", "Sovereign (illustrative)", None, None),
    ("facility_type", "TERM_LOAN", "Term loan (illustrative)", None, None),
    ("facility_type", "REVOLVER", "Revolving facility (illustrative)", None, None),
    ("facility_type", "GUARANTEE", "Guarantee (illustrative)", None, None),
    ("collateral_type", "CASH", "Cash (illustrative)", None, None),
    ("collateral_type", "SECURITIES", "Securities (illustrative)", None, None),
    ("collateral_type", "REAL_ESTATE", "Real estate (illustrative)", None, None),
]
VOCABULARY_NOTE = (
    "PROVISIONAL / ILLUSTRATIVE seed vocabulary (D-11, D-37 §5); not client-approved. "
    "No rating-agency or vendor scale is implied."
)

CONTRACT_KEYS = (
    "name",
    "data_type",
    "required",
    "unit",
    "precision",
    "scale",
    "classification",
    "extension_eligible",
)


def contract_sha256(fields: list[dict[str, Any]]) -> str:
    """Frozen copy of app.governance.service.contract_sha256 (as 0007's)."""
    ordered = sorted(fields, key=lambda fld: fld["name"])
    canonical = [{k: fld[k] for k in CONTRACT_KEYS} for fld in ordered]
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


IDENTITIES = ("counterparty", "credit_exposure")
VERSIONS = ("counterparty_version", "credit_exposure_version")
APPEND_ONLY_TABLES = (*IDENTITIES, *VERSIONS, "credit_vocabulary")
REGISTRY_TRIGGERS = (
    ("schema_field", "schema_field_immutable"),
    ("schema_version", "schema_version_immutable"),
)


def _row_checks(table: str, code: str, identity: str) -> str:
    """0017's version-row insert checks, for the credit tables (a frozen copy)."""
    return f"""
CREATE FUNCTION {table}_checks() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM domain_projection p WHERE p.id = NEW.domain_projection_id
            AND p.schema_code = '{code}') THEN
        RAISE EXCEPTION '{table} must belong to a {code} projection (D-29)'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM domain_projection p
            JOIN import_job j ON j.id = p.import_job_id
            JOIN canonical_record c ON c.import_job_id = p.import_job_id
        WHERE p.id = NEW.domain_projection_id AND p.tenant_id = NEW.tenant_id
            AND j.tenant_id = NEW.tenant_id AND p.import_job_id = NEW.import_job_id
            AND c.id = NEW.canonical_record_id
            AND c.source_record_id = NEW.source_record_id AND c.ordinal = NEW.source_ordinal
            AND c.schema_version_id = NEW.schema_version_id
            AND c.payload_sha256 = NEW.payload_sha256
    ) THEN
        RAISE EXCEPTION '{table} must project a canonical record of its projection''s import'
            ' and tenant (D-29, D-04)' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM {identity} i WHERE i.id = NEW.{identity}_id
            AND i.tenant_id = NEW.tenant_id
            AND i.source_system_id = (SELECT source_system_id FROM import_job
                WHERE id = NEW.import_job_id)) THEN
        RAISE EXCEPTION '{table} must version a {identity} of its tenant and source (D-04)'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$
"""


FUNCTIONS = (
    _row_checks("counterparty_version", "counterparty", "counterparty"),
    _row_checks("credit_exposure_version", "credit_exposure", "credit_exposure"),
)
# (trigger, timing and events, table, level, function); domain_append_only and
# domain_identity_checks are 0017's functions.
TRIGGERS = (
    *(
        (f"{t}_append_only", "BEFORE UPDATE OR DELETE", t, "ROW", "domain_append_only")
        for t in APPEND_ONLY_TABLES
    ),
    *(
        (f"{t}_no_truncate", "BEFORE TRUNCATE", t, "STATEMENT", "domain_append_only")
        for t in APPEND_ONLY_TABLES
    ),
    *((f"{t}_checks", "BEFORE INSERT", t, "ROW", f"{t}_checks") for t in VERSIONS),
    *((f"{t}_checks", "BEFORE INSERT", t, "ROW", "domain_identity_checks") for t in IDENTITIES),
)


def _audit_columns() -> list[sa.Column]:  # type: ignore[type-arg]
    return [
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("created_by", sa.String(length=255), nullable=False),
    ]


def _tenant(table: str) -> list[Any]:
    return [
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f(f"fk_{table}_tenant_id_tenant")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{table}")),
    ]


def _fk(table: str, column: str, target: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], [f"{target}.id"], name=op.f(f"fk_{table}_{column}_{target}")
    )


def _projected(table: str) -> list[Any]:
    """What every version row names (D-29 §6), as 0017."""
    return [
        sa.Column("import_job_id", sa.UUID(), nullable=False),
        sa.Column("domain_projection_id", sa.UUID(), nullable=False),
        sa.Column("canonical_record_id", sa.UUID(), nullable=False),
        sa.Column("source_record_id", sa.UUID(), nullable=False),
        sa.Column("source_ordinal", sa.Integer(), nullable=False),
        sa.Column("schema_version_id", sa.UUID(), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        _fk(table, "import_job_id", "import_job"),
        _fk(table, "domain_projection_id", "domain_projection"),
        _fk(table, "canonical_record_id", "canonical_record"),
        _fk(table, "source_record_id", "source_record"),
        _fk(table, "schema_version_id", "schema_version"),
        sa.UniqueConstraint("canonical_record_id", name=op.f(f"uq_{table}_canonical_record_id")),
        sa.CheckConstraint(
            "payload_sha256 ~ '^[0-9a-f]{64}$'", name=op.f(f"ck_{table}_payload_sha256")
        ),
        sa.CheckConstraint("source_ordinal >= 1", name=op.f(f"ck_{table}_source_ordinal")),
        sa.CheckConstraint("version >= 1", name=op.f(f"ck_{table}_version")),
    ]


def _indexes() -> list[tuple[str, str, list[str]]]:
    """(index, table, columns), as the models declare them."""
    found = [(f"ix_{t}_tenant_id", t, ["tenant_id"]) for t in APPEND_ONLY_TABLES]
    found += [
        ("ix_counterparty_version_import_job_id", "counterparty_version", ["import_job_id"]),
        ("ix_counterparty_version_counterparty_id", "counterparty_version", ["counterparty_id"]),
        ("ix_credit_exposure_version_import_job_id", "credit_exposure_version", ["import_job_id"]),
        (
            "ix_credit_exposure_version_credit_exposure_id",
            "credit_exposure_version",
            ["credit_exposure_id"],
        ),
        (
            "ix_credit_exposure_version_counterparty_key",
            "credit_exposure_version",
            ["counterparty_key"],
        ),
    ]
    return found


credit_vocabulary = sa.table(
    "credit_vocabulary",
    sa.column("id", sa.UUID()),
    sa.column("tenant_id", sa.UUID()),
    sa.column("vocabulary", sa.String()),
    sa.column("code", sa.String()),
    sa.column("name", sa.String()),
    sa.column("rank", sa.Integer()),
    sa.column("is_default_grade", sa.Boolean()),
    sa.column("description", sa.String()),
    sa.column("provisional", sa.Boolean()),
    sa.column("created_by", sa.String()),
    sa.column("seeded_by", sa.String()),
)


def _seed_schemas() -> None:
    bind = op.get_bind()
    for code, name, description, fields in SCHEMAS:
        schema_id = bind.execute(
            sa.text(
                "INSERT INTO schema_definition (id, tenant_id, code, name, description, owner,"
                " provisional, created_by, seeded_by) VALUES (gen_random_uuid(),"
                " CAST(:tenant AS uuid), :code, :name, :description, :owner, true, :actor, :tag)"
                " RETURNING id"
            ).bindparams(
                tenant=TENANT,
                code=code,
                name=name,
                description=f"{description} {ILLUSTRATIVE}",
                owner=OWNER,
                actor=ACTOR,
                tag=TAG,
            )
        ).scalar_one()
        # Inserted DRAFT so its fields can be added, then made ACTIVE (as 0007).
        version_id = bind.execute(
            sa.text(
                "INSERT INTO schema_version (id, schema_id, version, status, effective_from,"
                " compatibility, content_sha256, change_summary, created_by, seeded_by)"
                " VALUES (gen_random_uuid(), :schema_id, 1, 'DRAFT', DATE '2026-10-19', NULL,"
                " :sha, 'Initial version (Feature 04).', :actor, :tag) RETURNING id"
            ).bindparams(schema_id=schema_id, sha=contract_sha256(fields), actor=ACTOR, tag=TAG)
        ).scalar_one()
        for position, fld in enumerate(fields, start=1):
            bind.execute(
                sa.text(
                    "INSERT INTO schema_field (id, schema_version_id, position, name,"
                    " data_type, required, unit, precision, scale, classification,"
                    " extension_eligible, description, created_by, seeded_by) VALUES"
                    " (gen_random_uuid(), :version_id, :position, :name, :data_type,"
                    " :required, :unit, :precision, :scale, :classification,"
                    " :extension_eligible, :description, :actor, :tag)"
                ).bindparams(version_id=version_id, position=position, actor=ACTOR, tag=TAG, **fld)
            )
        bind.execute(
            sa.text("UPDATE schema_version SET status = 'ACTIVE' WHERE id = :id").bindparams(
                id=version_id
            )
        )
    for code, dep_type, ref, dep_code, description in DEPENDENCIES:
        bind.execute(
            sa.text(
                "INSERT INTO schema_dependency (id, schema_id, dependent_type, dependent_ref,"
                " dependent_schema_id, description, provisional, created_by, seeded_by)"
                " SELECT gen_random_uuid(), s.id, :dep_type, :ref,"
                " (SELECT d.id FROM schema_definition d WHERE d.tenant_id = s.tenant_id"
                "  AND d.code = :dep_code), :description, true, :actor, :tag"
                " FROM schema_definition s WHERE s.tenant_id = CAST(:tenant AS uuid)"
                " AND s.code = :code"
            ).bindparams(
                dep_type=dep_type,
                ref=ref,
                dep_code=dep_code,
                description=description,
                actor=ACTOR,
                tag=TAG,
                tenant=TENANT,
                code=code,
            )
        )


def upgrade() -> None:
    # (a) the seeded schemas.
    _seed_schemas()
    # (b) D-38, the named D-01 exception: each schema_code CHECK is replaced by a wider one
    # under the same name, in this transaction. No row changes.
    for table in CHECKED:
        name = op.f(f"ck_{table}_schema_code")
        op.drop_constraint(name, table, type_="check")
        wider = _in("schema_code", (*OLD_SCHEMA_CODES, *SCHEMA_CODES))
        op.create_check_constraint(name, table, wider)
    # (c) the credit domain tables.
    for table in IDENTITIES:
        op.create_table(
            table,
            sa.Column("source_system_id", sa.UUID(), nullable=False),
            sa.Column("business_key", sa.String(), nullable=False),
            *_audit_columns(),
            *_tenant(table),
            _fk(table, "source_system_id", "source_system"),
            sa.UniqueConstraint(
                "tenant_id", "source_system_id", "business_key", name=op.f(f"uq_{table}_tenant_id")
            ),
        )
    op.create_table(
        "counterparty_version",
        sa.Column("counterparty_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("counterparty_name", sa.String(), nullable=True),
        sa.Column("segment", sa.String(), nullable=True),
        sa.Column("country", sa.String(length=2), nullable=True),
        *_audit_columns(),
        *_tenant("counterparty_version"),
        *_projected("counterparty_version"),
        _fk("counterparty_version", "counterparty_id", "counterparty"),
        sa.UniqueConstraint(
            "counterparty_id", "version", name=op.f("uq_counterparty_version_counterparty_id")
        ),
        sa.CheckConstraint(
            "country IS NULL OR country ~ '^[A-Z]{2}$'", name=op.f("ck_counterparty_version_country")
        ),
    )
    op.create_table(
        "credit_exposure_version",
        sa.Column("credit_exposure_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("counterparty_key", sa.String(), nullable=True),
        sa.Column("instrument_key", sa.String(), nullable=True),
        sa.Column("as_of_date", sa.Date(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("facility_type", sa.String(), nullable=True),
        sa.Column("drawn_amount", sa.Numeric(), nullable=True),
        sa.Column("undrawn_amount", sa.Numeric(), nullable=True),
        sa.Column("days_past_due", sa.Integer(), nullable=True),
        sa.Column("default_flag", sa.Boolean(), nullable=True),
        sa.Column("origination_date", sa.Date(), nullable=True),
        sa.Column("origination_grade", sa.String(), nullable=True),
        sa.Column("maturity_date", sa.Date(), nullable=True),
        sa.Column("effective_interest_rate", sa.Numeric(), nullable=True),
        sa.Column("collateral_type", sa.String(), nullable=True),
        sa.Column("collateral_value", sa.Numeric(), nullable=True),
        *_audit_columns(),
        *_tenant("credit_exposure_version"),
        *_projected("credit_exposure_version"),
        _fk("credit_exposure_version", "credit_exposure_id", "credit_exposure"),
        sa.UniqueConstraint(
            "credit_exposure_id",
            "version",
            name=op.f("uq_credit_exposure_version_credit_exposure_id"),
        ),
        sa.CheckConstraint(
            "currency IS NULL OR currency ~ '^[A-Z]{3}$'",
            name=op.f("ck_credit_exposure_version_currency"),
        ),
    )
    # (d) the credit vocabulary.
    op.create_table(
        "credit_vocabulary",
        sa.Column("vocabulary", sa.String(length=30), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.Column("is_default_grade", sa.Boolean(), nullable=True),
        sa.Column("description", sa.String(length=1000), nullable=True),
        sa.Column("provisional", sa.Boolean(), nullable=False),
        sa.Column("seeded_by", sa.String(length=100), nullable=True),
        *_audit_columns(),
        *_tenant("credit_vocabulary"),
        sa.UniqueConstraint(
            "tenant_id", "vocabulary", "code", name=op.f("uq_credit_vocabulary_tenant_id")
        ),
        sa.CheckConstraint(
            _in("vocabulary", VOCABULARY_KINDS), name=op.f("ck_credit_vocabulary_vocabulary")
        ),
        sa.CheckConstraint("code ~ '^[A-Z][A-Z0-9_]*$'", name=op.f("ck_credit_vocabulary_code")),
        sa.CheckConstraint(
            "(vocabulary = 'rating_grade') = (rank IS NOT NULL AND is_default_grade IS NOT NULL)"
            " AND (rank IS NULL OR rank >= 1)",
            name=op.f("ck_credit_vocabulary_grade"),
        ),
    )
    for name, table, columns in _indexes():
        op.create_index(op.f(name), table, columns, unique=False)
    # One statement per op.execute: asyncpg cannot run several in one call.
    for function in FUNCTIONS:
        op.execute(function)
    for name, when, table, level, function in TRIGGERS:
        op.execute(
            f"CREATE TRIGGER {name} {when} ON {table} FOR EACH {level} EXECUTE FUNCTION {function}()"
        )
    op.bulk_insert(
        credit_vocabulary,
        [
            {
                "id": uuid.uuid5(NS, f"credit_vocabulary:{TENANT}:{kind}:{code}"),
                "tenant_id": uuid.UUID(TENANT),
                "vocabulary": kind,
                "code": code,
                "name": name,
                "rank": rank,
                "is_default_grade": default,
                "description": VOCABULARY_NOTE,
                "provisional": True,
                "created_by": ACTOR,
                "seeded_by": TAG,
            }
            for kind, code, name, rank, default in VOCABULARY
        ],
    )


def downgrade() -> None:
    # Credit domain rows are kept (D-29): refuse rather than lose them, and refuse while any
    # row carries a new schema code (the narrow CHECKs would not hold, D-38).
    codes = ", ".join(repr(c) for c in SCHEMA_CODES)
    tagged_versions = (
        "SELECT v.id FROM schema_version v JOIN schema_definition d ON d.id = v.schema_id"  # noqa: S608
        f" WHERE d.seeded_by = '{TAG}'"
    )
    tagged_schemas = f"SELECT id FROM schema_definition WHERE seeded_by = '{TAG}'"  # noqa: S608
    checks = " OR ".join(
        [
            *(f"EXISTS (SELECT 1 FROM {t})" for t in (*IDENTITIES, *VERSIONS)),
            f"EXISTS (SELECT 1 FROM domain_projection WHERE schema_code IN ({codes}))",
            f"EXISTS (SELECT 1 FROM dataset_snapshot_member WHERE schema_code IN ({codes}))",
            f"EXISTS (SELECT 1 FROM credit_vocabulary WHERE seeded_by IS DISTINCT FROM '{TAG}')",
            f"EXISTS (SELECT 1 FROM import_job WHERE schema_version_id IN ({tagged_versions}))",
            # Sources, mappings or later versions built on the seeded schemas.
            f"EXISTS (SELECT 1 FROM source_contract WHERE schema_id IN ({tagged_schemas}))",
            f"EXISTS (SELECT 1 FROM field_mapping WHERE schema_id IN ({tagged_schemas}))",
            f"EXISTS (SELECT 1 FROM schema_change_proposal WHERE schema_id IN ({tagged_schemas}))",
            f"EXISTS (SELECT 1 FROM schema_version WHERE schema_id IN ({tagged_schemas})"
            f" AND seeded_by IS DISTINCT FROM '{TAG}')",
        ]
    )
    op.execute(
        "DO $$ BEGIN IF " + checks + " THEN RAISE EXCEPTION 'credit projections, credit domain"
        " rows, or sources, mappings or imports of the credit schemas exist; they are kept"
        " (D-29, D-38), so this downgrade is refused'; END IF; END $$"
    )
    for name, _, table, _, _ in reversed(TRIGGERS):
        op.execute(f"DROP TRIGGER IF EXISTS {name} ON {table}")
    for function in reversed(FUNCTIONS):
        name = function.split("FUNCTION ", 1)[1].split("(", 1)[0]
        op.execute(f"DROP FUNCTION IF EXISTS {name}()")
    for name, table, _ in reversed(_indexes()):
        op.drop_index(op.f(name), table_name=table)
    for table in ("credit_vocabulary", *reversed(VERSIONS), *reversed(IDENTITIES)):
        op.drop_table(table)
    # The narrow CHECKs back (no row carries a new code: refused above otherwise).
    for table in CHECKED:
        name = op.f(f"ck_{table}_schema_code")
        op.drop_constraint(name, table, type_="check")
        op.create_check_constraint(name, table, _in("schema_code", OLD_SCHEMA_CODES))
    # Only the seed rows this migration inserted (D-11), children first. Seeded versions are
    # not DRAFT, so the registry triggers are lifted for this transaction only (as 0007).
    for table, trigger in REGISTRY_TRIGGERS:
        op.execute(f"ALTER TABLE {table} DISABLE TRIGGER {trigger}")
    for table in ("schema_dependency", "schema_field", "schema_version", "schema_definition"):
        op.execute(sa.text(f"DELETE FROM {table} WHERE seeded_by = :tag").bindparams(tag=TAG))  # noqa: S608
    for table, trigger in REGISTRY_TRIGGERS:
        op.execute(f"ALTER TABLE {table} ENABLE TRIGGER {trigger}")
