"""Error-tracker hook (BE-34.6, D-62 §4).

`report_exception(exc, correlation_id)` is called for every unhandled exception (by
`CorrelationIdMiddleware`). It hands a reporter only the correlation id and the exception
*class*: never the message, the traceback, the request or any value, which could carry
classified data (D-50 §3).

The default is a no-op. No vendor SDK is installed or imported: choosing the vendor is a client
hard stop (D-62 §4). `ERROR_TRACKER_DSN` is the setting the vendor adapter will read; while
no adapter is installed, setting it only logs (once, value-free) that reports are dropped.
A vendor adapter, once chosen, is installed with `set_reporter`.

A reporter must never break the request that failed: anything it raises is swallowed.
"""

import logging
from collections.abc import Callable

logger = logging.getLogger("app.error_tracker")

# (correlation_id, exception class) -> None
Reporter = Callable[[str | None, type[BaseException]], None]


def _no_op(correlation_id: str | None, exc_class: type[BaseException]) -> None:
    return None


_reporter: Reporter = _no_op


def set_reporter(reporter: Reporter | None) -> Reporter:
    """Install a reporter (None restores the no-op); returns the one it replaces."""
    global _reporter
    previous, _reporter = _reporter, reporter or _no_op
    return previous


def configure_error_tracker(dsn_configured: bool) -> None:
    """At startup: no adapter exists yet, so a configured DSN changes nothing but is noted."""
    if dsn_configured and _reporter is _no_op:
        logger.warning("error_tracker_dsn_set_without_adapter")


def report_exception(exc: BaseException, correlation_id: str | None) -> None:
    try:
        _reporter(correlation_id, type(exc))
    except Exception:  # noqa: BLE001 - the tracker never breaks the failing request
        logger.warning("error_tracker_report_failed")
