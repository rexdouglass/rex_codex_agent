"""Expose the global shim scope for external callers."""

from __future__ import annotations

from typing import Any

__all__ = ["app", "build_parser", "run_install", "self_update", "uninstall_agent"]


def __getattr__(name: str) -> Any:
    if name == "app" or name == "build_parser":
        from .cli import app as _app, build_parser as _build_parser

        return {"app": _app, "build_parser": _build_parser}[name]
    if name == "run_install":
        from .install import run_install as _run_install

        return _run_install
    if name == "self_update":
        from .self_update import self_update as _self_update

        return _self_update
    if name == "uninstall_agent":
        from .uninstall import uninstall_agent as _uninstall_agent

        return _uninstall_agent
    raise AttributeError(name)
