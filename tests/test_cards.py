from __future__ import annotations

import re

from rex_codex.cards import sanitise_slug


def test_sanitise_slug_strips_leading_invalid_characters() -> None:
    assert sanitise_slug("🔥 Feature!") == "feature"


def test_sanitise_slug_collapses_weird_spacing() -> None:
    assert sanitise_slug("  --My Feature  ") == "my-feature"


def test_sanitise_slug_fallback_when_empty() -> None:
    slug = sanitise_slug("___")
    assert re.fullmatch(r"feature-\d{14}", slug)
