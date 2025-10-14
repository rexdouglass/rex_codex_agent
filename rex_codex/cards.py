"""Feature card helpers."""

from __future__ import annotations

from datetime import UTC, datetime
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

from .utils import RexContext, dump_json, load_json, repo_root


CARD_DIR = Path("documents/feature_cards")
CARD_FILENAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
STATUS_RE = re.compile(r"^[ \t]*status:[ \t]*([A-Za-z0-9_.-]+)", re.IGNORECASE)


@dataclass
class FeatureCard:
    path: Path
    slug: str
    status: str

    @property
    def relative_path(self) -> Path:
        root = repo_root()
        try:
            return self.path.relative_to(root)
        except ValueError:
            return self.path


def card_directory(context: RexContext | None = None) -> Path:
    context = context or RexContext.discover()
    return context.root / CARD_DIR


def slug_from_filename(path: Path) -> str:
    stem = path.stem.lower()
    return stem


def read_status(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "missing"
    for line in text.splitlines():
        match = STATUS_RE.match(line)
        if match:
            return match.group(1).lower()
    return "unknown"


def discover_cards(
    statuses: Iterable[str] | None = None,
    *,
    context: RexContext | None = None,
) -> List[FeatureCard]:
    context = context or RexContext.discover()
    directory = card_directory(context)
    if not directory.exists():
        return []
    normalized_statuses = {s.lower() for s in (statuses or [])}
    matches: List[FeatureCard] = []
    for path in sorted(directory.glob("*.md")):
        slug = slug_from_filename(path)
        status = read_status(path)
        if normalized_statuses and status not in normalized_statuses:
            continue
        matches.append(FeatureCard(path, slug, status))
    return matches


def latest_card(statuses: Sequence[str] | None = None) -> Optional[FeatureCard]:
    cards = discover_cards(statuses)
    return cards[0] if cards else None


def load_rex_agent(context: RexContext | None = None) -> dict:
    context = context or RexContext.discover()
    return load_json(context.rex_agent_file)


def update_active_card(context: RexContext, *, card: FeatureCard | None) -> None:
    data = load_json(context.rex_agent_file)
    feature = data.setdefault("feature", {})
    if card:
        feature["active_card"] = str(card.relative_path)
        feature["active_slug"] = card.slug
    else:
        feature["active_card"] = None
        feature["active_slug"] = None
    dump_json(context.rex_agent_file, data)


def sanitise_slug(raw: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", raw.lower())
    slug = re.sub(r"-{2,}", "-", slug)
    slug = slug.strip("-_")
    slug = re.sub(r"^[^a-z0-9]+", "", slug)
    if not slug:
        slug = f"feature-{datetime.now(UTC):%Y%m%d%H%M%S}"
    return slug


def validate_slug(slug: str) -> None:
    if not slug:
        raise ValueError("slug cannot be empty")
    if not CARD_FILENAME_RE.match(slug):
        raise ValueError(
            "slug must contain lowercase letters, digits, hyphen, or underscore; "
            f"got {slug!r}"
        )


def create_card(
    context: RexContext,
    *,
    slug: str,
    title: str,
    summary: str,
    acceptance: Sequence[str],
) -> FeatureCard:
    validate_slug(slug)
    directory = card_directory(context)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{slug}.md"
    if path.exists():
        raise FileExistsError(f"Feature Card already exists: {path}")
    body_lines = [
        "status: proposed",
        "",
        f"# {title.strip()}",
        "",
        "## Summary",
        "",
        summary.strip(),
        "",
        "## Acceptance Criteria",
    ]
    if acceptance:
        body_lines.append("")
        for item in acceptance:
            item = item.strip()
            if not item:
                continue
            if not item.startswith("- "):
                body_lines.append(f"- {item}")
            else:
                body_lines.append(item)
    else:
        body_lines.append("")
        body_lines.append("- TBD")
    body_lines.extend(
        [
            "",
            "## Links",
            "",
            "## Spec Trace",
            "",
        ]
    )
    path.write_text("\n".join(body_lines) + "\n", encoding="utf-8")
    card = FeatureCard(path=path, slug=slug, status="proposed")
    update_active_card(context, card=card)
    return card


def lint_card(path: Path) -> List[str]:
    errors: List[str] = []
    if not path.exists():
        return [f"{path}: missing file"]
    text = path.read_text(encoding="utf-8").splitlines()
    status_lines = [ln for ln in text if ln.lower().startswith("status:")]
    if not status_lines:
        errors.append(f"{path}: missing `status:` line")
    elif len(status_lines) > 1:
        errors.append(f"{path}: more than one `status:` line detected")
    headers = [ln.strip() for ln in text if ln.startswith("## ")]
    expected = {"## Summary", "## Acceptance Criteria", "## Links", "## Spec Trace"}
    missing = expected.difference(headers)
    for header in sorted(missing):
        errors.append(f"{path}: missing header {header!r}")
    return errors


def lint_all_cards(context: RexContext | None = None) -> List[str]:
    context = context or RexContext.discover()
    directory = card_directory(context)
    errors: List[str] = []
    if not directory.exists():
        return ["No Feature Cards found; run `rex-codex card new` first."]
    for card in discover_cards(context=context):
        errors.extend(lint_card(card.path))
    return errors
