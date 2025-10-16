"""Helpers to surface rex-agent.json metadata."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Dict, Iterable

from .cards import FeatureCard, card_content_hash, card_path_for, discover_cards, load_rex_agent
from .utils import RexContext


def _format_timestamp(value: str | None) -> str:
    if not value:
        return "unknown"
    try:
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        when = dt.datetime.fromisoformat(normalized)
    except ValueError:
        return value
    return when.isoformat(timespec="seconds")


def summarize_context(context: RexContext) -> Dict[str, Any]:
    data = load_rex_agent(context)
    feature = data.get("feature", {})
    active_slug = feature.get("active_slug")
    active_card_path = feature.get("active_card")
    active_card: FeatureCard | None = None
    card_path: Path | None = None

    if active_card_path:
        path = (context.root / active_card_path).resolve()
        if path.exists():
            active_card = FeatureCard(path=path, slug=active_slug or path.stem, status="")
            card_path = path
    else:
        cards = discover_cards(context=context, statuses=["proposed"])
        active_card = cards[0] if cards else None
        if active_card is not None:
            candidate = card_path_for(context, active_card.slug)
            if candidate.exists():
                card_path = candidate

    if card_path is None and active_slug:
        candidate = card_path_for(context, active_slug)
        if candidate.exists():
            card_path = candidate

    card_hashes = feature.get("card_hashes") if isinstance(feature.get("card_hashes"), dict) else {}
    stored_hash = card_hashes.get(active_slug) if isinstance(card_hashes, dict) else None
    current_hash = card_content_hash(card_path) if card_path else None
    hash_drift = bool(stored_hash and current_hash and stored_hash != current_hash)

    discriminator_state = data.get("discriminator", {})

    return {
        "active_slug": active_slug or (active_card.slug if active_card else None),
        "active_card": active_card_path or (str(active_card.relative_path) if active_card else None),
        "stages": data.get("stages"),
        "llm": data.get("llm"),
        "feature": {
            "active_card": active_card_path,
            "active_slug": active_slug,
            "updated_at": _format_timestamp(feature.get("updated_at")),
            "stored_hash": stored_hash,
            "current_hash": current_hash,
            "hash_drift": hash_drift,
        },
        "discriminator": {
            "last_mode": discriminator_state.get("last_mode"),
            "last_slug": discriminator_state.get("last_slug"),
            "last_green_at": _format_timestamp(discriminator_state.get("last_green_at")),
            "last_test_count": discriminator_state.get("last_test_count"),
        },
    }


def render_status(context: RexContext, *, json_output: bool = False) -> None:
    summary = summarize_context(context)
    if json_output:
        print(json.dumps(summary, indent=2, sort_keys=True))
        return
    print("Active Feature:")
    print(f"  slug: {summary.get('active_slug') or 'none'}")
    print(f"  card: {summary.get('active_card') or 'none'}")
    feature = summary.get("feature", {})
    print(f"  updated_at: {feature.get('updated_at')}")
    stored_hash = feature.get("stored_hash")
    current_hash = feature.get("current_hash")
    if stored_hash or current_hash:
        print(f"  stored_hash: {stored_hash or 'none'}")
        print(f"  current_hash: {current_hash or 'none'}")
        drift = "YES" if feature.get("hash_drift") else "no"
        print(f"  hash_drift: {drift}")
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
    discriminator = summary.get("discriminator")
    if isinstance(discriminator, dict):
        print("Discriminator:")
        print(f"  last_mode: {discriminator.get('last_mode') or 'unknown'}")
        print(f"  last_slug: {discriminator.get('last_slug') or 'none'}")
        print(f"  last_green_at: {discriminator.get('last_green_at')}")
        if discriminator.get("last_test_count") is not None:
            print(f"  last_test_count: {discriminator.get('last_test_count')}")
