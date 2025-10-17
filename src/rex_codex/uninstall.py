"""Compatibility shim exposing global uninstall helpers."""

from __future__ import annotations

from ._compat import reexport

reexport("rex_codex.scope_global.uninstall", globals())
