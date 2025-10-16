from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from rex_codex.generator import _guard_card_edits


def _write_card(path: Path, body: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path.read_text(encoding="utf-8")


@pytest.fixture()
def card_path(tmp_path: Path) -> Path:
    return tmp_path / "documents" / "feature_cards" / "demo.md"


def test_guard_card_edits_allows_links_append(card_path: Path) -> None:
    baseline = _write_card(
        card_path,
        """
        status: proposed

        # Demo

        ## Summary

        Initial summary.

        ## Acceptance Criteria

        - behaviour holds

        ## Links

        ## Spec Trace

        """,
    )

    card_path.write_text(
        baseline.replace(
            "## Links\n\n",
            "## Links\n\n- https://example.test/demo\n\n",
        ),
        encoding="utf-8",
    )

    assert _guard_card_edits("demo", card_path.parents[2], baseline)


def test_guard_card_edits_rejects_status_change(card_path: Path) -> None:
    baseline = _write_card(
        card_path,
        """
        status: proposed

        # Demo

        ## Summary

        Initial summary.

        ## Acceptance Criteria

        - behaviour holds

        ## Links

        ## Spec Trace

        """,
    )

    card_path.write_text(
        baseline.replace("status: proposed", "status: accepted"), encoding="utf-8"
    )

    assert not _guard_card_edits("demo", card_path.parents[2], baseline)


def test_guard_card_edits_rejects_non_allowed_section(card_path: Path) -> None:
    baseline = _write_card(
        card_path,
        """
        status: proposed

        # Demo

        ## Summary

        Initial summary.

        ## Acceptance Criteria

        - behaviour holds

        ## Links

        ## Spec Trace

        """,
    )

    card_path.write_text(
        baseline.replace(
            "Initial summary.",
            "Initial summary.\nExtra summary line added by mistake.",
        ),
        encoding="utf-8",
    )

    assert not _guard_card_edits("demo", card_path.parents[2], baseline)
