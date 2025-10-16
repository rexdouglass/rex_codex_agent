from __future__ import annotations

import textwrap

import pytest
from rex_codex.cards import read_status


@pytest.mark.unit
@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("status: proposed", "proposed"),
        (" Status: Accepted", "accepted"),
        ("\tstatus:REVIEW", "review"),
    ],
)
def test_generator_card_status_respects_whitespace_and_case(
    tmp_path, line: str, expected: str
) -> None:
    card = tmp_path / "demo_card.md"
    card.write_text(
        textwrap.dedent(
            f"""
            {line}

            # Demo
            """
        ),
        encoding="utf-8",
    )
    assert read_status(card) == expected
