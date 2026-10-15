import io
import json
import logging

from app.core.logging import REDACTED, JsonFormatter, Sensitive

JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.c2lnbmF0dXJl"


def _capture() -> tuple[logging.Logger, io.StringIO]:
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("test.redaction")
    logger.handlers[:] = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    return logger, buf


def test_tokens_and_secrets_never_reach_logs() -> None:
    logger, buf = _capture()
    logger.info(
        "login attempt with Bearer abc.def.ghi",
        extra={
            "payload": {
                "Authorization": "Bearer xyz-123-token",
                "client_secret": "s3cr3t-value",
                "password": "hunter2",
                "nested": [{"api_key": "AK-999"}],
                "note": f"forwarded {JWT}",
                "account_number": Sensitive("GB00TEST12345"),
                "amount": 125.5,
            }
        },
    )
    out = buf.getvalue()
    for leaked in (
        "abc.def.ghi",
        "xyz-123-token",
        "s3cr3t-value",
        "hunter2",
        "AK-999",
        JWT,
        "GB00TEST12345",
    ):
        assert leaked not in out, leaked
    record = json.loads(out)
    assert record["extra"]["payload"]["amount"] == 125.5  # non-sensitive values kept
    assert record["extra"]["payload"]["client_secret"] == REDACTED


def test_args_are_redacted() -> None:
    logger, buf = _capture()
    logger.info("token is %s", f"Bearer {JWT}")
    assert JWT not in buf.getvalue()


def test_record_is_json_with_correlation_field() -> None:
    logger, buf = _capture()
    logger.info("hello")
    record = json.loads(buf.getvalue())
    assert record["message"] == "hello"
    assert "correlation_id" in record
