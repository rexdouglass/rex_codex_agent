from __future__ import annotations

import json
from pathlib import Path

from rex_codex import events


def test_emit_event_writes_jsonl(tmp_path, monkeypatch) -> None:
    target = tmp_path / "custom-events.log"
    monkeypatch.setenv("REX_EVENTS_FILE", str(target))
    events.reset_events_cache()

    events.emit_event("generator", "feature_started", slug="hello", title="Hello CLI")
    events.emit_event("generator", "iteration_start", slug="hello", iteration=1, focus="default")

    contents = target.read_text(encoding="utf-8").splitlines()
    assert len(contents) == 2

    first = json.loads(contents[0])
    second = json.loads(contents[1])

    assert first["phase"] == "generator"
    assert first["type"] == "feature_started"
    assert first["slug"] == "hello"
    assert first["data"]["title"] == "Hello CLI"

    assert second["data"]["iteration"] == 1
    assert second["data"]["focus"] == "default"


def test_emit_event_missing_parent_directory(tmp_path, monkeypatch) -> None:
    target_dir = tmp_path / "nested" / "dir"
    target = target_dir / "events.jsonl"
    monkeypatch.setenv("REX_EVENTS_FILE", str(target))
    events.reset_events_cache()

    events.emit_event("generator", "heartbeat", slug=None, seconds=5)

    assert target.exists()
    decoded = json.loads(target.read_text(encoding="utf-8"))
    assert decoded["type"] == "heartbeat"
    assert decoded["data"]["seconds"] == 5
