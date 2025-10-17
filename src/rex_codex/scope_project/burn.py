"""Implementation of `rex-codex burn`."""

from __future__ import annotations

import shutil

from .utils import RexContext, ask_confirmation, ensure_dir


def burn_repo(
    *,
    force: bool,
    purge_agent: bool,
    dry_run: bool,
    context: RexContext | None = None,
) -> None:
    context = context or RexContext.discover()
    root = context.root
    print(f"WARNING: This will delete repository files in {root}")
    if purge_agent:
        print("  - .rex_agent will be removed")
    else:
        print("  - .rex_agent will be preserved")
    print("  - .git directory is always preserved")

    if dry_run:
        print("[burn] Dry-run mode: no files will be deleted.")
    elif not force:
        if not ask_confirmation(
            "Type 'burn it down' to continue: ", expected="burn it down"
        ):
            print("[burn] Aborted.")
            return

    entries = list(root.iterdir())
    for entry in entries:
        name = entry.name
        if name in {".", ".."}:
            continue
        if name in {".git", "rex-codex"}:
            continue
        if name == ".rex_agent" and not purge_agent:
            continue
        if dry_run:
            print(f"[dry-run] would remove: {entry}")
            continue
        if entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()

    if dry_run:
        print("[✓] Dry-run complete. No files were removed.")
        return

    ensure_dir(root)
    print("[✓] Repository reset. Re-run ./rex-codex init to seed fresh scaffolding.")
