"""calculations.modules

The calculation modules registered in code (D-31 §1). A module is a pure function over
frozen typed inputs and pinned parameters (see `base`). Each registered module version is
seeded as a `module_version` row; a run refuses a module version that is not both
registered here and AVAILABLE in the registry (409 `module_not_available`). There is no API
to create a module: a new module version is a code release plus a migration.
"""

from app.calculations.modules import lcr_hqla, maturity_ladder
from app.calculations.modules.base import RegisteredModule

# (code, version) -> module
REGISTRY: dict[tuple[str, int], RegisteredModule] = {
    (m.spec.code, m.spec.version): m for m in (maturity_ladder.MODULE, lcr_hqla.MODULE)
}


def registered(code: str, version: int) -> RegisteredModule | None:
    return REGISTRY.get((code, version))
