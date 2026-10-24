"""core.pagination

Explicit truncation for every capped list (BE-33.5, D-59 §6). Additive to the existing
`limit`/`offset`/`total` pages:

- each page adds `has_more` (more rows exist after this page) and `next_cursor` (an opaque
  continuation for the next page, or null on the last page);
- each capped list route accepts `cursor`. A cursor replaces `offset`: it is bound to the
  caller's tenant, to the list and to the list's filters, so a foreign, tampered or
  mismatched cursor is 400 `invalid_cursor` (nothing read, no value echoed).

Routers call `page_offset` to resolve the offset to read from and `continuation` for the two
page fields. Scope still comes from the principal (D-04): a cursor never widens it.

The cursor is `<payload>.<mac>`, both base64url without padding. The payload holds only a
format version and the next offset, so it reveals nothing. The mac is an HMAC-SHA256, under
CURSOR_SIGNING_KEY, of the version, the caller's tenant, the list (its operationId), the
canonical JSON of its filters and the offset. A cursor of another tenant, list or filter set,
or with any byte changed, fails the mac: there is nothing to decode before it is verified.
The key is set once per app (`configure`); local/test fall back to a development key (D-13).
"""

import base64
import binascii
import hashlib
import hmac
import json
from collections.abc import Mapping
from enum import Enum
from types import MappingProxyType
from typing import Annotated, Any

from fastapi import Query
from pydantic import Field

from app.core.config import DEV_CURSOR_KEY_DEFAULT
from app.core.errors import ApiError, ErrorEnvelope
from app.core.principal import Principal

INVALID_CURSOR = "invalid_cursor"
# The filters of an unfiltered list (still bound into its cursor).
NO_FILTERS: Mapping[str, Any] = MappingProxyType({})
CURSOR_MAX_LENGTH = 512

Cursor = Annotated[
    str | None,
    Query(
        min_length=1,
        max_length=CURSOR_MAX_LENGTH,
        pattern=r"^[A-Za-z0-9_\-.]+$",
        description="Opaque continuation: a previous page's `next_cursor`, sent with the same "
        "filters. It replaces `offset`. A cursor of another tenant, another list or other "
        "filters, or a tampered one, is 400 invalid_cursor (D-59 §6).",
    ),
]

HasMore = Annotated[
    bool,
    Field(description="True when rows exist after this page: the list is truncated (D-59 §6)."),
]

NextCursor = Annotated[
    str | None,
    Field(
        description="Opaque cursor for the next page (pass it as `cursor`), bound to the "
        "caller's tenant, this list and its filters; null on the last page (D-59 §6)."
    ),
]

INVALID_CURSOR_RESPONSE: dict[int | str, dict[str, Any]] = {
    400: {
        "model": ErrorEnvelope,
        "description": "invalid_cursor: the cursor is malformed, tampered, of another tenant, "
        "or of another list or filter set (D-59 §6). Nothing is read.",
    }
}


CURSOR_VERSION = 1
# The largest offset a cursor may carry (far above any real list; bounds the decoded int).
MAX_CURSOR_OFFSET = 10**12

_key: bytes = DEV_CURSOR_KEY_DEFAULT.encode()


def configure(key: bytes) -> None:
    """Set the signing key (create_app, from Settings.cursor_signing_key)."""
    global _key
    _key = key


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _filter_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, list | tuple | frozenset | set):
        return [_filter_value(v) for v in value]
    if value is None or isinstance(value, bool | int | str):
        return value
    return str(value)  # UUID, date, Decimal: their canonical text


def _mac(principal: Principal, listing: str, filters: Mapping[str, Any], offset: int) -> bytes:
    bound = json.dumps(
        [
            CURSOR_VERSION,
            str(principal.tenant_id),
            listing,
            {k: _filter_value(v) for k, v in filters.items()},
            offset,
        ],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hmac.new(_key, bound.encode("ascii"), hashlib.sha256).digest()


def encode_cursor(
    principal: Principal, listing: str, filters: Mapping[str, Any], offset: int
) -> str:
    """The opaque cursor that resumes `listing` (with `filters`) at `offset` for the caller's
    tenant."""
    payload = json.dumps({"o": offset, "v": CURSOR_VERSION}, separators=(",", ":"))
    return f"{_b64(payload.encode('ascii'))}.{_b64(_mac(principal, listing, filters, offset))}"


def _invalid() -> ApiError:
    # No detail: which check failed, or what the cursor held, is not echoed.
    return ApiError(
        400,
        INVALID_CURSOR,
        "The cursor is not valid for this list: pass a next_cursor this list returned, with "
        "the same filters.",
    )


def decode_cursor(
    principal: Principal, listing: str, filters: Mapping[str, Any], cursor: str
) -> int:
    """The offset a cursor carries, once its mac verifies for this tenant, list and filter
    set; otherwise 400 invalid_cursor."""
    try:
        payload_text, mac_text = cursor.split(".")
        payload = json.loads(_unb64(payload_text).decode("ascii"))
        mac = _unb64(mac_text)
    except (ValueError, binascii.Error, UnicodeDecodeError):
        raise _invalid() from None
    if not isinstance(payload, dict) or set(payload) != {"o", "v"}:
        raise _invalid()
    offset, version = payload["o"], payload["v"]
    if (
        version != CURSOR_VERSION
        or type(offset) is not int
        or not 0 <= offset <= MAX_CURSOR_OFFSET
        or not hmac.compare_digest(mac, _mac(principal, listing, filters, offset))
    ):
        raise _invalid()
    return offset


def page_offset(
    principal: Principal,
    listing: str,
    filters: Mapping[str, Any],
    offset: int,
    cursor: str | None,
) -> int:
    """The offset to read from: `offset`, or the one the cursor carries once verified (the
    cursor replaces `offset`)."""
    if cursor is None:
        return offset
    return decode_cursor(principal, listing, filters, cursor)


def continuation(
    principal: Principal,
    listing: str,
    filters: Mapping[str, Any],
    limit: int,
    offset: int,
    returned: int,
    total: int,
) -> dict[str, Any]:
    """The page's `has_more` and `next_cursor` (for `**continuation(...)`): the cursor
    resumes after the rows returned, and is null on the last page."""
    has_more = returned > 0 and offset + returned < total
    next_cursor = (
        encode_cursor(principal, listing, filters, offset + returned) if has_more else None
    )
    return {"has_more": has_more, "next_cursor": next_cursor}
