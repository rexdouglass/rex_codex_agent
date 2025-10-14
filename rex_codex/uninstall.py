"""Implementation of `rex-codex uninstall`."""

from __future__ import annotations

import shutil
from pathlib import Path

from .utils import RexContext, ask_confirmation


def uninstall_agent(*, force: bool, keep_wrapper: bool, context: RexContext | None = None) -> None:
    context = context or RexContext.discover()
    root = context.root
    agent_dir = root / ".rex_agent"
    wrapper = root / "rex-codex"

    if not force:
        print("This will remove the Codex agent from:")
        print(f"  - {agent_dir}")
        if keep_wrapper:
            print("  - (wrapper preserved due to --keep-wrapper)")
        else:
            print(f"  - {wrapper}")
        if not ask_confirmation("Type 'remove agent' to continue: ", expected="remove agent"):
            print("[uninstall] Aborted.")
            return

    if agent_dir.exists():
        shutil.rmtree(agent_dir)
        print(f"[uninstall] Removed {agent_dir}")
    else:
        print("[uninstall] No .rex_agent directory found; nothing to remove.")

    if not keep_wrapper and wrapper.exists():
        wrapper.unlink()
        print(f"[uninstall] Removed {wrapper}")

    print("[uninstall] Agent uninstalled. Remove guardrail artefacts manually if desired.")

