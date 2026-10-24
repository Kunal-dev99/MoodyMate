# Incident response: signals and first checks

**Scope:** what the platform gives an operator when something goes wrong, and the first
checks for each signal (BE-34.7, D-62).
**Status:** the signals below are built. Severities, on-call, paging, communication and
response targets are the client's to define (placeholders at the end). No error-tracker
vendor is chosen (D-62 §4, a client decision).

## Signals

| Signal | Where | What it says |
| --- | --- | --- |
| Liveness | `GET /api/v1/health/live` (no credentials) | 200 `{"status": "ok"}` means the process answers. It does no I/O. The backend image's HEALTHCHECK uses it. |
| Readiness | `GET /api/v1/health/ready` (no credentials) | 200, or 503 with `checks`: `database` (answers within 2 s), `migrations` (the database is at the code's head), `artifact_store` (writable), `workers` (always `ok` with inline execution). Values are `ok` or `fail` only: no DSN, host or version. Each failing probe logs one `readiness_failed` WARNING naming the failed checks, with no rate limit. |
| Correlation id | `X-Correlation-ID` on every response; `correlation_id` in every error envelope (D-07) | The key to every log line of one request. Ask whoever reports a problem for it. |
| Access log | `app.access` JSON lines on stdout (`ACCESS_LOG_ENABLED`, default on) | One line per request: `correlation_id`, `method`, route template, `status`, `duration_ms`. Never a query, body, header or token (D-62 §3). |
| Application log | JSON lines on stdout | For example `unhandled_exception` (with the correlation id), `readiness_failed`, `error_tracker_dsn_set_without_adapter`. Values are redacted as for API masking (`../RETENTION.md`, "Application logs"). |
| Metrics | Internal listener only (`METRICS_ENABLED`, default off; `GET /metrics` on `METRICS_HOST:METRICS_PORT`) | See below. One set per process ([`MIGRATIONS.md`](MIGRATIONS.md)). |
| Error tracker hook | `report_exception` (`backend/app/core/error_tracker.py`) | See "Error-tracker hook: what it sees". |

### Metric series

| Series | Labels |
| --- | --- |
| `moodymate_http_requests_total` | `method`, `route` (template or `unmatched`), `status` |
| `moodymate_http_request_errors_total` (5xx only) | `method`, `route`, `status` |
| `moodymate_http_request_duration_seconds` | `method`, `route` |
| `moodymate_runs_total`, `moodymate_run_duration_seconds` | `status` (terminal run status) |
| `moodymate_worker_busy`, `moodymate_worker_queue_depth` | none (the queue is always 0 with inline execution) |
| `moodymate_import_failures_total` | `stage` (`upload` or `validation`), `reason_code` |
| `moodymate_db_pool_size`, `_in_use`, `_idle`, `_overflow` | none |
| `process_*` (memory, CPU, open files) | none |

No series carries a tenant, principal, id or value label (D-62 §2). With `METRICS_TOKEN` set,
the listener needs `Authorization: Bearer <token>` and answers 401 otherwise.

### Error-tracker hook: what it sees

- `report_exception(exc, correlation_id)` is called by `CorrelationIdMiddleware` for an
  exception that **escapes a request**: the client gets the 500 `internal_error` envelope,
  and the log has an `unhandled_exception` line with the same correlation id.
- The reporter receives only the correlation id and the exception **class**: never the
  message, the traceback, the request or any value.
- It does **not** see:
  - failures the application handles and answers itself (every error envelope other than
    `internal_error`, for example `artifact_integrity_error`);
  - a calculation that fails inside the engine. The run is recorded as FAILED
    (`_record_failure` in `backend/app/calculations/service.py`), and the request that
    started it does not fail. Watch `moodymate_runs_total{status="FAILED"}` and the run's
    own record instead;
  - refused uploads and imports that end FAILED. Watch `moodymate_import_failures_total`
    and the import's issues instead;
  - anything outside a request, for example startup errors. These are in the process log.
- The default reporter does nothing. No vendor adapter exists, so `ERROR_TRACKER_DSN`
  changes nothing: when it is set, startup logs one value-free warning
  (`error_tracker_dsn_set_without_adapter`). An uncommented empty `ERROR_TRACKER_DSN=` also
  counts as set.

## First checks by symptom

| Symptom | First checks |
| --- | --- |
| Ready 503, `database: fail` | Is the database reachable from the API, and are the credentials current? Then pool pressure: `moodymate_db_pool_in_use` against `moodymate_db_pool_size` and `_overflow`. |
| Ready 503, `migrations: fail` | The database is not at the code's head. Either the one-shot migration has not run (run it), or this instance is an older version than the database ([`MIGRATIONS.md`](MIGRATIONS.md), "Readiness and the migration head"). |
| Ready 503, `artifact_store: fail` | `ARTIFACT_STORE_DIR` is missing, not mounted, or not writable by the API user (uid 10001 in the image). |
| Live fails | The process is not answering. Look at the container's exit status and the last process log lines. |
| `artifact_integrity_error` (500) | A stored object is missing or no longer matches its hash. Do not repair it in place. Run `scripts/verify-artifact-store.sh` against the store, and restore the object from a backup generation that holds it ([`BACKUP_RESTORE.md`](BACKUP_RESTORE.md)). Treat it as an integrity incident. |
| `internal_error` (500) | Find the `unhandled_exception` line by the correlation id. |
| Many 429 `rate_limited` | Per (tenant, principal, route) buckets, per process (D-53). Check which route and principal in the access log. |
| Calculation runs FAILED | The run's record and `moodymate_runs_total{status="FAILED"}`. Not in the error tracker (above). |
| Metrics listener answers 401 | A missing or wrong `METRICS_TOKEN` on the scraper. All failure cases get the same 401 on purpose. |
| Startup stops | The settings are validated at startup and fail closed (D-13): read the first error line. A metrics port that cannot be bound also stops startup. |

## Handling data during an incident

- Production may hold client data (D-14). Do not copy production data, exports or payloads
  into tickets, chats or other environments. Logs and metrics are built to carry no
  classified values, so share those instead.
- Never disable the database triggers to correct data ([`TRIGGERS.md`](TRIGGERS.md)).
  Governed records are corrected by new records or status changes, never by edits (D-01,
  D-02, D-10).
- Rotate a secret (database password, `METRICS_TOKEN`, OIDC client settings) in the secret
  store, then restart the affected service. Secrets never live in images or in this
  repository (D-50 §2).

## Client decisions (placeholders)

| Item | Value |
| --- | --- |
| Severity levels and response targets | `<client>` |
| On-call rota, paging and escalation contacts | `<client>` |
| Error-tracker vendor (D-62 §4) | `<client: not chosen>` |
| Metrics scrape topology and `METRICS_TOKEN` policy | `<client>` |
| Log platform, its access and retention | `<client; see ../RETENTION.md>` |
| Status page and user communication | `<client>` |
| Post-incident review format | `<client>` |
