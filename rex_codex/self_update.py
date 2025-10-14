"""Agent self-update helpers."""

from __future__ import annotations

import os
from pathlib import Path

from .config import AGENT_SRC
from .utils import RexError, run


def self_update(channel: str | None = None) -> None:
    """Mirror the legacy Bash self-update strategy."""
    if os.environ.get("REX_AGENT_NO_UPDATE", "1") == "1" and channel is None:
        return

    src = AGENT_SRC
    if not (src / ".git").exists():
        # Nothing to update; installation likely incomplete.
        return

    run(["git", "-C", str(src), "fetch", "--all", "--tags", "--prune", "--force"], check=False)

    channel = channel or os.environ.get("REX_AGENT_CHANNEL", "stable")
    if channel == "stable":
        completed = run(
            ["git", "-C", str(src), "tag", "--sort=-v:refname"],
            capture_output=True,
            check=False,
        )
        tags = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
        target = tags[0] if tags else "main"
        run(["git", "-C", str(src), "checkout", "-q", target], check=False)
    elif channel == "main":
        run(["git", "-C", str(src), "checkout", "-q", "main"], check=False)
        run(["git", "-C", str(src), "pull", "--ff-only"], check=False)
    else:
        run(["git", "-C", str(src), "checkout", "-q", channel], check=False)

