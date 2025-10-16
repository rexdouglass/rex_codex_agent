from __future__ import annotations

import json

from rex_codex.hud import generator_snapshot_text


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
