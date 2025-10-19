"""Lifecycle helpers for cleaning up loop-related background processes."""

from __future__ import annotations

import json
import os
import signal
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable, Iterator, List, MutableSequence

from .utils import RexContext, dump_json, ensure_dir

_REGISTRY_FILENAME = "loop_processes.json"
_LOCK_FILENAME = "loop_processes.lock"


@dataclass(slots=True)
class _ProcessEntry:
    pid: int
    label: str
    command: str | None
    started_at: str

    @classmethod
    def from_dict(cls, data: dict) -> _ProcessEntry | None:
        pid = data.get("pid")
        label = data.get("label") or "loop"
        command = data.get("command")
        started_at = data.get("started_at")
        if not isinstance(pid, int) or pid <= 0:
            return None
        if not isinstance(label, str):
            label = str(label)
        if command is not None and not isinstance(command, str):
            command = str(command)
        if not isinstance(started_at, str):
            started_at = datetime.now(UTC).isoformat()
        return cls(pid=pid, label=label, command=command, started_at=started_at)

    def to_dict(self) -> dict:
        payload: dict[str, object] = {
            "pid": self.pid,
            "label": self.label,
            "started_at": self.started_at,
        }
        if self.command:
            payload["command"] = self.command
        return payload


@contextmanager
def _registry_lock(context: RexContext) -> Iterator[None]:
    lock_path = context.codex_ci_dir / _LOCK_FILENAME
    ensure_dir(lock_path.parent)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o666)
    try:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _registry_path(context: RexContext) -> Path:
    ensure_dir(context.codex_ci_dir)
    return context.codex_ci_dir / _REGISTRY_FILENAME


def _load_registry(context: RexContext) -> list[_ProcessEntry]:
    path = _registry_path(context)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except json.JSONDecodeError:
        path.unlink(missing_ok=True)
        return []
    entries: list[_ProcessEntry] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                entry = _ProcessEntry.from_dict(item)
                if entry is not None:
                    entries.append(entry)
    return entries


def _write_registry(context: RexContext, entries: MutableSequence[_ProcessEntry]) -> None:
    path = _registry_path(context)
    payload = [entry.to_dict() for entry in entries]
    dump_json(path, payload)


def register_loop_process(
    pid: int,
    *,
    context: RexContext,
    label: str,
    command: str | None = None,
) -> None:
    """Record a background process so later loop invocations can terminate it."""

    if pid <= 0:
        return
    entry = _ProcessEntry(
        pid=pid,
        label=label,
        command=command,
        started_at=datetime.now(UTC).isoformat(),
    )
    with _registry_lock(context):
        entries = _load_registry(context)
        entries = [existing for existing in entries if existing.pid != pid]
        entries.append(entry)
        _write_registry(context, entries)


def unregister_loop_process(pid: int, *, context: RexContext) -> None:
    """Remove a process from the registry once it has exited cleanly."""

    if pid <= 0:
        return
    with _registry_lock(context):
        entries = _load_registry(context)
        new_entries = [entry for entry in entries if entry.pid != pid]
        if len(new_entries) == len(entries):
            return
        if new_entries:
            _write_registry(context, new_entries)
        else:
            _registry_path(context).unlink(missing_ok=True)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but belongs to another user; assume alive to avoid tampering.
        return True
    else:
        return True


def _terminate_pid(pid: int, *, gentle_seconds: float = 1.5) -> None:
    if pid <= 0:
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError:
        return
    deadline = time.monotonic() + max(gentle_seconds, 0.0)
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return
        time.sleep(0.1)
    if not _pid_alive(pid):
        return
    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        return


def cleanup_loop_processes(
    context: RexContext,
    *,
    keep_labels: Iterable[str] | None = None,
) -> list[str]:
    """Kill any recorded processes from previous loop executions.

    Returns a list of human-readable notes describing performed actions.
    """

    keep = {label for label in (keep_labels or []) if label}
    notes: list[str] = []
    with _registry_lock(context):
        entries = _load_registry(context)
        survivors: list[_ProcessEntry] = []
        for entry in entries:
            if entry.label in keep:
                survivors.append(entry)
                continue
            if not _pid_alive(entry.pid):
                notes.append(
                    f"Process {entry.pid} ({entry.label}) already exited; removing from registry."
                )
                continue
            command_part = f" :: {entry.command}" if entry.command else ""
            notes.append(
                f"Terminating lingering process {entry.pid} ({entry.label}){command_part}"
            )
            _terminate_pid(entry.pid)
            if _pid_alive(entry.pid):
                notes.append(
                    f"Process {entry.pid} ({entry.label}) resisted termination; keeping entry."
                )
                survivors.append(entry)
            else:
                notes.append(f"Process {entry.pid} ({entry.label}) terminated.")
        if survivors:
            _write_registry(context, survivors)
        else:
            _registry_path(context).unlink(missing_ok=True)
    return notes
