from __future__ import annotations

import json

from rex_codex.hud import discriminator_snapshot_text, generator_snapshot_text


def test_snapshot_uses_last_feature_run(tmp_path):
    slug = "demo"
    events = [
        {"slug": slug, "type": "feature_started", "data": {"title": "Demo", "status": "proposed"}},
        {"slug": slug, "type": "feature_failed"},
        {"slug": slug, "type": "feature_started", "data": {"title": "Demo", "status": "proposed"}},
        {"slug": slug, "type": "feature_completed"},
    ]
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join(json.dumps(event) for event in events))
    output = generator_snapshot_text(slug, path)
    assert output
    assert "FAILED" not in output
    assert "COMPLETED" in output


def test_snapshot_includes_coverage_line(tmp_path):
    slug = "demo"
    events = [
        {
            "slug": slug,
            "type": "feature_started",
            "data": {
                "title": "Demo",
                "status": "proposed",
                "acceptance": ["happy path works", "handles errors"],
            },
        },
        {
            "slug": slug,
            "type": "spec_trace_update",
            "data": {
                "coverage": {
                    "entries": [
                        {
                            "index": 1,
                            "text": "happy path works",
                            "tests": ["tests/feature_specs/demo/test_demo.py::test_happy"],
                        },
                        {"index": 2, "text": "handles errors", "tests": []},
                    ],
                    "missing": [{"index": 2, "text": "handles errors", "tests": []}],
                    "orphans": [],
                }
            },
        },
        {
            "slug": slug,
            "type": "pytest_snapshot",
            "data": {"status": "failed"},
        },
    ]
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join(json.dumps(event) for event in events))
    output = generator_snapshot_text(slug, path)
    assert "Coverage: " in output
    assert "1/2 bullets linked" in output


def test_discriminator_snapshot_tracks_latest_run(tmp_path):
    events = [
        {
            "phase": "discriminator",
            "type": "run_started",
            "slug": None,
            "data": {
                "mode": "global",
                "pass_number": 1,
                "run_id": 1,
                "attempt": 1,
                "stage_groups": ["Level 00 - Repo & System Health"],
            },
        },
        {
            "phase": "discriminator",
            "type": "stage_start",
            "slug": None,
            "data": {
                "mode": "global",
                "pass_number": 1,
                "run_id": 1,
                "attempt": 1,
                "identifier": "00.1",
                "description": "Git status",
                "group": "Level 00 - Repo & System Health",
                "command": "git status -sb",
            },
        },
        {
            "phase": "discriminator",
            "type": "stage_end",
            "slug": None,
            "data": {
                "mode": "global",
                "pass_number": 1,
                "run_id": 1,
                "attempt": 1,
                "identifier": "00.1",
                "description": "Git status",
                "group": "Level 00 - Repo & System Health",
                "command": "git status -sb",
                "ok": True,
                "elapsed": 0.12,
                "tail": "",
                "failure_reason": "",
            },
        },
        {
            "phase": "discriminator",
            "type": "run_completed",
            "slug": None,
            "data": {
                "mode": "global",
                "pass_number": 1,
                "run_id": 1,
                "attempt": 1,
                "ok": True,
            },
        },
        {
            "phase": "discriminator",
            "type": "run_started",
            "slug": None,
            "data": {
                "mode": "global",
                "pass_number": 1,
                "run_id": 2,
                "attempt": 2,
                "stage_groups": ["Level 00 - Repo & System Health"],
            },
        },
        {
            "phase": "discriminator",
            "type": "stage_start",
            "slug": None,
            "data": {
                "mode": "global",
                "pass_number": 1,
                "run_id": 2,
                "attempt": 2,
                "identifier": "00.1",
                "description": "Git status",
                "group": "Level 00 - Repo & System Health",
                "command": "git status -sb",
            },
        },
        {
            "phase": "discriminator",
            "type": "stage_end",
            "slug": None,
            "data": {
                "mode": "global",
                "pass_number": 1,
                "run_id": 2,
                "attempt": 2,
                "identifier": "00.1",
                "description": "Git status",
                "group": "Level 00 - Repo & System Health",
                "command": "git status -sb",
                "ok": False,
                "elapsed": 0.34,
                "tail": "boom",
                "failure_reason": "boom",
            },
        },
        {
            "phase": "discriminator",
            "type": "coverage_update",
            "slug": None,
            "data": {
                "mode": "global",
                "pass_number": 1,
                "run_id": 2,
                "attempt": 2,
                "identifier": "04.1",
                "percent": 72.0,
                "threshold": "80",
                "targets": ["src"],
            },
        },
        {
            "phase": "discriminator",
            "type": "llm_patch_decision",
            "slug": None,
            "data": {
                "mode": "global",
                "pass_number": 1,
                "run_id": 2,
                "attempt": 2,
                "accepted": False,
                "reason": "no_diff",
                "next_run_id": 3,
            },
        },
        {
            "phase": "discriminator",
            "type": "run_completed",
            "slug": None,
            "data": {
                "mode": "global",
                "pass_number": 1,
                "run_id": 2,
                "attempt": 2,
                "ok": False,
            },
        },
    ]
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join(json.dumps(event) for event in events))
    output = discriminator_snapshot_text(None, path)
    assert "run 2" in output
    assert "Result: FAIL" in output
    assert "run 1" not in output
    assert "72%" in output
    assert "boom" in output
