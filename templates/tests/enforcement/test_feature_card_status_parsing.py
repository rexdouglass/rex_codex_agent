from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

CARD = Path("documents/feature_cards/_status_parse_demo.md")


def _write(contents: str) -> None:
    CARD.parent.mkdir(parents=True, exist_ok=True)
    CARD.write_text(textwrap.dedent(contents), encoding="utf-8")


def _call(command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-lc", command],
        capture_output=True,
        text=True,
        check=False,
    )


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
    rex_src = os.environ.get("REX_SRC")
    if not rex_src:
        pytest.skip("REX_SRC not exported; generator helper unavailable outside rex-codex executor")

    _write(f"{line}\n\nTitle: Demo\n")
    command = f"source \"{rex_src}/lib/generator.sh\"; generator_card_status {CARD}"
    result = _call(command)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected
