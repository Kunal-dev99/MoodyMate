"""Phase 29 (BE-29.1, BE-29.2, BE-29.4): the D-52 hashing checks and the hypothesis
properties, as plain functions called from existing test functions (D-26: the suite stays at
49, so nothing here is collected).

- `check_canonical_policy()`: the grep fold (no `json.dumps` feeding `hashlib` outside
  `app/core/canonical.py` and `app/db/migrations/`), golden hex values for an ASCII and a
  non-ASCII payload, canonical == legacy_ascii for ASCII payloads, `Decimal("1.10")` as
  `"1.10"`, float/NaN/other types refused, the legacy business-key hash, and the `HASH_SITES`
  registry (D-52 §3, §5). Properties: serialisation is idempotent and independent of key
  order, and every ASCII payload hashes the same under both policies.
- `check_decimal_properties()`: `parse_decimal_text` round trip and over-scale refused.
- `check_overlay_properties()`: overlay conservation per currency and direction,
  deterministic part ids, no negative part.
- `check_stored_hashes(db)`: every stored source contract, import fingerprint, import manifest
  and validation profile hash in the fixture DB re-verifies under its registered policy.

Hypothesis runs derandomised with a fixed `max_examples` and no example database, so every
run sees the same examples and writes nothing to disk.
"""

import ast
import hashlib
import json
import re
import uuid
from collections.abc import Callable, Mapping
from datetime import date, timedelta
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.calculations.modules import maturity_ladder
from app.calculations.modules.base import CashFlowInput, InstrumentInput, ModuleInputs
from app.core.decimals import exact_context
from app.tests.conftest import sql
from app.tests.scenario_run_kit import CASE1_BUCKETS, DIRECTIONS, _apply, _pins

APP = Path(__file__).resolve().parents[1]
PROPERTY = settings(
    derandomize=True,
    max_examples=60,
    deadline=None,
    database=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
POLICIES = {"CANONICAL", "LEGACY_ASCII", "LEGACY_KEY"}

# Golden values (D-52 §1), computed once from the policy's own definition with the stdlib:
# sha256(json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).
GOLDEN_ASCII = {"b": [1, None, True, "x"], "a": {"z": Decimal("0.10"), "y": "1.10"}, "c": "EUR"}
GOLDEN_ASCII_TEXT = '{"a":{"y":"1.10","z":"0.10"},"b":[1,null,true,"x"],"c":"EUR"}'
GOLDEN_ASCII_SHA = "f050fdc3301e0ac0e0becad56ecff424bf0a0200aa6d970fa300d790946e75df"
GOLDEN_UNICODE = {"name": "Zürich €", "keys": ["é", "日本"], "a": Decimal("1.10")}
GOLDEN_UNICODE_TEXT = '{"a":"1.10","keys":["é","日本"],"name":"Zürich €"}'
GOLDEN_UNICODE_SHA = "ab15aaa9cfca11c998230b23f89a4ba2d7d04529ce76ea493b9a3e0c2175d06c"
# ensure_ascii=True of the same payload (the legacy policy escapes, so the hash differs).
GOLDEN_UNICODE_LEGACY_ASCII_SHA = "a59ca622d23a1e7ebb47569a2bb6e6cff52f6c7067bddfb6624b2ec556c9aee9"
# legacy_key: json.dumps(list, ensure_ascii=False) with the default separators.
GOLDEN_KEY = (["I-1", "Zürich"], "9173bce1fb0b36295ad55777cffcfa59de803bddf6b292851016d4b8e0976b3e")
GOLDEN_ASCII_KEY = (
    ["I-1", "BK-1"],
    "46f31c37da39837ac65ae1212828b620db484f3af7360b909d97416bc86379ee",
)


def _text(serialised: str | bytes) -> str:
    return serialised.decode() if isinstance(serialised, bytes) else serialised


def _value(x: Any) -> str:
    return str(getattr(x, "value", x))


def _sites() -> dict[str, tuple[str, str]]:
    """`HASH_SITES` as {kind: (policy, reason)}, whatever record type the registry uses."""
    from app.core.canonical import HASH_SITES

    out: dict[str, tuple[str, str]] = {}
    registry: Mapping[Any, Any] = HASH_SITES  # the record type may vary; see the branches
    for kind, site in registry.items():
        if isinstance(site, Mapping):
            policy, reason = site["policy"], site["reason"]
        elif hasattr(site, "policy"):
            policy, reason = site.policy, site.reason
        else:
            policy, reason = site
        out[_value(kind)] = (_value(policy), str(reason))
    return out


# --- BE-29.1: the grep fold ---------------------------------------------------------------------


def _json_hash_sites(path: Path) -> list[int]:
    """Lines where a `hashlib` call is fed (directly, or through a local name) by `json.dumps`."""

    def is_dumps(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "dumps"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "json"
        )

    def is_hash(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "hashlib"
        )

    tree = ast.parse(path.read_text(), str(path))
    found: list[int] = []
    scopes = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]
    for scope in [tree, *scopes]:
        dumped: set[str] = set()
        for node in ast.walk(scope):
            if isinstance(node, ast.Assign) and any(is_dumps(n) for n in ast.walk(node.value)):
                dumped |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        for node in ast.walk(scope):
            if not isinstance(node, ast.Call) or not is_hash(node):
                continue
            fed = [n for a in ast.walk(node) for n in [a] if n is not node]
            if any(is_dumps(n) or (isinstance(n, ast.Name) and n.id in dumped) for n in fed):
                found.append(node.lineno)
    return sorted(set(found))


def check_no_json_hash_outside_core() -> None:
    canonical = APP / "core" / "canonical.py"
    allowed = (APP / "db" / "migrations", APP / "tests")  # tests recompute hashes as oracles
    offenders = {
        str(p.relative_to(APP)): lines
        for p in sorted(APP.rglob("*.py"))
        if p != canonical and not any(p.is_relative_to(a) for a in allowed)
        for lines in [_json_hash_sites(p)]
        if lines
    }
    assert offenders == {}, offenders
    assert _json_hash_sites(canonical), "the policy itself hashes json.dumps output"
    # BE-29.2: no code path rewrites a stored hash (D-52 §4): no UPDATE that SETs a *_sha256.
    update = re.compile(r"UPDATE\s+\w+\s+SET\s+[^;\"']*\b\w*sha256\s*=", re.I)
    for p in sorted(APP.rglob("*.py")):
        if not p.is_relative_to(APP / "tests"):
            assert not update.search(p.read_text()), p


# --- BE-29.1 / BE-29.2: the policy, golden values and the registry --------------------------------

json_scalars = (
    st.none()
    | st.booleans()
    | st.integers(min_value=-(10**15), max_value=10**15)
    | st.text(max_size=8)
    | st.decimals(
        allow_nan=False, allow_infinity=False, places=4, max_value=10**12, min_value=-(10**12)
    )
)
json_values = st.recursive(
    json_scalars,
    lambda children: st.lists(children, max_size=4)
    | st.dictionaries(st.text(max_size=6), children, max_size=4),
    max_leaves=12,
)
# U+0000-U+007E: the stdlib escapes DEL (U+007F) under ensure_ascii=True only, so D-52 §2's
# byte identity holds for ASCII text without DEL (pinned in check_canonical_policy).
ascii_text = st.text(alphabet=st.characters(min_codepoint=0, max_codepoint=0x7E), max_size=8)
ascii_values = st.recursive(
    st.none() | st.booleans() | st.integers(-(10**9), 10**9) | ascii_text,
    lambda children: st.lists(children, max_size=4)
    | st.dictionaries(ascii_text, children, max_size=4),
    max_leaves=12,
)


def _reordered(value: Any) -> Any:
    """The same value with every dict's keys inserted in reverse order."""
    if isinstance(value, dict):
        return {k: _reordered(value[k]) for k in reversed(list(value))}
    if isinstance(value, list):
        return [_reordered(v) for v in value]
    return value


def _oracle(value: Any) -> str:
    """D-52 §1 from its definition (Decimals already strings after a JSON round trip)."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def check_canonical_policy() -> None:
    from app.core import canonical as c

    check_no_json_hash_outside_core()

    # Golden values: ASCII and non-ASCII, text and hex (D-52 §1, §6).
    assert _text(c.canonical_json(GOLDEN_ASCII)) == GOLDEN_ASCII_TEXT
    assert c.canonical_sha256(GOLDEN_ASCII) == GOLDEN_ASCII_SHA
    assert _text(c.canonical_json(GOLDEN_UNICODE)) == GOLDEN_UNICODE_TEXT
    assert c.canonical_sha256(GOLDEN_UNICODE) == GOLDEN_UNICODE_SHA
    # Byte identity for ASCII payloads (D-52 §2); the legacy policy escapes non-ASCII.
    ascii_strings = json.loads(GOLDEN_ASCII_TEXT)
    assert (
        c.legacy_ascii_sha256(ascii_strings)
        == GOLDEN_ASCII_SHA
        == c.canonical_sha256(ascii_strings)
    )
    unicode_strings = json.loads(GOLDEN_UNICODE_TEXT)
    assert c.legacy_ascii_sha256(unicode_strings) == GOLDEN_UNICODE_LEGACY_ASCII_SHA
    # The one ASCII exception to D-52 §2: DEL is escaped by ensure_ascii=True only.
    assert _text(c.canonical_json("\x7f")) == '"\x7f"'
    assert c.canonical_sha256("\x7f") != c.legacy_ascii_sha256("\x7f")
    for key, sha in (GOLDEN_KEY, GOLDEN_ASCII_KEY):
        assert c.legacy_key_sha256(key) == sha, key

    # Decimals: exact text, never exponent form, never rounded; floats and NaN refused.
    for value, text in (
        (Decimal("1.10"), '"1.10"'),
        (Decimal("-0.00"), '"-0.00"'),
        (Decimal("1E+2"), '"100"'),
        (Decimal("0.000000000000000000001"), '"0.000000000000000000001"'),
        ({"amount": Decimal("1.10"), "unit": "EUR"}, '{"amount":"1.10","unit":"EUR"}'),
    ):
        assert _text(c.canonical_json(value)) == text, value
    assert c.canonical_sha256(Decimal("1.10")) != c.canonical_sha256(Decimal("1.1"))
    for refused in (
        1.5,
        0.0,
        float("nan"),
        float("inf"),
        Decimal("NaN"),
        Decimal("sNaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
        {"a": [1, {"b": 2.5}]},
        {1: "non-str key"},
        {("t",): "tuple key"},
        {"a": {1, 2}},
        {"d": date(2026, 1, 1)},
        {"u": uuid.UUID(int=1)},
        b"bytes",
        object(),
    ):
        for fn in (c.canonical_json, c.canonical_sha256):
            try:
                fn(refused)
            except (TypeError, ValueError):
                continue
            raise AssertionError(f"{fn.__name__} accepted {refused!r}")

    check_hash_sites()
    _canonical_properties(c)


def _canonical_properties(c: Any) -> None:
    @PROPERTY
    @given(json_values)
    def idempotent_and_order_free(value: Any) -> None:
        text = _text(c.canonical_json(value))
        assert _text(c.canonical_json(json.loads(text))) == text  # idempotent
        assert _text(c.canonical_json(_reordered(value))) == text  # key order is irrelevant
        assert text == _oracle(json.loads(text))
        assert c.canonical_sha256(value) == hashlib.sha256(text.encode()).hexdigest()

    @PROPERTY
    @given(ascii_values)
    def ascii_is_byte_identical(value: Any) -> None:
        assert c.canonical_sha256(value) == c.legacy_ascii_sha256(value)

    idempotent_and_order_free()
    ascii_is_byte_identical()


# Every hash site of the pre-plan findings (DELIVERY_PLAN Feature 06), by kind.
ALWAYS_CANONICAL = {  # ensure_ascii=False, sorted, compact before Phase 29: already canonical
    "rule_parameters", "rule_set", "dataset_snapshot", "calculation_manifest",
    "calculation_result", "calculation_run_request", "scenario_shocks", "scenario_run_request",
    "mapping_version", "source_row", "data_profile", "canonical_payload",
}  # fmt: skip
FORMER_ASCII = {  # the former default ensure_ascii=True (D-52 context)
    "source_contract", "import_request", "import_manifest", "validation_profile_rules",
    "schema_contract",
}  # fmt: skip
BUSINESS_KEYS = {"business_key", "dry_run_business_key"}  # default separators, a list
# BE-29.2: a FORMER_ASCII kind may be CANONICAL only with a fold that shows the API refuses a
# non-ASCII component of its payload with 422 (the class is ASCII-only). Each probe is
# (api, headers) -> response; none is registered because no kind moved in Phase 29.
ASCII_ONLY_PROBES: dict[str, Callable[..., Any]] = {}


def check_hash_sites() -> None:
    from app.core import canonical as c

    sites = _sites()
    assert set(sites) == ALWAYS_CANONICAL | FORMER_ASCII | BUSINESS_KEYS, set(sites) ^ (
        ALWAYS_CANONICAL | FORMER_ASCII | BUSINESS_KEYS
    )
    for kind, (policy, reason) in sites.items():
        assert policy in POLICIES, (kind, policy)
        assert reason.strip(), f"{kind}: a policy needs its reason (D-52 §3)"
    assert {k for k, (p, _) in sites.items() if p == "LEGACY_KEY"} == BUSINESS_KEYS
    assert {k for k in ALWAYS_CANONICAL if sites[k][0] != "CANONICAL"} == set()
    moved = {k for k in FORMER_ASCII if sites[k][0] == "CANONICAL"}
    assert moved <= set(ASCII_ONLY_PROBES), f"moved to CANONICAL with no 422 fold: {moved}"
    assert {k for k in FORMER_ASCII - moved if sites[k][0] != "LEGACY_ASCII"} == set()
    # sha256_for applies the registered policy (a kind cannot drift from it).
    probe = {"name": "Zürich", "n": 1}
    for kind, (policy, _) in sites.items():
        value = ["I-1", "Zürich"] if policy == "LEGACY_KEY" else probe
        hashers: dict[str, Callable[[Any], str]] = {
            "CANONICAL": c.canonical_sha256,
            "LEGACY_ASCII": c.legacy_ascii_sha256,
            "LEGACY_KEY": c.legacy_key_sha256,
        }
        expected = hashers[policy](value)
        assert c.sha256_for(c.HashKind(kind), value) == expected, kind


# --- BE-29.3 / BE-29.4: decimal text ------------------------------------------------------------


# BE-29.3: one definition each (production code; tests and migrations excluded).
ONE_DEFINITION = {
    r"^\s*def parse_decimal_text\(": "core/decimals.py",
    r"^\s*def most_sensitive\(": "core/classification.py",
    r"^\s*async def refuse\(": "core/refusals.py",
    r"^\s*(async )?def advisory_xact_lock\(": "core/locks.py",
    r"pg_advisory_xact_lock": "core/locks.py",
    r"alias=\"Idempotency-Key\"": "core/idempotency.py",
}


def _imports_core_refuse(tree: ast.Module) -> bool:
    return any(
        isinstance(node, ast.ImportFrom)
        and node.module == "app.core.refusals"
        and any(alias.name == "refuse" for alias in node.names)
        for node in ast.walk(tree)
    )


def check_one_definition_each() -> None:
    found: dict[str, list[str]] = {}
    denial_callers: set[str] = set()
    wrappers_without_core: list[str] = []
    for p in sorted(APP.rglob("*.py")):
        if p.is_relative_to(APP / "tests") or p.is_relative_to(APP / "db" / "migrations"):
            continue
        text = p.read_text()
        rel = str(p.relative_to(APP))
        for pattern in ONE_DEFINITION:
            if re.search(pattern, text, re.M):
                found.setdefault(pattern, []).append(rel)
        # Q1/Q11: denials are recorded only through core.refusals; a per-module `_refuse`
        # wrapper is allowed only as a thin delegate to the core `refuse`.
        for line in text.splitlines():
            if "record_denial(" in line and not re.match(r"^\s*async def record_denial\(", line):
                denial_callers.add(rel)
        if re.search(r"^\s*async def _refuse\(", text, re.M) and not _imports_core_refuse(
            ast.parse(text)
        ):
            wrappers_without_core.append(rel)
    assert found == {k: [v] for k, v in ONE_DEFINITION.items()}, found
    assert denial_callers == {"core/refusals.py"}, denial_callers
    assert wrappers_without_core == [], wrappers_without_core


decimal_cases = st.tuples(
    st.integers(min_value=-(10**30) + 1, max_value=10**30 - 1),
    st.integers(min_value=0, max_value=18),
)


def check_decimal_properties() -> None:
    from app.core import decimals as d

    check_one_definition_each()

    def refused(text: object, code: str, profile: Any = d.STRICT, **limits: int) -> None:
        try:
            d.parse_decimal_text(text, profile, **limits)
        except d.DecimalTextError as exc:
            assert (exc.code.value, str(exc)) == (code, code), (text, exc.code)  # value-free
            return
        raise AssertionError(f"accepted {text!r} under {profile.name}")

    @PROPERTY
    @given(decimal_cases, st.integers(min_value=0, max_value=3))
    def round_trip_and_over_scale(case: tuple[int, int], extra: int) -> None:
        units, places = case
        value = Decimal(units).scaleb(-places)
        text = f"{value:.{places}f}"  # trailing zeros kept: "1.10" stays "1.10"
        for profile in (d.STRICT, d.SOURCE):
            parsed = d.parse_decimal_text(text, profile, max_scale=places + extra)
            assert parsed == value and d.decimal_text(parsed) == text, (text, profile.name)
        # One place more than allowed is refused, never rounded.
        refused(text + ("" if places else ".") + "1", "scale_exceeded", max_scale=places)
        refused(text + ("" if places else ".") + "0", "scale_exceeded", max_scale=places)

    round_trip_and_over_scale()
    assert d.decimal_text(d.parse_decimal_text("1.10")) == "1.10"
    assert d.parse_decimal_text("-0") == 0 and d.parse_decimal_text("0.000") == 0
    assert d.parse_decimal_text("1" * 30) == Decimal("1" * 30)
    refused("1" * 31, "too_many_digits")
    refused("0." + "1" * 19, "scale_exceeded")
    refused("12.345", "too_many_digits", max_digits=4)
    # SOURCE keeps today's ingestion leniency; STRICT refuses it.
    for text, value in (("+1.50", Decimal("1.50")), ("007.10", Decimal("7.10")), ("-01", -1)):
        assert d.parse_decimal_text(text, d.SOURCE) == value
        refused(text, "not_decimal_text")
    refused("-1", "not_decimal_text", d.UNSIGNED)
    for bad in (
        "1e3", "1E3", "1,000", "1_000", " 1", "1 ", "1\n", ".5", "5.", "", "-", "+", "NaN",
        "Infinity", "-Infinity", "\u0661\u0662", "\uff11", "0x10", "--1",
    ):  # fmt: skip
        for profile in (d.STRICT, d.SOURCE, d.UNSIGNED):
            refused(bad, "not_decimal_text", profile)
    for not_text in (1.5, 1, Decimal("1.5"), None, b"1"):
        refused(not_text, "not_decimal_text")


# --- BE-29.4: the overlay properties ----------------------------------------------------------

REPORTING = date(2026, 12, 31)
NS = uuid.UUID("29292929-0000-0000-0000-000000000029")
fractions = st.integers(min_value=1, max_value=10_000).map(lambda n: f"{Decimal(n) / 10_000:.4f}")
amounts = st.builds(
    lambda units, places: Decimal(units).scaleb(-places),
    st.integers(min_value=0, max_value=10**12),
    st.integers(min_value=0, max_value=4),
)
flows = st.lists(
    st.tuples(
        st.sampled_from(sorted(DIRECTIONS)),
        st.sampled_from(["INR", "USD"]),
        st.integers(min_value=1, max_value=60),
        amounts,
    ),
    min_size=2,
    max_size=8,
)
shocks = st.lists(
    st.one_of(
        st.builds(
            lambda kind, f, d: {
                "template": "DELAYED_INFLOWS",
                "parameters": {"cashflow_type": kind, "affected_fraction": f, "delay_days": d},
            },
            st.sampled_from(["LOAN", "BOND", "LOAN", "BOND", "DEPOSIT"]),  # DEPOSIT: no match
            fractions,
            st.integers(min_value=1, max_value=60),
        ),
        st.builds(
            lambda f, cur: {
                "template": "DEPOSIT_WITHDRAWAL",
                "parameters": {"cashflow_type": "DEPOSIT", "increase_fraction": f},
                "selector": {"currency": cur},
            },
            fractions,
            st.sampled_from([None, "INR", "USD"]),
        ),
        st.builds(
            lambda kind, f, d: {
                "template": "FUNDING_UNAVAILABLE",
                "parameters": {"affected_fraction": f, "unavailable_days": d},
                "selector": {"instrument_type": kind},
            },
            st.sampled_from(["LOAN", "BOND"]),
            fractions,
            st.integers(min_value=1, max_value=60),
        ),
    ),
    min_size=1,
    max_size=3,
)


def _overlay_inputs(rows: list[tuple[str, str, int, Decimal]]) -> ModuleInputs:
    cash, held = [], {}
    for n, (kind, currency, day, amount) in enumerate(rows):
        key = f"I-{kind}-{n}"
        cash.append(
            CashFlowInput(
                uuid.uuid5(NS, f"cf:{n}"), key, REPORTING + timedelta(days=day), amount, currency,
                "CONTRACTUAL",
            )
        )  # fmt: skip
        held[key] = InstrumentInput(
            uuid.uuid5(NS, f"v:{key}"), uuid.uuid5(NS, f"i:{key}"), key, kind
        )
    return ModuleInputs(REPORTING, tuple(cash), tuple(held.values()))


def _totals(inputs: ModuleInputs) -> dict[tuple[str, str], Decimal]:
    kinds = {i.business_key: i.instrument_type for i in inputs.instruments}
    out: dict[tuple[str, str], Decimal] = {}
    for f in inputs.cash_flows:
        key = (f.currency or "", DIRECTIONS[kinds[f.instrument_key or ""] or ""])
        out[key] = out.get(key, Decimal(0)) + (f.amount or Decimal(0))
    return out


def check_overlay_properties() -> None:
    from app.calculations import scenario_overlay as so

    @PROPERTY
    @given(flows, shocks)
    def overlay_properties(
        rows: list[tuple[str, str, int, Decimal]], raw: list[dict[str, Any]]
    ) -> None:
        inputs = _overlay_inputs(rows)
        overlay = _apply(so, inputs, DIRECTIONS, raw)
        again = _apply(so, inputs, DIRECTIONS, raw)
        # Deterministic part ids: the same inputs give the same parts, and every id is the
        # uuid5 of "<event>/<path>" (the root part keeps its event id); ids are unique.
        assert [(p.part_id, p.path, p.amount) for p in again.parts] == [
            (p.part_id, p.path, p.amount) for p in overlay.parts
        ]
        for p in overlay.parts:
            expected = (
                p.cashflow_event_id
                if p.path == so.ROOT
                else uuid.uuid5(so.PART_NAMESPACE, f"{p.cashflow_event_id}/{p.path}")
            )
            assert p.part_id == expected == p.flow.cashflow_event_id, p
        assert len({p.part_id for p in overlay.parts}) == len(overlay.parts)
        # No negative part, and no stressed flow below zero.
        assert all(p.amount >= 0 for p in overlay.parts)
        assert all((f.amount or 0) >= 0 for f in overlay.inputs.cash_flows)
        # Conservation per currency and direction: timing moves money, only
        # DEPOSIT_WITHDRAWAL adds, and it adds exactly what its applications declare.
        expected_totals = _totals(inputs)
        for a in overlay.applications:
            key = (a.currency, so.TEMPLATE_DIRECTION[a.template].value)
            expected_totals[key] = expected_totals.get(key, Decimal(0)) + a.added_amount
            if a.template is not so.T.DEPOSIT_WITHDRAWAL:
                assert a.added_amount == 0, a
        assert _totals(overlay.inputs) == expected_totals
        # ... and the engine's own SCENARIO_CONSERVATION post-check agrees.
        pins = _pins(CASE1_BUCKETS, DIRECTIONS)
        with localcontext(exact_context()):
            baseline = maturity_ladder.MODULE.run(inputs, pins)
            stressed = maturity_ladder.MODULE.run(overlay.inputs, pins)
            parsed = so.parse_shocks(raw, list(range(1, len(raw) + 1)))
            assert so.conserved(inputs, parsed, overlay, baseline, stressed)
            assert so.overlay_matches(again, overlay)

    overlay_properties()


# --- BE-29.1 / BE-29.2: stored hashes re-verify -------------------------------------------------


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def stored_in(sites: Mapping[Any, Any]) -> dict[str, set[tuple[str, str]]]:
    """{kind: {(table, column)}} from each site's `stored_in` ("not stored" gives none)."""
    out: dict[str, set[tuple[str, str]]] = {}
    for kind, site in sites.items():
        text = site.stored_in
        out[_value(kind)] = (
            set()
            if text == "not stored"
            else {tuple(t.strip().split(".")) for t in text.split(",")}
        )
    return out


def check_stored_hashes(db: str) -> None:
    """BE-29.1 / BE-29.2: every registered table.column exists, and the stored rows of each
    kind re-verify under its registered policy (sha256_for), byte for byte."""
    from app.core.canonical import HASH_SITES, HashKind, sha256_for

    columns = {
        (r["table_name"], r["column_name"])
        for r in sql(
            db,
            "SELECT table_name, column_name FROM information_schema.columns"
            " WHERE table_schema = 'public' AND column_name LIKE '%sha256'",
        )
    }
    places = stored_in(HASH_SITES)
    missing = {k: sorted(v - columns) for k, v in places.items() if v - columns}
    assert missing == {}, f"HASH_SITES names columns the database does not have: {missing}"

    def verify(kind: str, query: str, payload: Callable[[dict[str, Any]], Any]) -> int:
        rows = sql(db, query)
        for row in rows:
            assert sha256_for(HashKind(kind), payload(row)) == row["sha"], (kind, row["id"])
        return len(rows)

    seen = {
        "source_contract": verify(
            "source_contract",
            "SELECT id, schema_id, expected_columns, business_key_columns,"
            " control_total_columns, content_sha256 AS sha FROM source_contract",
            lambda r: {
                "schema_id": str(r["schema_id"]),
                "expected_columns": sorted(
                    (
                        {"name": c["name"], "required": bool(c["required"])}
                        for c in _json(r["expected_columns"])
                    ),
                    key=lambda c: c["name"],
                ),
                "business_key_columns": sorted(_json(r["business_key_columns"])),
                "control_total_columns": sorted(_json(r["control_total_columns"])),
            },
        ),
        "import_request": verify(
            "import_request",
            "SELECT j.id, j.source_system_id, a.sha256 AS file_sha256, j.synthetic_attested,"
            " j.expected_row_count, j.control_totals, j.retry_of_import_id,"
            " j.request_sha256 AS sha FROM import_job j JOIN import_artifact a"
            " ON a.import_job_id = j.id",
            lambda r: {
                "source_id": str(r["source_system_id"]),
                "file_sha256": r["file_sha256"],
                "synthetic_attested": r["synthetic_attested"],
                "expected_row_count": r["expected_row_count"],
                "control_totals": _json(r["control_totals"]) or {},
            }
            | (
                {}
                if r["retry_of_import_id"] is None
                else {"retry_of_import_id": str(r["retry_of_import_id"])}
            ),
        ),
        "import_manifest": verify(
            "import_manifest",
            "SELECT id, manifest, manifest_sha256 AS sha FROM import_manifest",
            lambda r: _json(r["manifest"]),
        ),
        "validation_profile_rules": verify(
            "validation_profile_rules",
            "SELECT id, rules, content_sha256 AS sha FROM validation_profile",
            lambda r: _json(r["rules"]),
        ),
        "canonical_payload": verify(
            "canonical_payload",
            "SELECT id, payload, payload_sha256 AS sha FROM canonical_record",
            lambda r: _json(r["payload"]),
        ),
        "source_row": verify(
            "source_row",
            "SELECT id, raw_values, extra_values, row_sha256 AS sha FROM source_record",
            lambda r: {"values": _json(r["raw_values"]), "extras": _json(r["extra_values"])},
        ),
        "business_key": verify(
            "business_key",
            "SELECT q.id, s.raw_values, c.business_key_columns, q.business_key_sha256 AS sha"
            " FROM quarantine_item q JOIN source_record s ON s.id = q.source_record_id"
            " JOIN import_job j ON j.id = q.import_job_id"
            " JOIN source_contract c ON c.id = j.source_contract_id"
            " WHERE q.business_key_sha256 IS NOT NULL",
            lambda r: [
                (_json(r["raw_values"]).get(col) or "").strip()
                for col in _json(r["business_key_columns"])
            ],
        ),
    }
    # The fixture DB holds rows of every kind checked here (seeds and this test's imports).
    assert all(seen.values()), seen


def check_schema_contract_hashes(db: str) -> None:
    """BE-29.1: every stored schema contract hash re-verifies under its registered policy, the
    service's `contract_sha256`, and the migrations' frozen copies (0007, 0022), unchanged."""
    import importlib.util

    from app.core.canonical import HashKind, sha256_for
    from app.governance.service import CONTRACT_KEYS, FieldSpec, contract_sha256

    frozen = []
    for name in ("0007_schema_seed", "0022_credit_domain"):
        path = APP / "db" / "migrations" / "versions" / f"{name}.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module.CONTRACT_KEYS == CONTRACT_KEYS, name
        frozen.append(module.contract_sha256)
    versions = sql(db, "SELECT id, content_sha256 FROM schema_version")
    assert versions
    for version in versions:
        rows = sql(db, "SELECT * FROM schema_field WHERE schema_version_id = $1", version["id"])
        fields = [{k: r[k] for k in CONTRACT_KEYS} for r in rows]
        ordered = sorted(fields, key=lambda f: f["name"])
        sha = version["content_sha256"]
        assert contract_sha256([FieldSpec.from_row(dict(r)) for r in rows]) == sha, version["id"]
        assert sha256_for(HashKind.SCHEMA_CONTRACT, ordered) == sha, version["id"]
        assert all(copy(fields) == sha for copy in frozen), version["id"]
