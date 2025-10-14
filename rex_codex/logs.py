"""Log helpers for rex-codex."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .utils import RexContext


def tail_log(path: Path, *, lines: int = 120) -> None:
    if not path.exists():
        print(f"[logs] {path} not found.")
        return
    content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(0, len(content) - lines)
    for line in content[start:]:
        print(line)


def show_latest_logs(context: RexContext, *, lines: int = 120) -> None:
    candidates = [
        context.codex_ci_dir / "latest_discriminator.log",
        context.codex_ci_dir / "generator_response.log",
        context.codex_ci_dir / "generator_critic_response.log",
        context.codex_ci_dir / "latest.log",
    ]
    for path in candidates:
        if path.exists():
            print(f"--- {context.relative(path)} (tail {lines}) ---")
            tail_log(path, lines=lines)
        else:
            print(f"[logs] No recent log at {path}")

