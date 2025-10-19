"""Helpers for re-exporting modules across the new scope boundaries."""

from __future__ import annotations

import sys
from importlib import import_module
from types import ModuleType


def reexport(module_path: str, global_ns: dict[str, object]) -> ModuleType:
    """Populate ``global_ns`` with attributes from ``module_path``.

    This preserves backwards compatibility for modules that used to live at the
    package root while allowing us to group implementations under
    ``scope_*`` packages.
    """

    module = import_module(module_path)
    exported = getattr(module, "__all__", None)
    if exported is None:
        names = [name for name in dir(module) if not name.startswith("__")]
    else:
        names = list(exported)
        extras = [
            name
            for name in dir(module)
            if name.startswith("_") and not name.startswith("__")
        ]
        for extra in extras:
            if extra not in names:
                names.append(extra)

    for name in names:
        global_ns[name] = getattr(module, name)
    global_ns["__all__"] = names
    module_name_obj = global_ns.get("__name__")
    module_name = module_path if not isinstance(module_name_obj, str) else module_name_obj
    sys.modules[module_name] = module
    return module
