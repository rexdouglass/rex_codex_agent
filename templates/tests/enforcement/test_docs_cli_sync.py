from __future__ import annotations

from pathlib import Path

import pytest

README = Path("README.md")
OPS = Path("AGENTS.md")


@pytest.mark.unit
def test_readme_mentions_loop_and_discriminator_flags() -> None:
    text = README.read_text(encoding="utf-8", errors="replace")
    assert "./rex-codex loop" in text
    assert "discriminator --feature-only" in text
    assert "discriminator --global" in text


@pytest.mark.unit
def test_operations_guide_mentions_logs_and_guardrails() -> None:
    text = OPS.read_text(encoding="utf-8", errors="replace").lower()
    assert "logs" in text and ".codex_ci" in text
    assert "guardrail" in text or "guardrails" in text
