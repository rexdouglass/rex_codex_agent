"""Diagnostics for rex-codex."""

from __future__ import annotations

from .utils import which


TOOLS = ("python3", "node", "npx", "docker")


def run_doctor() -> None:
    for tool in TOOLS:
        path = which(tool)
        if path:
            print(f"[doctor] {tool}: {path}")
        else:
            print(f"[doctor] {tool}: missing (install or add to PATH)")
