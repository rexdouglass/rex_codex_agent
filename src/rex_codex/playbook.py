"""Compatibility shim for project runtime playbook helpers."""

from __future__ import annotations

from ._compat import reexport

reexport("rex_codex.scope_project.playbook", globals())
