"""rex_codex Python package.

This package hosts the primary CLI implementation for the rex-codex agent.
The legacy Bash entrypoints now delegate to these modules so behaviour can be
unit-tested and extended directly in Python.
"""

from __future__ import annotations

from pathlib import Path


def _read_version() -> str:
    root = Path(__file__).resolve().parent.parent
    version_file = root / "VERSION"
    try:
        return version_file.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return "0.0.0"


__all__ = ["__version__"]
__version__ = _read_version()
