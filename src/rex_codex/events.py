"""Compatibility shim for project runtime event helpers."""

from __future__ import annotations

from ._compat import reexport

reexport("rex_codex.scope_project.events", globals())
