from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

import rex_codex.discriminator as disc


@pytest.mark.parametrize("fail_identifier", [None, "06.2"])
def test_parallel_style_gates_run_in_threads(monkeypatch, tmp_path, fail_identifier):
    events: list[dict[str, object]] = []
    thread_names: list[str] = []

    def fake_emit_event(phase: str, type_: str, *, slug=None, **data):
        events.append({"phase": phase, "type": type_, "slug": slug, "data": data})

    def fake_execute_stage(stage, env, context, log_path, latest_log_path, print_lock=None):
        thread_names.append(threading.current_thread().name)
        should_fail = stage.identifier == fail_identifier
        return (not should_fail), 0.01, ("boom" if should_fail else "")

    def fake_configure_pytest_flags(mode, env, context):
        return []

    def fake_build_stage_groups(mode, slug, pytest_flags, env, context):
        return [
            disc.StageGroup(
                title="Level 06 - Style & Type Gates",
                stages=[
                    disc.Stage("06.1", "black --check", "echo black"),
                    disc.Stage("06.2", "isort --check", "echo isort"),
                ],
            )
        ]

    monkeypatch.setattr(disc, "emit_event", fake_emit_event)
    monkeypatch.setattr(disc, "_execute_stage", fake_execute_stage)
    monkeypatch.setattr(disc, "_configure_pytest_flags", fake_configure_pytest_flags)
    monkeypatch.setattr(disc, "_build_stage_groups", fake_build_stage_groups)
    monkeypatch.setattr(disc, "find_orphan_spec_slugs", lambda context: [])

    codex_ci_dir = tmp_path / ".codex_ci"
    codex_ci_dir.mkdir()
    context = SimpleNamespace(root=tmp_path, codex_ci_dir=codex_ci_dir)
    log_path = codex_ci_dir / "log.log"
    latest_log_path = tmp_path / "latest.log"
    log_path.write_text("", encoding="utf-8")
    latest_log_path.write_text("", encoding="utf-8")

    result = disc._run_stage_plan(
        mode="global",
        slug=None,
        env={},
        context=context,
        log_path=log_path,
        latest_log_path=latest_log_path,
        pass_number=1,
        run_id=1,
        attempt=1,
    )

    assert thread_names, "expected worker threads to execute stages"
    assert all(name != "MainThread" for name in thread_names)
    stage_end_events = [event for event in events if event["type"] == "stage_end"]
    identifiers = {event["data"]["identifier"] for event in stage_end_events}
    assert identifiers == {"06.1", "06.2"}
    status_map = {event["data"]["identifier"]: event["data"]["ok"] for event in stage_end_events}
    assert status_map["06.1"] is True
    assert status_map["06.2"] is (fail_identifier is None)
    run_completed = [event for event in events if event["type"] == "run_completed"]
    assert run_completed and run_completed[-1]["data"]["ok"] is (fail_identifier is None)
    assert result is (fail_identifier is None)
