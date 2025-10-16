from __future__ import annotations

from rex_codex.generator_ui import GeneratorHUDModel


def _event(type_: str, **data):
    return {"phase": "generator", "type": type_, "slug": "hello", "data": data}


def test_model_acceptance_mapping_and_rendering() -> None:
    model = GeneratorHUDModel("hello")
    model.apply_event(
        _event(
            "feature_started",
            title="Hello CLI",
            status="proposed",
            summary="Command-line greeting",
            acceptance=["Handle --message flag", "Respect --quiet toggle"],
            passes=3,
            focus="Cover quiet negatives",
        )
    )
    model.apply_event(
        _event(
            "spec_trace_update",
            coverage={
                "entries": [
                    {
                        "index": 1,
                        "text": "Handle --message flag",
                        "tests": ["tests/feature_specs/hello_cli/test_cli_basic.py::test_flag"],
                    },
                    {"index": 2, "text": "Respect --quiet toggle", "tests": []},
                ],
                "missing": [{"index": 2, "text": "Respect --quiet toggle", "tests": []}],
                "orphans": ["tests/feature_specs/hello_cli/test_extra.py::test_unused"],
            },
        )
    )
    model.apply_event(_event("iteration_started", iteration=1, total_passes=3))
    model.apply_event(_event("codex_started"))
    model.apply_event(
        _event(
            "diff_summary",
            files=[{"path": "tests/feature_specs/hello_cli/test_cli_basic.py", "added": 12, "removed": 0}],
            totals={"files": 1, "added_lines": 12, "removed_lines": 0},
        )
    )
    model.apply_event(_event("pytest_snapshot", status="failed", output="AssertionError: message"))
    model.apply_event(_event("critic_guidance", done=False, guidance="TODO: add negative case"))
    model.apply_event(_event("iteration_completed", elapsed_seconds=18.0, exit_code=0))

    output = model.render(iteration_elapsed=12.0, codex_elapsed=8.0)
    assert "Hello CLI" in output
    assert "[*]" in output  # covered acceptance criterion
    assert "(missing)" in output  # missing acceptance mapping
    assert "Iteration     : 1/3" in output
    assert "avg 18s" in output
    assert "Pytest shard  : Failed" in output
    assert "Critic        : TODO: add negative case" in output
    assert "Orphan tests" in output
    assert "Focus: Cover quiet negatives" in output
