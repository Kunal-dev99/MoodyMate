"""Data classification and masking (BE-3.4, BE-3.5).

* `Classification("restricted")`: marker for a response-model field, used as
  `Annotated[str | MaskedValue, Classification("restricted")]`.
* `mask_model(model, clear)`: returns a copy where every classified field whose code is not
  in `clear` is replaced by `MaskedValue`. `mask_mapping` does the same for free-form dicts
  (e.g. audit details), using a per-key classification map.
* `Classified(value, code)`: wraps a value for logging. Only `LOG_CLEAR` codes print; any
  other (or unknown) code is redacted, using the same codes as API masking.
* `mask_value(value, code, clear)`: the one masking path of a single value (BE-29.3); the
  model and mapping masks use it too.
* `most_sensitive(codes)` (BE-29.3): the one ranking of classification codes. An unknown
  code ranks above every known one (the most restrictive level: no role is cleared for it);
  no code at all is `unclassified`. `storable(code)` is the code a module writes to a
  classification column: a known code as is, anything else `restricted`.

Unknown classification codes are treated as not clear (fail closed). The codes and ranks
are PROVISIONAL (D-11) and seeded by migration 0005.
"""

from collections.abc import Iterable, Mapping
from types import UnionType
from typing import Annotated, Any, Literal, TypeVar, Union, get_args, get_origin

from pydantic import BaseModel, ConfigDict, Field

# Logs have no viewer to check, so only the two lowest classifications may appear in them.
LOG_CLEAR = frozenset({"public", "internal"})
# The classification codes from least to most sensitive (ranks 0..3, migration 0005, D-11).
# app.governance.constants.ClassificationCode is the same order and checks it at import.
SENSITIVITY: tuple[str, ...] = ("public", "internal", "confidential", "restricted")
MOST_RESTRICTIVE_KNOWN = SENSITIVITY[-1]
_RANK = {code: rank for rank, code in enumerate(SENSITIVITY)}


class Classification:
    """Field marker: the classification of a response-model field's value."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code

    def __repr__(self) -> str:
        return f"Classification({self.code!r})"


class MaskedValue(BaseModel):
    """Stands in for a value the caller may not see (FE renders it as 'masked by policy')."""

    model_config = ConfigDict(frozen=True)  # hashable, so it can stand in inside a set

    masked: Literal[True] = True
    classification: str = Field(description="Classification code of the hidden value.")


class Classified:
    """A value tagged with its classification, for logging (see app.core.logging)."""

    __slots__ = ("code", "value")

    def __init__(self, value: object, code: str) -> None:
        self.value = value
        self.code = code


M = TypeVar("M", bound=BaseModel)
V = TypeVar("V")

# Stored audit details written before every key had to be classified (or by hand) are
# masked under this code, which no role is cleared for (fail closed).
UNCLASSIFIED = "unclassified"


def most_sensitive(codes: Iterable[str]) -> str:
    """The most sensitive of these codes. An unknown code ranks above every known one (fail
    closed: no role is cleared for it); between unknown codes the first wins. No code at all
    is `unclassified`."""
    return max(codes, key=lambda c: _RANK.get(c, len(_RANK)), default=UNCLASSIFIED)


def storable(code: str) -> str:
    """The code to store in a classification column (an FK to a real code): a known code as
    is; an unknown one (or `unclassified`) as `restricted`, the most sensitive known code."""
    return code if code in _RANK else MOST_RESTRICTIVE_KNOWN


def mask_value(value: V, code: str, clear: frozenset[str]) -> V | MaskedValue:
    """The value when `code` is cleared for the caller, else a MaskedValue of `code`. An
    unknown code is never in `clear` (fail closed)."""
    return value if code in clear else MaskedValue(classification=code)


def _marker(metadata: list[Any]) -> Classification | None:
    return next((m for m in metadata if isinstance(m, Classification)), None)


def _union_args(annotation: Any) -> tuple[Any, ...] | None:
    return get_args(annotation) if get_origin(annotation) in (Union, UnionType) else None


def _mask(value: Any, annotation: Any, clear: frozenset[str]) -> Any:
    """Mask `value` as declared by `annotation`: markers may sit on the field, inside an
    Optional/Union, or on the element/value type of a list, tuple, set or dict."""
    if get_origin(annotation) is Annotated:
        inner, *metadata = get_args(annotation)
        marker = _marker(metadata)
        if marker is not None and marker.code not in clear:
            # A classified None is masked too, so absence is not revealed.
            return MaskedValue(classification=marker.code)
        return _mask(value, inner, clear)
    union = _union_args(annotation)
    if union is not None:
        for arg in union:
            if get_origin(arg) is Annotated:
                marker = _marker(list(get_args(arg)[1:]))
                if marker is not None and marker.code not in clear:
                    return MaskedValue(classification=marker.code)
        if value is None:
            return None
        # Recurse with the union arm that matches the value's container shape, if any.
        for arg in union:
            if arg is type(None):
                continue
            masked = _mask(value, arg, clear)
            if masked is not value:
                return masked
        return _mask_by_value(value, clear)
    if isinstance(value, BaseModel):
        return mask_model(value, clear)
    origin, args = get_origin(annotation), get_args(annotation)
    if isinstance(value, dict):
        value_type = args[1] if origin is dict and len(args) == 2 else Any
        return {k: _mask(v, value_type, clear) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        if origin is tuple and args and args[-1] is not Ellipsis:
            types = list(args)  # fixed-length tuple: one type per position
        else:
            types = [args[0] if args else Any] * len(value)
        items = [_mask(v, t, clear) for v, t in zip(value, types, strict=False)]
        if hasattr(type(value), "_fields"):  # NamedTuple
            return type(value)(*items)
        return type(value)(items)
    return value


def _mask_by_value(value: Any, clear: frozenset[str]) -> Any:
    return _mask(value, Any, clear)


def mask_model(model: M, clear: frozenset[str]) -> M:
    """Copy of `model` with every classified value the caller may not see replaced.

    A classified `None` is masked as well, so the response does not reveal whether the
    hidden value exists (same rule as `mask_mapping`).
    """
    updates: dict[str, Any] = {}
    for name, field in type(model).model_fields.items():
        value = getattr(model, name)
        marker = _marker(field.metadata)
        if marker is not None and marker.code not in clear:
            updates[name] = MaskedValue(classification=marker.code)
            continue
        masked = _mask(value, field.annotation, clear)
        if masked != value or type(masked) is not type(value):
            updates[name] = masked
    return model.model_copy(update=updates) if updates else model


def mask_mapping(
    values: Mapping[str, Any], classes: Mapping[str, str], clear: frozenset[str]
) -> dict[str, Any]:
    """Mask the keys of a free-form mapping whose classification is not clear.

    A key with no classification is masked as `unclassified` (fail closed).
    """
    return {
        key: mask_value(value, classes.get(key, UNCLASSIFIED), clear)
        for key, value in values.items()
    }
