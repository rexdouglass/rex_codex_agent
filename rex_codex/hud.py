"""One-shot HUD renderer for use with `watch` or CI snapshots."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Optional

from .cards import latest_card
from .events import events_path as default_events_path
from .generator_ui import GeneratorHUDModel
from .utils import RexContext


class _HUDPrinter:
    def __init__(self, *, width: int = 100) -> None:
        self.width = width

    def divider(self, title: str) -> str:
        title = title.strip()
        pad = max(0, self.width - len(title) - 4)
        return f"{title} {'-' * pad}"


def _load_events(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return []
    results = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            results.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return results


def _resolve_slug(slug: Optional[str], *, context: RexContext) -> Optional[str]:
    if slug:
        return slug
    card = latest_card()
    return card.slug if card else None


def render_generator_snapshot(
    *,
    slug: str,
    events: Iterable[dict[str, Any]],
    printer: _HUDPrinter,
) -> str:
    model = GeneratorHUDModel(slug)
    relevant = [
        event for event in events if event.get("slug") in (slug, None)
    ]
    start_index = 0
    for idx, event in enumerate(reversed(relevant)):
        if event.get("type") == "feature_started" and event.get("slug") == slug:
            start_index = len(relevant) - idx - 1
            break
    for event in relevant[start_index:]:
        model.apply_event(event)
    snapshot = model.render(iteration_elapsed=None, codex_elapsed=None)
    header = printer.divider(f"Generator HUD :: {slug}")
    return f"{header}\n{snapshot}\n"


def generator_snapshot_text(slug: str, path: Path) -> str:
    events = _load_events(path)
    if not events:
        return ""
    printer = _HUDPrinter()
    return render_generator_snapshot(slug=slug, events=events, printer=printer)


def render_hud(
    *,
    phase: str,
    slug: Optional[str],
    events_file: Optional[str],
    context: RexContext,
) -> None:
    if phase != "generator":
        raise SystemExit(f"[hud] Unsupported phase: {phase}")
    resolved_slug = _resolve_slug(slug, context=context)
    if not resolved_slug:
        print("[hud] No feature slug provided and no active card detected.")
        raise SystemExit(1)
    path = Path(events_file).expanduser() if events_file else default_events_path()
    snapshot = generator_snapshot_text(resolved_slug, path)
    if not snapshot:
        print(f"[hud] No events recorded yet at {path}.")
        raise SystemExit(1)
    print(snapshot, end="")
