"""Compatibility shim for project runtime hermetic helpers."""

from __future__ import annotations

from ._compat import reexport

reexport("rex_codex.scope_project.hermetic", globals())
