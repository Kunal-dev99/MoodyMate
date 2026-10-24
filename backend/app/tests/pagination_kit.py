"""Phase 33 (BE-33.5, D-59 §6): every capped list says when it is truncated. One loop over the
inventory (every GET in backend/openapi.json that takes `cursor`): with 3 or more rows,
`limit=2` gives has_more and a next_cursor, and the cursor gives the 3rd row (the row at
offset 2); the last page has neither. A tampered cursor, another list's cursor and a cursor
sent with other filters are 400 invalid_cursor. Folded into test_calculations part 5 (+0)."""

import json
import re
from collections.abc import Mapping
from functools import partial
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

OPENAPI = Path(__file__).resolve().parents[2] / "openapi.json"
# Every page has these; `limit` and `offset` only where its schema declares them (ProposalList
# has neither).
PAGE_KEYS = {"items", "total", "has_more", "next_cursor"}
WINDOW_KEYS = {"limit", "offset"}


def inventory() -> dict[str, tuple[str, tuple[str, ...], frozenset[str]]]:
    """operationId -> (path template, required query names, the page schema's properties),
    for every capped list (a GET taking `cursor`)."""
    spec = json.loads(OPENAPI.read_text())
    schemas = spec["components"]["schemas"]

    def page_keys(op: Any) -> frozenset[str]:
        ref = op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        return frozenset(schemas[ref.rsplit("/", 1)[-1]]["properties"])

    return {
        op["operationId"]: (
            path,
            tuple(p["name"] for p in op["parameters"] if p["in"] == "query" and p.get("required")),
            page_keys(op),
        )
        for path, ops in spec["paths"].items()
        for method, op in ops.items()
        if method == "get" and any(p["name"] == "cursor" for p in op.get("parameters", []))
    }


def invalid(r: Any, label: str) -> None:
    assert r.status_code == 400, (label, r.status_code, r.text)
    assert r.json()["code"] == "invalid_cursor", (label, r.text)


def tampered(cursor: str) -> str:
    i = len(cursor) // 2
    return cursor[:i] + ("A" if cursor[i] != "A" else "B") + cursor[i + 1 :]


def _fetch(
    api: TestClient,
    url: str,
    headers: dict[str, str],
    fixed: Mapping[str, str],
    params: dict[str, str],
) -> Any:
    return api.get(url, params={**fixed, **params}, headers=headers)


def check_cursor_pages(
    api: TestClient,
    headers: Mapping[str, dict[str, str]],
    path_params: dict[str, str],
    filters: Mapping[str, dict[str, str]] | None = None,
    fixed: Mapping[str, dict[str, str]] | None = None,
) -> set[str]:
    """Loops over the inventory; returns the lists checked with 3+ rows. `headers` maps an
    operationId to its caller's headers ("*": every other list); `path_params` fills path
    templates and required query parameters; `fixed` (operationId -> query parameters) goes
    on every request of that list; `filters` (operationId -> a filter the list accepts) adds
    the other-filters refusal."""
    covered: set[str] = set()
    cursors: dict[str, str] = {}
    urls: dict[str, str] = {}
    for op, (template, required, declared) in inventory().items():
        names = re.findall(r"{(\w+)}", template)
        if any(n not in path_params for n in (*names, *required)):
            continue
        url = template.format(**path_params)
        if required:
            url += "?" + "&".join(f"{n}={path_params[n]}" for n in required)
        urls[op] = url
        get = partial(_fetch, api, url, headers.get(op, headers["*"]), (fixed or {}).get(op, {}))
        first = get({"limit": "2"})
        assert first.status_code == 200, (op, first.text)
        page = first.json()
        assert PAGE_KEYS <= declared and PAGE_KEYS <= set(page), (op, sorted(page))
        assert set(page) & WINDOW_KEYS == declared & WINDOW_KEYS, (op, sorted(page))
        if "limit" in declared:
            assert page["limit"] == 2, (op, page["limit"])
        total = page["total"]
        whole = get({"limit": str(max(total, 1))}).json()
        assert (whole["has_more"], whole["next_cursor"]) == (False, None), (
            op,
            total,
            whole["total"],
        )
        if total < 3:
            assert page["has_more"] is (total > 2), op
            continue
        assert page["has_more"] is True and isinstance(page["next_cursor"], str), (op, page)
        cursor = page["next_cursor"]
        nxt = get({"limit": "2", "cursor": cursor})
        assert nxt.status_code == 200, (op, nxt.text)
        third = get({"limit": "1", "offset": "2"}).json()
        assert nxt.json()["items"][0] == third["items"][0] == whole["items"][2], op
        assert nxt.json()["has_more"] is (total > 4), op
        invalid(get({"limit": "2", "cursor": tampered(cursor)}), op)
        if op in (filters or {}):
            mismatched = {"limit": "2", "cursor": cursor, **(filters or {})[op]}
            invalid(get(mismatched), f"{op} other filters")
        cursors[op] = cursor
        covered.add(op)
    # Another list's cursor is refused (the cursor is bound to its list).
    ops = sorted(cursors)
    for op, next_op in zip(ops, ops[1:] + ops[:1], strict=True):
        if op != next_op:
            caller, extra = headers.get(op, headers["*"]), (fixed or {}).get(op, {})
            invalid(_fetch(api, urls[op], caller, extra, {"cursor": cursors[next_op]}), op)
    return covered


def check_foreign_cursor(
    api: TestClient, own: dict[str, str], foreign: dict[str, str], url: str
) -> None:
    """A cursor issued to one tenant is refused for another (tenant-bound)."""
    page = api.get(url, params={"limit": "1"}, headers=own).json()
    assert page["has_more"] and page["next_cursor"], page
    invalid(api.get(url, params={"cursor": page["next_cursor"]}, headers=foreign), url)
