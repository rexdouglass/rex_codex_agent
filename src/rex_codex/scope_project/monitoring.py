"""Helpers for launching the local monitoring UI."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import TypedDict

from .utils import RexContext, which

_MONITOR_STARTED = False


class _PortInfo(TypedDict, total=False):
    port: int
    url: str


_DEFAULT_PORT = 4321
_HEALTH_TIMEOUT = float(os.environ.get("MONITOR_HEALTH_TIMEOUT", "1.5") or "1.5")
_WAIT_SECONDS = float(os.environ.get("MONITOR_BOOT_TIMEOUT", "5.0") or "5.0")


def _read_port_file(path: Path) -> _PortInfo | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    port = payload.get("port")
    if isinstance(port, int) and port > 0:
        info: _PortInfo = {"port": port}
        url = payload.get("url")
        if isinstance(url, str):
            info["url"] = url
        return info
    return None


def _monitor_health(port: int) -> bool:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/health", timeout=_HEALTH_TIMEOUT
        ) as response:
            if response.status != 200:
                return False
            payload = json.loads(response.read().decode("utf-8"))
            return bool(payload and payload.get("ok"))
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError):
        return False


def _port_open(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(_HEALTH_TIMEOUT)
    try:
        sock.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        try:
            sock.close()
        except OSError:
            pass


def _await_monitor_ready(context: RexContext) -> _PortInfo | None:
    deadline = time.time() + _WAIT_SECONDS
    port_file = context.monitor_log_dir / "monitor.port"
    while time.time() < deadline:
        info = _read_port_file(port_file)
        if info and _monitor_health(info["port"]):
            return info
        time.sleep(0.2)
    info = _read_port_file(port_file)
    if info and _monitor_health(info["port"]):
        return info
    return None


def ensure_monitor_server(
    context: RexContext,
    *,
    open_browser: bool = True,
    extra_env: dict[str, str] | None = None,
) -> None:
    """Launch the monitor web server in the background if available.

    The monitor is optional; failures to spawn are ignored so the core agent
    workflow keeps running even when Node/monitor assets are missing.
    """

    if os.environ.get("REX_DISABLE_MONITOR_UI", "").lower() in {"1", "true", "yes"}:
        return

    global _MONITOR_STARTED
    if _MONITOR_STARTED:
        port_file = context.monitor_log_dir / "monitor.port"
        info = _read_port_file(port_file)
        if info and _monitor_health(info["port"]):
            return
        _MONITOR_STARTED = False

    launcher = context.root / "monitor" / "agent" / "launch-monitor.js"
    if not launcher.exists():
        return

    node = which("node")
    if node is None:
        return

    os.environ.setdefault("LOG_DIR", str(context.monitor_log_dir))
    os.environ.setdefault("REPO_ROOT", str(context.root))
    os.environ.setdefault("GENERATOR_UI_POPOUT", "0")
    os.environ.setdefault("GENERATOR_UI_TUI", "0")

    port_file = context.monitor_log_dir / "monitor.port"
    existing = _read_port_file(port_file)
    if existing and _monitor_health(existing["port"]):
        os.environ.setdefault("MONITOR_PORT", str(existing["port"]))
        _MONITOR_STARTED = True
        return

    env = os.environ.copy()
    env.setdefault("LOG_DIR", str(context.monitor_log_dir))
    env.setdefault("REPO_ROOT", str(context.root))
    env.setdefault("MONITOR_PORT", os.environ.get("MONITOR_PORT", str(_DEFAULT_PORT)))
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
        result = subprocess.run(
            args,
            cwd=context.root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return

    stdout = (result.stdout or "").strip()
    if stdout:
        for line in stdout.splitlines():
            print(f"[monitor] {line}")
    elif result.returncode != 0 and result.stderr:
        print("[monitor] Failed to launch UI:", result.stderr.strip())

    info = _await_monitor_ready(context)
    if info:
        os.environ["MONITOR_PORT"] = str(info["port"])
        _MONITOR_STARTED = True
        if stdout:
            # already printed, but ensure discovered port is visible
            pass
        else:
            url = info.get("url") or f"http://localhost:{info['port']}"
            print(f"[monitor] UI listening at {url}")
        return

    # monitor failed to boot within timeout; surface diagnostics
    last_port = env.get("MONITOR_PORT")
    if last_port and _port_open(int(last_port)) and not _monitor_health(int(last_port)):
        print(
            f"[monitor] Port {last_port} is occupied but not serving the Codex monitor. "
            "Consider setting MONITOR_PORT to a free port."
        )
