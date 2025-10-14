"""Install or re-install the rex-codex agent."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .config import AGENT_SRC
from .utils import RexContext, RexError


def run_install(
    *,
    force: bool = False,
    channel: str | None = None,
    context: RexContext | None = None,
) -> None:
    """Invoke the bundled install script to (re)install the agent."""
    context = context or RexContext.discover()
    script = AGENT_SRC / "scripts" / "install.sh"
    if not script.exists():
        raise RexError(f"Install script not found: {script}")

    cmd = ["bash", str(script)]
    if force:
        cmd.append("--force")
    if channel:
        cmd.extend(["--channel", channel])

    env = os.environ.copy()
    if channel:
        env["REX_AGENT_CHANNEL"] = channel
    completed = subprocess.run(cmd, cwd=context.root, env=env)
    if completed.returncode != 0:
        raise RexError(f"Install command failed with exit code {completed.returncode}")
