from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from rex_codex.scope_project import cards
from rex_codex.scope_project.utils import RexContext


@pytest.fixture()
def context(tmp_path: Path) -> RexContext:
    root = tmp_path
    codex_ci = root / ".codex_ci"
    monitor_logs = root / ".agent" / "logs"
    codex_ci.mkdir(parents=True, exist_ok=True)
    monitor_logs.mkdir(parents=True, exist_ok=True)
    return RexContext(
        root=root,
        codex_ci_dir=codex_ci,
        monitor_log_dir=monitor_logs,
        rex_agent_file=root / "rex-agent.json",
        venv_dir=root / ".venv",
    )


def test_collect_and_fix_card(tmp_path: Path, context: RexContext) -> None:
    card_dir = tmp_path / "documents" / "feature_cards"
    card_dir.mkdir(parents=True, exist_ok=True)
    card_path = card_dir / "sample.md"
    card_path.write_text(
        textwrap.dedent(
            """
            # Sample Feature

            ## Summary

            Something descriptive.

            ## Acceptance Criteria

            This line should be a bullet
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    issues = cards.collect_card_issues(card_path)
    codes = {issue.code for issue in issues}
    assert {"CARD100", "CARD110", "CARD120"}.issubset(codes)

    changed = cards.fix_card(card_path)
    assert changed is True

    fixed_content = card_path.read_text(encoding="utf-8")
    assert fixed_content.startswith("status: proposed")
    assert "- This line should be a bullet" in fixed_content
    assert "## Links" in fixed_content
    assert "## Spec Trace" in fixed_content

    remaining = cards.collect_card_issues(card_path)
    assert remaining == []

    reports = cards.fix_cards(context, slugs=["sample"])
    assert len(reports) == 1
    report = reports[0]
    assert report.slug == "sample"
    assert report.changed is False  # already fixed
    assert report.before == []
    assert report.after == []


def test_collect_issues_for_missing_card(context: RexContext) -> None:
    (context.root / "documents" / "feature_cards").mkdir(parents=True, exist_ok=True)
    issues = cards.collect_all_card_issues(context, slugs=["missing-feature"])
    assert issues
    assert issues[0].code == "CARD001"
