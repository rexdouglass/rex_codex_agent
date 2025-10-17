"""Expose the global shim scope for external callers."""

from __future__ import annotations

from .cli import app, build_parser  # noqa: F401
from .install import run_install  # noqa: F401
from .self_update import self_update  # noqa: F401
from .uninstall import uninstall_agent  # noqa: F401

__all__ = [
    "app",
    "build_parser",
    "run_install",
    "self_update",
    "uninstall_agent",
]
