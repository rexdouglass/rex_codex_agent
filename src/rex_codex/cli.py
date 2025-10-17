"""Compatibility shim for legacy imports of rex_codex.cli."""

from __future__ import annotations

from ._compat import reexport

reexport("rex_codex.scope_global.cli", globals())
