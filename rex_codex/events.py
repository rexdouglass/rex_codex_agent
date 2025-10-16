"""Event stream helpers for generator/discriminator progress."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Mapping

from .utils import ensure_dir, repo_root

_EVENTS_PATH_CACHE: Path | None = None


def _default_events_path() -> Path:
    root = repo_root()
    return root / ".codex_ci" / "events.jsonl"


def _resolve_events_path() -> Path:
    global _EVENTS_PATH_CACHE
    if _EVENTS_PATH_CACHE is not None:
        return _EVENTS_PATH_CACHE
    raw = os.environ.get("REX_EVENTS_FILE")
    if raw:
        candidate = Path(raw).expanduser()
    else:
        candidate = _default_events_path()
    ensure_dir(candidate.parent)
    _EVENTS_PATH_CACHE = candidate
    return candidate


def events_path() -> Path:
    """Return the resolved events log path (creates the directory if needed)."""

    return _resolve_events_path()


def reset_events_cache() -> None:
    """Clear the cached events path (mostly for tests)."""

    global _EVENTS_PATH_CACHE
    _EVENTS_PATH_CACHE = None


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, set):
        return sorted(value)
    if isinstance(value, (list, dict, str, int, float, bool)) or value is None:
        return value
    return repr(value)


def emit_event(phase: str, type_: str, *, slug: str | None = None, **data: Any) -> None:
    """Append a structured event to the shared JSONL log.

    Best-effort: failures to serialise or write are swallowed so that progress
    reporting never interferes with the main generator/discriminator flow.
    """

    record: Mapping[str, Any] = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "phase": phase,
        "type": type_,
        "slug": slug,
        "data": data,
    }
    try:
        payload = json.dumps(record, ensure_ascii=False, default=_json_default)
    except Exception:
        return
    try:
        events_path = _resolve_events_path()
        with events_path.open("a", encoding="utf-8") as fh:
            fh.write(payload)
            fh.write("\n")
    except Exception:
        # Never let monitoring abort the main flow.
        return
