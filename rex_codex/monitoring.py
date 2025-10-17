"""Helpers for launching the local monitoring UI."""

from __future__ import annotations

import os
import subprocess
from typing import Optional

from .utils import RexContext, which

_MONITOR_STARTED = False


def ensure_monitor_server(
    context: RexContext,
    *,
    open_browser: bool = True,
    extra_env: Optional[dict[str, str]] = None,
) -> None:
    """Launch the monitor web server in the background if available.

    The monitor is optional; failures to spawn are ignored so the core agent
    workflow keeps running even when Node/monitor assets are missing.
    """

    if os.environ.get("REX_DISABLE_MONITOR_UI", "").lower() in {"1", "true", "yes"}:
        return

    global _MONITOR_STARTED
    if _MONITOR_STARTED:
        return

    launcher = context.root / "monitor" / "agent" / "launch-monitor.js"
    if not launcher.exists():
        return

    node = which("node")
    if node is None:
        return

    os.environ.setdefault("LOG_DIR", str(context.monitor_log_dir))
    os.environ.setdefault("GENERATOR_UI_POPOUT", "0")
    os.environ.setdefault("GENERATOR_UI_TUI", "0")

    env = os.environ.copy()
    env.setdefault("LOG_DIR", str(context.monitor_log_dir))
    env.setdefault("MONITOR_PORT", os.environ.get("MONITOR_PORT", "4321"))
    env.setdefault("GENERATOR_UI_POPOUT", os.environ.get("GENERATOR_UI_POPOUT", "0"))
    env.setdefault("GENERATOR_UI_TUI", os.environ.get("GENERATOR_UI_TUI", "0"))

    if open_browser:
        if os.environ.get("REX_MONITOR_OPEN_BROWSER", "").lower() in {"0", "false"}:
            env.setdefault("OPEN_BROWSER", "false")
        else:
            env.setdefault("OPEN_BROWSER", "true")
    else:
        env.setdefault("OPEN_BROWSER", env.get("OPEN_BROWSER", "false"))

    if extra_env:
        env.update(extra_env)

    args = [node, str(launcher), "--background"]
    try:
        subprocess.Popen(
            args,
            cwd=context.root,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        _MONITOR_STARTED = True
    except OSError:
        # Allow future attempts if launching fails (e.g., transient ENOENT).
        pass
