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
