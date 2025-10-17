"""rex_codex Python package.

This package hosts the primary CLI implementation for the rex-codex agent.
The legacy Bash entrypoints now delegate to these modules so behaviour can be
unit-tested and extended directly in Python.
"""

from __future__ import annotations

from importlib import import_module
from pathlib import Path


def _read_version() -> str:
    """Resolve the project VERSION file even when the package lives under src/."""
    for parent in Path(__file__).resolve().parents:
        version_file = parent / "VERSION"
        if version_file.is_file():
            return version_file.read_text(encoding="utf-8").strip()
    return "0.0.0"


__all__ = ["__version__", "scope_global", "scope_project", "scope_sandbox"]
__version__ = _read_version()


def __getattr__(name: str):
    if name in {"scope_global", "scope_project", "scope_sandbox"}:
        return import_module(f"{__name__}.{name}")
    raise AttributeError(name)
