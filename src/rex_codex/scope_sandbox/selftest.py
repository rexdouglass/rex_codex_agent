"""Python helpers for invoking the self-test sandbox loops."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Mapping, MutableMapping


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def selftest_script() -> Path:
    script = _repo_root() / "scripts" / "selftest_loop.sh"
    if not script.exists():
        raise FileNotFoundError(f"Selftest script missing at {script}")
    return script


def smoke_script() -> Path:
    script = _repo_root() / "scripts" / "smoke_e2e.sh"
    if not script.exists():
        raise FileNotFoundError(f"Smoke script missing at {script}")
    return script


def run_selftest(*, keep_workspace: bool = False, extra_env: Mapping[str, str] | None = None) -> subprocess.CompletedProcess:
    env: MutableMapping[str, str] = os.environ.copy()
    if keep_workspace:
        env["SELFTEST_KEEP"] = "1"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(["bash", str(selftest_script())], env=env, check=False)


def run_smoke(*, keep_workspace: bool = False, extra_env: Mapping[str, str] | None = None) -> subprocess.CompletedProcess:
    env: MutableMapping[str, str] = os.environ.copy()
    if keep_workspace:
        env["KEEP"] = "1"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(["bash", str(smoke_script())], env=env, check=False)
