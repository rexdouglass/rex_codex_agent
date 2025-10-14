from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from rex_codex.cards import read_status

CARD = Path("documents/feature_cards/_status_parse_demo.md")


def _write(contents: str) -> None:
    CARD.parent.mkdir(parents=True, exist_ok=True)
    CARD.write_text(textwrap.dedent(contents), encoding="utf-8")


@pytest.mark.unit
@pytest.mark.parametrize(
    "line, expected",
    [
        ("status: proposed", "proposed"),
        (" Status: Accepted", "accepted"),
        ("\tstatus:REVIEW", "review"),
    ],
)
def test_generator_card_status_respects_whitespace_and_case(line: str, expected: str) -> None:
    _write(f"{line}\n\nTitle: Demo\n")
    assert read_status(CARD) == expected
