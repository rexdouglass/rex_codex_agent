from __future__ import annotations

import json

from rex_codex.scope_project import events


def test_emit_event_includes_schema_and_ids(tmp_path, monkeypatch):
    events_file = tmp_path / "events.jsonl"
    monitor_file = tmp_path / "monitor.jsonl"
    monkeypatch.setenv("REX_EVENTS_FILE", str(events_file))
    monkeypatch.setenv("REX_MONITOR_EVENTS_FILE", str(monitor_file))
    events.reset_events_cache()

    events.emit_event("generator", "unit_test", slug="fc-0001", detail=42)

    raw_lines = events_file.read_text(encoding="utf-8").splitlines()
    assert raw_lines
    record = json.loads(raw_lines[0])
    assert record["schema_version"] == events.EVENT_SCHEMA_VERSION
    assert record["event_id"]
    assert record["source"] == "rex_codex"
    assert record["phase"] == "generator"
    assert record["data"]["detail"] == 42

    monitor_lines = monitor_file.read_text(encoding="utf-8").splitlines()
    assert monitor_lines
    monitor_record = json.loads(monitor_lines[0])
    assert monitor_record["schema_version"] == events.EVENT_SCHEMA_VERSION
    assert monitor_record["event_id"] == record["event_id"]

    events.reset_events_cache()
