"""Log helpers for rex-codex."""

from __future__ import annotations

import time
from pathlib import Path

from .utils import RexContext


def tail_log(path: Path, *, lines: int = 120) -> None:
    if not path.exists():
        print(f"[logs] {path} not found.")
        return
    content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(0, len(content) - lines)
    for line in content[start:]:
        print(line)


def follow_log(path: Path) -> None:
    if not path.exists():
        print(f"[logs] {path} not found.")
        return
    print(f"[logs] Following {path} (press Ctrl-C to stop)")
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(0, 2)
            while True:
                line = handle.readline()
                if not line:
                    time.sleep(0.5)
                    continue
                print(line, end="")
    except KeyboardInterrupt:  # pragma: no cover - user interaction
        print("\n[logs] Follow stopped.")


def show_latest_logs(
    context: RexContext,
    *,
    lines: int = 120,
    generator: bool = False,
    discriminator: bool = False,
    follow: bool = False,
) -> None:
    sections: list[tuple[str, Path]] = []

    include_generator = generator or not (generator or discriminator)
    include_discriminator = discriminator or not (generator or discriminator)

    if follow:
        if include_discriminator:
            target = context.root / ".codex_ci_latest.log"
        else:
            target = context.codex_ci_dir / "generator_response.log"
        follow_log(target)
        return

    if include_generator:
        sections.extend(
            [
                ("Generator response", context.codex_ci_dir / "generator_response.log"),
                ("Generator patch", context.codex_ci_dir / "generator_patch.diff"),
                ("Generator tests", context.codex_ci_dir / "generator_tests.log"),
            ]
        )
    if include_discriminator:
        sections.extend(
            [
                (
                    "Discriminator log",
                    context.codex_ci_dir / "latest_discriminator.log",
                ),
                ("Discriminator latest", context.root / ".codex_ci_latest.log"),
            ]
        )

    seen: set[Path] = set()
    for label, path in sections:
        if path in seen:
            continue
        seen.add(path)
        if path.exists():
            print(f"--- {label}: {context.relative(path)} (last {lines} lines) ---")
            tail_log(path, lines=lines)
        else:
            print(f"[logs] Missing {label.lower()} at {context.relative(path)}")
