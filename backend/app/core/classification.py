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
from typing import Any, Literal, TypeVar

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


def _marker(metadata: list[Any]) -> Classification | None:
    return next((m for m in metadata if isinstance(m, Classification)), None)


def mask_model(model: M, clear: frozenset[str]) -> M:
    updates: dict[str, Any] = {}
    for name, field in type(model).model_fields.items():
        value = getattr(model, name)
        marker = _marker(field.metadata)
        if marker is not None and value is not None and marker.code not in clear:
            updates[name] = MaskedValue(classification=marker.code)
        elif isinstance(value, BaseModel):
            updates[name] = mask_model(value, clear)
        elif isinstance(value, list) and value and isinstance(value[0], BaseModel):
            updates[name] = [mask_model(v, clear) for v in value]
    return model.model_copy(update=updates) if updates else model


def mask_mapping(
    values: Mapping[str, Any], classes: Mapping[str, str], clear: frozenset[str]
) -> dict[str, Any]:
    """Mask the keys of a free-form mapping whose classification is not clear."""
    return {
        key: MaskedValue(classification=classes[key])
        if key in classes and classes[key] not in clear
        else value
        for key, value in values.items()
    }
