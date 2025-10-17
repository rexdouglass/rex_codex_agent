"""Compatibility shim exposing global self-update helpers."""

from __future__ import annotations

from ._compat import reexport

reexport("rex_codex.scope_global.self_update", globals())
