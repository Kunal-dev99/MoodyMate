"""Data classification and masking (BE-3.4, BE-3.5).

* `Classification("restricted")`: marker for a response-model field, used as
  `Annotated[str | MaskedValue, Classification("restricted")]`.
* `mask_model(model, clear)`: returns a copy where every classified field whose code is not
  in `clear` is replaced by `MaskedValue`. `mask_mapping` does the same for free-form dicts
  (e.g. audit details), using a per-key classification map.
* `Classified(value, code)`: wraps a value for logging. Only `LOG_CLEAR` codes print; any
  other (or unknown) code is redacted, using the same codes as API masking.

Unknown classification codes are treated as not clear (fail closed). The codes and ranks
are PROVISIONAL (D-11) and seeded by migration 0005.
"""

from collections.abc import Mapping
from typing import Annotated, Any, Literal, TypeVar, get_args, get_origin

from pydantic import BaseModel, Field

# Logs have no viewer to check, so only the two lowest classifications may appear in them.
LOG_CLEAR = frozenset({"public", "internal"})


class Classification:
    """Field marker: the classification of a response-model field's value."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code

    def __repr__(self) -> str:
        return f"Classification({self.code!r})"


class MaskedValue(BaseModel):
    """Stands in for a value the caller may not see (FE renders it as 'masked by policy')."""

    masked: Literal[True] = True
    classification: str = Field(description="Classification code of the hidden value.")


class Classified:
    """A value tagged with its classification, for logging (see app.core.logging)."""

    __slots__ = ("code", "value")

    def __init__(self, value: object, code: str) -> None:
        self.value = value
        self.code = code


M = TypeVar("M", bound=BaseModel)

# Stored audit details written before every key had to be classified (or by hand) are
# masked under this code, which no role is cleared for (fail closed).
UNCLASSIFIED = "unclassified"


def _marker(metadata: list[Any]) -> Classification | None:
    return next((m for m in metadata if isinstance(m, Classification)), None)


def _element_marker(annotation: Any) -> Classification | None:
    """Marker on the element type of `list[Annotated[..., Classification(...)]]`."""
    if get_origin(annotation) in (list, tuple, set, frozenset):
        for arg in get_args(annotation):
            if get_origin(arg) is Annotated:
                marker = _marker(list(get_args(arg)[1:]))
                if marker is not None:
                    return marker
    return None


def _mask_nested(value: Any, clear: frozenset[str]) -> Any:
    """Walk containers so models nested anywhere in a field are masked too."""
    if isinstance(value, BaseModel):
        return mask_model(value, clear)
    if isinstance(value, list | tuple):
        return type(value)(_mask_nested(v, clear) for v in value)
    if isinstance(value, dict):
        return {k: _mask_nested(v, clear) for k, v in value.items()}
    return value


def mask_model(model: M, clear: frozenset[str]) -> M:
    """Copy of `model` with every classified value the caller may not see replaced.

    A classified `None` is masked as well, so the response does not reveal whether the
    hidden value exists (same rule as `mask_mapping`).
    """
    updates: dict[str, Any] = {}
    for name, field in type(model).model_fields.items():
        value = getattr(model, name)
        marker = _marker(field.metadata)
        element = _element_marker(field.annotation)
        if marker is not None and marker.code not in clear:
            updates[name] = MaskedValue(classification=marker.code)
        elif element is not None and element.code not in clear and isinstance(value, list):
            updates[name] = [MaskedValue(classification=element.code) for _ in value]
        elif isinstance(value, BaseModel | list | tuple | dict):
            masked = _mask_nested(value, clear)
            if masked != value:
                updates[name] = masked
    return model.model_copy(update=updates) if updates else model


def mask_mapping(
    values: Mapping[str, Any], classes: Mapping[str, str], clear: frozenset[str]
) -> dict[str, Any]:
    """Mask the keys of a free-form mapping whose classification is not clear.

    A key with no classification is masked as `unclassified` (fail closed).
    """
    return {
        key: value
        if classes.get(key, UNCLASSIFIED) in clear
        else MaskedValue(classification=classes.get(key, UNCLASSIFIED))
        for key, value in values.items()
    }
