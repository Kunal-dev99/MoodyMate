"""rules.constants

The rule, module and assumption registry's enums, transition table and ILLUSTRATIVE seed
(D-06, D-11, D-32, D-35). Every enum column is CHECK-constrained in the database (migration
0018, which holds frozen copies kept equal by test_calculations).

Parameters are typed data from a closed list of kinds (D-32 §1): no expressions, formulas,
templates or user code. A decimal parameter is a JSON string of exact decimal text, never a
JSON number.
"""

from enum import StrEnum
from typing import Any


class ImplementationStatus(StrEnum):
    """`module_version.implementation_status`: only AVAILABLE versions can run (D-31 §1).
    PLANNED versions are declared extension points (Phase 18) and never run."""

    AVAILABLE = "AVAILABLE"
    PLANNED = "PLANNED"


class RuleVersionStatus(StrEnum):
    """Data Model §9 `rule_version.status` plus REJECTED (D-06, D-32 §2). Also the status set
    of `assumption_set` versions."""

    DRAFT = "DRAFT"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"  # reserved: an approval activates in the same transaction (D-32 §2)
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    RETIRED = "RETIRED"
    REJECTED = "REJECTED"


# D-06 transition table (D-32 §2), mirrored by the DB lifecycle trigger: a version is edited
# only while DRAFT; once it leaves DRAFT only its status moves, along these edges, and the
# approval columns change only on the move out of IN_REVIEW.
RULE_VERSION_TRANSITIONS: dict[RuleVersionStatus, frozenset[RuleVersionStatus]] = {
    RuleVersionStatus.DRAFT: frozenset({RuleVersionStatus.IN_REVIEW}),
    RuleVersionStatus.IN_REVIEW: frozenset({RuleVersionStatus.ACTIVE, RuleVersionStatus.REJECTED}),
    RuleVersionStatus.APPROVED: frozenset(),
    RuleVersionStatus.ACTIVE: frozenset({RuleVersionStatus.SUPERSEDED, RuleVersionStatus.RETIRED}),
    RuleVersionStatus.SUPERSEDED: frozenset(),
    RuleVersionStatus.RETIRED: frozenset(),
    RuleVersionStatus.REJECTED: frozenset(),
}


class RuleCategory(StrEnum):
    """What a rule decides (`rule_definition.category`)."""

    BUCKETING = "BUCKETING"  # time buckets (DAY_BUCKETS)
    CLASSIFICATION = "CLASSIFICATION"  # a vocabulary mapped to classes (e.g. direction)
    TREATMENT = "TREATMENT"  # how an edge case is treated (e.g. past-due cash flows)
    FACTOR = "FACTOR"  # rates, haircuts and caps (Phase 17)


class ParameterKind(StrEnum):
    """The closed list of parameter kinds (D-32 §1). Adding one needs code, tests and an
    appended decision. The full per-kind checks of proposed versions land with BE-16.1."""

    DECIMAL = "DECIMAL"
    INTEGER = "INTEGER"
    ENUM = "ENUM"
    DAY_BUCKETS = "DAY_BUCKETS"
    ENUM_MAP = "ENUM_MAP"
    DECIMAL_MAP = "DECIMAL_MAP"


class CashflowDirection(StrEnum):
    """The values of the `cashflow_direction` ENUM_MAP (D-35 §1)."""

    INFLOW = "INFLOW"
    OUTFLOW = "OUTFLOW"


class PastDueTreatment(StrEnum):
    """The values of the `past_due_treatment` ENUM. D-35 §1 seeds EXCLUDE_WITH_WARNING."""

    EXCLUDE_WITH_WARNING = "EXCLUDE_WITH_WARNING"
    INCLUDE_IN_FIRST_BUCKET = "INCLUDE_IN_FIRST_BUCKET"


# D-35 §3: every seeded rule version and module, and every result, carries this statement.
ILLUSTRATIVE_STATEMENT = (
    "Illustrative prototype. Not a regulatory calculation; no parity with any vendor system "
    "is claimed."
)
RULES_SEED_TAG = "0018_calculations"
# The assumption set a run pins in Feature 03 (D-32 §6): its ACTIVE version.
BASELINE_ASSUMPTION_SET = "baseline"

# --- the ILLUSTRATIVE / PROVISIONAL seed (D-11, D-35); frozen copies in migration 0018 -------

MATURITY_LADDER = "maturity_ladder"
MATURITY_BUCKETS = "maturity_buckets"
CASHFLOW_DIRECTION = "cashflow_direction"
PAST_DUE_TREATMENT = "past_due_treatment"


def _bucket(code: str, label: str, lower: int, upper: int | None) -> dict[str, Any]:
    return {"code": code, "label": label, "lower_exclusive": lower, "upper_inclusive": upper}


# D-35 §1: calendar days after the reporting date, half-open (lower, upper], the last open.
SEEDED_BUCKETS: list[dict[str, Any]] = [
    _bucket("B01", "(0,1]", 0, 1),
    _bucket("B02", "(1,7]", 1, 7),
    _bucket("B03", "(7,30]", 7, 30),
    _bucket("B04", "(30,90]", 30, 90),
    _bucket("B05", "(90,180]", 90, 180),
    _bucket("B06", "(180,365]", 180, 365),
    _bucket("B07", "(365,730]", 365, 730),
    _bucket("B08", "(730,inf)", 730, None),
]

# code -> (name, category, parameter_schema, parameters, description)
SEEDED_RULES: dict[str, tuple[str, RuleCategory, dict[str, Any], dict[str, Any], str]] = {
    MATURITY_BUCKETS: (
        "Maturity buckets (illustrative)",
        RuleCategory.BUCKETING,
        {"kind": ParameterKind.DAY_BUCKETS.value, "unit": "calendar days after reporting date"},
        {"buckets": SEEDED_BUCKETS},
        "Illustrative day intervals (0,1], (1,7], (7,30], (30,90], (90,180], (180,365], "
        "(365,730] and (730,inf), in calendar days after the reporting date (D-35).",
    ),
    CASHFLOW_DIRECTION: (
        "Cash-flow direction by instrument type (illustrative)",
        RuleCategory.CLASSIFICATION,
        {
            "kind": ParameterKind.ENUM_MAP.value,
            "key_vocabulary": "instrument_type",
            "values": [d.value for d in CashflowDirection],
        },
        {
            "map": {
                "BOND": CashflowDirection.INFLOW.value,
                "DEPOSIT": CashflowDirection.OUTFLOW.value,
                "LOAN": CashflowDirection.INFLOW.value,
            }
        },
        "LOAN and BOND are inflows and DEPOSIT is an outflow: an illustrative asset/liability "
        "assumption, because cashflow_event v1 has no direction (D-35).",
    ),
    PAST_DUE_TREATMENT: (
        "Past-due cash-flow treatment (illustrative)",
        RuleCategory.TREATMENT,
        {"kind": ParameterKind.ENUM.value, "values": [t.value for t in PastDueTreatment]},
        {"value": PastDueTreatment.EXCLUDE_WITH_WARNING.value},
        "A cash flow due before the reporting date is excluded from the ladder with a "
        "warning (D-35).",
    ),
}
SEEDED_RULE_OWNER = "Methodology owner (risk_analyst), provisional"
SEEDED_ASSUMPTION_SET_NAME = "baseline (illustrative)"
