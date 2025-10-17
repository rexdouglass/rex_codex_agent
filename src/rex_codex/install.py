"""Compatibility shim exposing global install helpers."""

from __future__ import annotations

from ._compat import reexport

reexport("rex_codex.scope_global.install", globals())
