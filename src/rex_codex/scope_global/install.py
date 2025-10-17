"""Install or re-install the rex-codex agent."""

from __future__ import annotations

import os
import subprocess

from ..scope_project.config import AGENT_SRC
from ..scope_project.doctor import run_doctor
from ..scope_project.init import run_init
from ..scope_project.utils import RexContext, RexError


def run_install(
    *,
    force: bool = False,
    channel: str | None = None,
    run_init_after: bool = True,
    run_doctor_after: bool = True,
    context: RexContext | None = None,
) -> None:
    """Invoke the bundled install script to (re)install the agent."""
    context = context or RexContext.discover()
    script = AGENT_SRC / "packaging" / "install.sh"
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
    env["REX_AGENT_SKIP_INIT"] = "1"
    env["REX_AGENT_SKIP_DOCTOR"] = "1"
    completed = subprocess.run(cmd, cwd=context.root, env=env)
    if completed.returncode != 0:
        raise RexError(f"Install command failed with exit code {completed.returncode}")

    if run_init_after:
        run_init(context=context, perform_self_update=False)
    if run_doctor_after:
        run_doctor()
