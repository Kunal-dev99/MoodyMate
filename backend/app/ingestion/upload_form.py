"""ingestion.upload_form (BE-35.2: split from ingestion.router)

The HTTP side of `POST /imports`: its multipart body, read after authorization so that the
D-16 byte limit is enforced while streaming (`read_upload`): a declared or received size
beyond the limit raises `_TooLarge` (the route answers 413 `payload_too_large`) before the
rest of the body is read; a malformed body is a RequestValidationError (422).
"""

import json
import unicodedata
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from python_multipart.exceptions import MultipartParseError
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from app.ingestion.adapters import Limits
from app.ingestion.constants import UploadRefusal
from app.ingestion.schemas import ImportForm

# Multipart framing (boundaries, part headers) and the small form fields, on top of the
# file's own D-16 byte limit. Also the cap on any non-file field.
FORM_OVERHEAD = 64 * 1024
FILENAME_MAX = 255


class _TooLarge(MultiPartException):
    def __init__(self) -> None:
        super().__init__(UploadRefusal.PAYLOAD_TOO_LARGE)


async def _bounded(stream: AsyncIterator[bytes], limit: int) -> AsyncGenerator[bytes, None]:
    """Pass the body through, refusing as soon as more than `limit` bytes arrived."""
    received = 0
    async for chunk in stream:
        received += len(chunk)
        if received > limit:
            raise _TooLarge()
        yield chunk


def _malformed(message: str, *loc: str) -> RequestValidationError:
    return RequestValidationError([{"loc": ("body", *loc), "msg": message, "type": "value_error"}])


def _filename(name: str | None) -> str | None:
    """The file's base name, bounded and free of control characters."""
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not base:
        return None
    if len(base) > FILENAME_MAX:
        raise _malformed(f"The file name is longer than {FILENAME_MAX} characters.", "file")
    if any(unicodedata.category(c) == "Cc" for c in base):
        raise _malformed("The file name contains control characters.", "file")
    return base


def _form_fields(form: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        k: v for k, v in form.multi_items() if isinstance(v, str) and k in ImportForm.model_fields
    }
    if fields.get("control_totals") in ("", None):
        fields.pop("control_totals", None)
    elif "control_totals" in fields:
        try:
            fields["control_totals"] = json.loads(fields["control_totals"])
        except ValueError:
            raise _malformed("control_totals must be a JSON object.", "control_totals") from None
    if fields.get("expected_row_count") == "":
        fields.pop("expected_row_count")
    return fields


async def read_upload(request: Request, limits: Limits) -> tuple[ImportForm, bytes, str | None]:
    """The validated form, the file's bytes and its name. Raises _TooLarge (413) while
    streaming, or RequestValidationError (422) for a malformed body."""
    body_limit = limits.max_bytes + FORM_OVERHEAD
    if not request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
        raise _malformed("Expected a multipart/form-data body.")
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > body_limit:
        raise _TooLarge()  # refused before any of the body is read
    parser = MultiPartParser(
        request.headers,
        _bounded(request.stream(), body_limit),
        max_files=1,
        max_fields=10,
        max_part_size=FORM_OVERHEAD,
    )
    try:
        form = await parser.parse()
    except _TooLarge:
        raise
    except (MultiPartException, MultipartParseError):
        raise _malformed("The multipart body is malformed.") from None
    try:
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise _malformed("A file is required.", "file")
        filename = _filename(upload.filename)
        try:
            fields = ImportForm.model_validate(_form_fields(form))
        except ValidationError as exc:
            raise RequestValidationError(
                [
                    {"loc": ("body", *e["loc"]), "msg": e["msg"], "type": e["type"]}
                    for e in exc.errors()
                ]
            ) from None
        return fields, await upload.read(), filename
    finally:
        await form.close()
