"""core.idempotency

The `Idempotency-Key` dependency (BE-29.3, D-21, D-31 §5, D-45). One definition of the
header: required, 1-255 visible ASCII characters (`^[!-~]+$`), unique per request per user.
Each route passes its own description, so its OpenAPI text is its own.

A replay (same key, same request) answers 200 with `Idempotent-Replayed: true` and the
original resource (`mark_replayed`); the same key with a different request is the route's
409 `idempotency_conflict`, decided by the service from the stored request fingerprint
(`app.core.canonical.sha256_for`).
"""

from typing import Any

from fastapi import Header, Response

REPLAYED_HEADER = "Idempotent-Replayed"
KEY_PATTERN = r"^[!-~]+$"
KEY_MAX_LENGTH = 255


def idempotency_key_header(description: str) -> Any:
    """The header parameter, for `Annotated[str, idempotency_key_header("...")]`."""
    return Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=KEY_MAX_LENGTH,
        pattern=KEY_PATTERN,
        description=description,
    )


def mark_replayed(response: Response) -> None:
    """An idempotent replay: 200 (not 201) and `Idempotent-Replayed: true`."""
    response.status_code = 200
    response.headers[REPLAYED_HEADER] = "true"
