from __future__ import annotations

import textwrap
from pathlib import Path
from types import SimpleNamespace

from rex_codex.cards import FeatureCard
from rex_codex.generator import _build_spec_trace_result, _spec_trace_payload


def test_spec_trace_section_lines_include_indices(tmp_path: Path) -> None:
    card_path = tmp_path / "documents" / "feature_cards" / "demo.md"
    card_path.parent.mkdir(parents=True, exist_ok=True)
    card_path.write_text(
        """
        status: proposed

        # Demo

        ## Summary

        Demo summary

        ## Acceptance Criteria

        - happy path
        - error path

        ## Spec Trace

        """,
        encoding="utf-8",
    )

    specs_dir = tmp_path / "tests" / "feature_specs" / "demo"
    specs_dir.mkdir(parents=True, exist_ok=True)
    test_source = textwrap.dedent(
        '''
        import pytest


        def test_happy():
            """AC#1 covers happy path"""
            assert True
        '''
    )
    (specs_dir / "test_demo.py").write_text(test_source, encoding="utf-8")

    card = FeatureCard(card_path, "demo", "proposed")
    context = SimpleNamespace(root=tmp_path)
    result = _build_spec_trace_result(card=card, slug="demo", context=context)
    assert result is not None
    assert any(line.startswith("  -> [AC#1]") for line in result.section_lines)
    assert any(
        line.strip() == "-> [AC#2] (missing)"
        for line in (line.strip() for line in result.section_lines)
    )

    payload = _spec_trace_payload(result)
    entries = payload["entries"]
    assert entries[0]["status"] == "covered"
    assert entries[1]["status"] == "missing"
    assert entries[0]["tests"] == ["tests/feature_specs/demo/test_demo.py::test_happy"]
    assert payload["missing"][0]["status"] == "missing"
