"""Event stream helpers for generator/discriminator progress."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .utils import ensure_dir, repo_root

_EVENTS_PATH_CACHE: Path | None = None
_MONITOR_EVENTS_PATH_CACHE: Path | None = None


def _default_events_path() -> Path:
    root = repo_root()
    return root / ".codex_ci" / "events.jsonl"


def _default_monitor_events_path() -> Path:
    root = repo_root()
    return root / ".agent" / "logs" / "events.jsonl"


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


def _resolve_monitor_events_path() -> Path:
    global _MONITOR_EVENTS_PATH_CACHE
    if _MONITOR_EVENTS_PATH_CACHE is not None:
        return _MONITOR_EVENTS_PATH_CACHE
    raw = os.environ.get("REX_MONITOR_EVENTS_FILE")
    if raw:
        candidate = Path(raw).expanduser()
    else:
        base = os.environ.get("LOG_DIR")
        if base:
            candidate = Path(base).expanduser() / "events.jsonl"
        else:
            candidate = _default_monitor_events_path()
    ensure_dir(candidate.parent)
    _MONITOR_EVENTS_PATH_CACHE = candidate
    return candidate


def events_path() -> Path:
    """Return the resolved events log path (creates the directory if needed)."""

    return _resolve_events_path()


def reset_events_cache() -> None:
    """Clear the cached events path (mostly for tests)."""

    global _EVENTS_PATH_CACHE
    global _MONITOR_EVENTS_PATH_CACHE
    _EVENTS_PATH_CACHE = None
    _MONITOR_EVENTS_PATH_CACHE = None


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
        _mirror_to_monitor(record)
    except Exception:
        # Never let monitoring abort the main flow.
        return


def _mirror_to_monitor(record: Mapping[str, Any]) -> None:
    try:
        monitor_path = _resolve_monitor_events_path()
    except Exception:
        return

    monitor_event = _to_monitor_event(record)
    if not monitor_event:
        return
    try:
        with monitor_path.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(monitor_event, ensure_ascii=False, default=_json_default)
            )
            fh.write("\n")
    except Exception:
        return


def _to_monitor_event(record: Mapping[str, Any]) -> Mapping[str, Any] | None:
    ts = record.get("ts")
    if not isinstance(ts, str):
        return None
    phase = str(record.get("phase", "")).strip()
    type_ = str(record.get("type", "")).strip()
    slug = record.get("slug")
    data = record.get("data") or {}
    if not isinstance(data, Mapping):
        data = {}

    level = _monitor_level(type_, data)
    status = _extract_status(data, level)
    progress = _extract_progress(data)
    task = _extract_task(slug, data)
    message = _compose_message(phase, type_, slug, data, status)

    meta = _extract_meta(data, phase, type_)
    if meta is None:
        meta = {}
    meta.setdefault("slug", slug)
    monitor_event = {
        "ts": ts,
        "level": level,
        "message": message,
    }
    if task:
        monitor_event["task"] = task
    if status:
        monitor_event["status"] = status
    if progress is not None:
        monitor_event["progress"] = progress
    if meta:
        monitor_event["meta"] = meta
    return monitor_event


def _monitor_level(type_: str, data: Mapping[str, Any]) -> str:
    explicit = str(data.get("level", "")).lower()
    if explicit in {"info", "warn", "warning", "error", "debug", "task", "progress"}:
        if explicit == "warning":
            return "warn"
        return explicit
    lowered = type_.lower()
    status = str(data.get("status", "")).lower()
    if "error" in lowered or "failed" in lowered or status in {"failed", "error"}:
        return "error"
    if "warn" in lowered or status in {"warning", "warn"}:
        return "warn"
    if "debug" in lowered:
        return "debug"
    if str(data.get("ok")).lower() == "false":
        return "error"
    return "info"


def _extract_status(data: Mapping[str, Any], level: str) -> str | None:
    status = data.get("status")
    if isinstance(status, str):
        return status
    if level == "error":
        return "failed"
    if level == "warn":
        return "warning"
    return None


def _extract_progress(data: Mapping[str, Any]) -> float | None:
    raw = data.get("progress")
    if isinstance(raw, (int, float)):
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        if value != value:  # NaN check
            return None
        return max(0.0, min(1.0, value))
    # Some events report percentages
    percent = data.get("percentage")
    if isinstance(percent, (int, float)):
        try:
            value = float(percent) / 100.0
        except (TypeError, ValueError):
            return None
        return max(0.0, min(1.0, value))
    return None


def _extract_task(slug: Any, data: Mapping[str, Any]) -> str | None:
    task = data.get("task")
    if isinstance(task, str) and task:
        return task
    if isinstance(slug, str) and slug:
        return slug
    stage = data.get("identifier")
    description = data.get("description")
    if isinstance(stage, str) and isinstance(description, str):
        return f"{stage} {description}".strip()
    return None


def _compose_message(
    phase: str,
    type_: str,
    slug: Any,
    data: Mapping[str, Any],
    status: str | None,
) -> str:
    headline = f"{phase}:{type_}" if phase else type_
    if isinstance(slug, str) and slug:
        headline = f"{slug} · {headline}"

    detail_keys = (
        "message",
        "summary",
        "description",
        "command",
        "guidance",
        "reason",
        "failure_reason",
        "note",
    )
    detail = None
    for key in detail_keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            detail = value.strip()
            break

    if detail is None:
        iteration = data.get("iteration")
        total = data.get("total_passes") or data.get("passes")
        if isinstance(iteration, int) and isinstance(total, int) and total > 0:
            detail = f"iteration {iteration}/{total}"
        elif isinstance(iteration, int):
            detail = f"iteration {iteration}"
        elif isinstance(total, int):
            detail = f"{total} total passes"
        elif status:
            detail = status

    if detail:
        return f"{headline} — {detail}"
    return headline


def _extract_meta(
    data: Mapping[str, Any], phase: str, type_: str
) -> Mapping[str, Any] | None:
    ignore_keys = {
        "message",
        "summary",
        "description",
        "command",
        "guidance",
        "reason",
        "failure_reason",
        "note",
        "status",
        "progress",
        "task",
        "level",
    }
    meta: dict[str, Any] = {"phase": phase, "type": type_}
    for key, value in data.items():
        if key in ignore_keys:
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            meta[key] = value
        else:
            meta[key] = _json_default(value)
    return meta or None
