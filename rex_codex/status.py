"""Helpers to surface rex-agent.json metadata."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, Dict, Iterable

from .cards import FeatureCard, discover_cards, load_rex_agent
from .utils import RexContext


def _format_timestamp(value: str | None) -> str:
    if not value:
        return "unknown"
    try:
        when = dt.datetime.fromisoformat(value)
    except ValueError:
        return value
    return when.isoformat(timespec="seconds")


def summarize_context(context: RexContext) -> Dict[str, Any]:
    data = load_rex_agent(context)
    feature = data.get("feature", {})
    active_slug = feature.get("active_slug")
    active_card_path = feature.get("active_card")
    active_card: FeatureCard | None = None

    if active_card_path:
        path = (context.root / active_card_path).resolve()
        if path.exists():
            active_card = FeatureCard(path=path, slug=active_slug or path.stem, status="")
    else:
        cards = discover_cards(context=context, statuses=["proposed"])
        active_card = cards[0] if cards else None

    return {
        "active_slug": active_slug or (active_card.slug if active_card else None),
        "active_card": active_card_path or (str(active_card.relative_path) if active_card else None),
        "stages": data.get("stages"),
        "llm": data.get("llm"),
        "feature": {
            "active_card": active_card_path,
            "active_slug": active_slug,
            "updated_at": _format_timestamp(feature.get("updated_at")),
        },
    }


def render_status(context: RexContext) -> None:
    summary = summarize_context(context)
    print("Active Feature:")
    print(f"  slug: {summary.get('active_slug') or 'none'}")
    print(f"  card: {summary.get('active_card') or 'none'}")
    feature = summary.get("feature", {})
    print(f"  updated_at: {feature.get('updated_at')}")
    stages = summary.get("stages")
    if isinstance(stages, Iterable):
        print("Configured Stages:")
        for stage in stages:
            print(f"  - {stage}")
    llm = summary.get("llm")
    if isinstance(llm, dict):
        print("LLM Settings:")
        for key, value in llm.items():
            print(f"  {key}: {value}")

