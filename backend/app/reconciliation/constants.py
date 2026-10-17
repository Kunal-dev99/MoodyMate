"""reconciliation.constants

Module constants and enums (D-06, D-23). All values are UPPERCASE and every enum column is
CHECK-constrained in the database (migration 0016, which holds frozen copies kept equal by
test_reconciliation).
"""

from enum import StrEnum


class ReconciliationOutcome(StrEnum):
    """D-23 `reconciliation_run.outcome`. MATCHED only when every item is MATCHED."""

    MATCHED = "MATCHED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"  # every gap is explained by held rows or tolerance
    BROKEN = "BROKEN"  # a gap that no held row explains, or a declared figure that differs


class ReconciliationItemStatus(StrEnum):
    """D-23 `reconciliation_item.status`, a function of the variance and the tolerance."""

    MATCHED = "MATCHED"  # variance = 0
    WITHIN_TOLERANCE = "WITHIN_TOLERANCE"  # 0 < |variance| <= tolerance
    VARIANCE = "VARIANCE"  # |variance| > tolerance


class ReconciliationCheck(StrEnum):
    """What one `reconciliation_item` compares (BE-12.2). `variance = source - target`.

    - ROW_COUNT: source rows vs accepted rows; held = quarantined + rejected. Zero tolerance.
    - EXPECTED_ROW_COUNT: the upload's declared `expected_row_count` vs the source rows.
      Zero tolerance.
    - CONTROL_TOTAL: per contract control-total column, the source total of the parseable
      values vs the accepted rows' total; held = the held rows' total.
    - DECLARED_CONTROL_TOTAL: the upload's declared total vs that source total.
    """

    ROW_COUNT = "ROW_COUNT"
    EXPECTED_ROW_COUNT = "EXPECTED_ROW_COUNT"
    CONTROL_TOTAL = "CONTROL_TOTAL"
    DECLARED_CONTROL_TOTAL = "DECLARED_CONTROL_TOTAL"


# Checks that count rows: always zero tolerance, unit "rows", classification public.
COUNT_CHECKS = frozenset({ReconciliationCheck.ROW_COUNT, ReconciliationCheck.EXPECTED_ROW_COUNT})
COUNT_UNIT = "rows"
# Part of the stored run: a change to the checks or the outcome rules is a new version.
RECONCILER_VERSION = 1
