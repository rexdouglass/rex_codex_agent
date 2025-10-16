"""Feature card helpers."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .utils import (
    RexContext,
    dump_json,
    ensure_dir,
    load_json,
    prompt,
    repo_root,
    run,
)


CARD_DIR = Path("documents/feature_cards")
CARD_FILENAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
STATUS_RE = re.compile(r"^[ \t]*status:[ \t]*([A-Za-z0-9_.-]+)", re.IGNORECASE)
SPEC_ROOT = Path("tests/feature_specs")


def card_path_for(context: RexContext, slug: str) -> Path:
    return card_directory(context) / f"{slug}.md"


def read_card_sections(path: Path) -> Dict[str, object]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    title = path.stem.replace("-", " ").title()
    for line in lines:
        if line.startswith("# "):
            title = line[2:].strip()
            break
    current_section: Optional[str] = None
    summary_lines: List[str] = []
    acceptance: List[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            current_section = stripped.lower()
            continue
        if current_section == "## summary":
            summary_lines.append(line.rstrip())
        elif current_section == "## acceptance criteria":
            if stripped.startswith("- "):
                acceptance.append(stripped[2:].strip())
    summary = "\n".join([line for line in summary_lines if line.strip()]).strip()
    return {"title": title, "summary": summary, "acceptance": acceptance}


def spec_directory(context: RexContext, slug: str) -> Path:
    return context.root / SPEC_ROOT / slug


def card_content_hash(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(8192), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _list_test_functions(path: Path) -> List[str]:
    try:
        source = path.read_text(encoding="utf-8")
    except OSError:
        return []
    import ast

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []
    names: List[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            names.append(node.name)
    return names


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
    headers = [ln.strip() for ln in text if ln.strip().startswith("## ")]
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


def rename_card(context: RexContext, old_slug: str, new_slug: str) -> FeatureCard:
    validate_slug(new_slug)
    directory = card_directory(context)
    old_path = directory / f"{old_slug}.md"
    if not old_path.exists():
        raise FileNotFoundError(f"Feature Card not found: {old_path}")
    new_path = directory / f"{new_slug}.md"
    if new_path.exists():
        raise FileExistsError(f"Target Feature Card already exists: {new_path}")

    ensure_dir(new_path.parent)
    old_path.rename(new_path)

    old_spec = spec_directory(context, old_slug)
    new_spec = spec_directory(context, new_slug)
    if old_spec.exists():
        ensure_dir(new_spec.parent)
        if new_spec.exists():
            raise FileExistsError(f"Target spec directory already exists: {new_spec}")
        old_spec.rename(new_spec)

    data = load_json(context.rex_agent_file)
    feature = data.setdefault("feature", {})
    if feature.get("active_slug") == old_slug:
        feature["active_slug"] = new_slug
        feature["active_card"] = str(new_path.relative_to(context.root))
    dump_json(context.rex_agent_file, data)

    return FeatureCard(path=new_path, slug=new_slug, status=read_status(new_path))


def archive_card(context: RexContext, slug: str, *, status: str = "archived") -> FeatureCard:
    path = card_path_for(context, slug)
    if not path.exists():
        raise FileNotFoundError(f"Feature Card not found: {path}")
    lines = path.read_text(encoding="utf-8").splitlines()
    replaced = False
    new_lines: List[str] = []
    for line in lines:
        if STATUS_RE.match(line):
            new_lines.append(f"status: {status}")
            replaced = True
        else:
            new_lines.append(line)
    if not replaced:
        raise ValueError(f"{path} does not contain a status line")
    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return FeatureCard(path=path, slug=slug, status=status)


def split_card(
    context: RexContext,
    source_slug: str,
    slug_a: str,
    slug_b: str,
) -> Tuple[FeatureCard, FeatureCard]:
    directory = card_directory(context)
    source_path = directory / f"{source_slug}.md"
    if not source_path.exists():
        raise FileNotFoundError(f"Feature Card not found: {source_path}")
    validate_slug(slug_a)
    validate_slug(slug_b)
    meta = read_card_sections(source_path)
    title = meta.get("title", slug_a.replace("-", " ").title())
    summary = meta.get("summary", "")
    acceptance = [str(item) for item in meta.get("acceptance", [])]

    card_a = create_card(context, slug=slug_a, title=title, summary=summary, acceptance=acceptance)
    card_b = create_card(context, slug=slug_b, title=title, summary=summary, acceptance=acceptance)

    source_spec = spec_directory(context, source_slug)
    if source_spec.exists():
        ensure_dir(spec_directory(context, slug_a))
        ensure_dir(spec_directory(context, slug_b))
        for path in sorted(source_spec.glob("*.py")):
            tests = _list_test_functions(path)
            test_display = ", ".join(tests) if tests else "(no tests discovered)"
            if not sys.stdin.isatty():
                choice = "k"
            else:
                prompt_msg = (
                    f"[card split] Move {path.relative_to(context.root)} "
                    f"(tests: {test_display}) to (a/b/k[eep]): "
                )
                raw = prompt(prompt_msg)
                choice = raw.strip().lower()[:1] if raw else "k"
                if choice not in {"a", "b"}:
                    choice = "k"
            if choice == "a":
                dest = spec_directory(context, slug_a) / path.name
            elif choice == "b":
                dest = spec_directory(context, slug_b) / path.name
            else:
                continue
            if dest.exists():
                raise FileExistsError(f"Destination already contains {dest}")
            shutil.move(str(path), str(dest))
            print(f"[card split] Moved {path.relative_to(context.root)} → {dest.relative_to(context.root)}")
        # Remove the source directory if empty after moves
        if not any(source_spec.iterdir()):
            source_spec.rmdir()

    return card_a, card_b


def _git_path_dirty(context: RexContext, path: Path) -> bool:
    completed = run(
        ["git", "status", "--short", "--", str(path)],
        cwd=context.root,
        capture_output=True,
        check=False,
    )
    return bool((completed.stdout or "").strip())


def prune_spec_directories(
    context: RexContext,
    *,
    include_archived: bool = True,
    assume_yes: bool = False,
) -> List[Path]:
    specs_root = context.root / SPEC_ROOT
    if not specs_root.exists():
        return []
    cards = {card.slug: card.status for card in discover_cards(context=context)}
    targets: List[Path] = []
    for path in sorted(specs_root.iterdir()):
        if not path.is_dir():
            continue
        slug = path.name
        if slug not in cards:
            targets.append(path)
            continue
        if include_archived and cards[slug].lower() == "archived":
            targets.append(path)
    removed: List[Path] = []
    for path in targets:
        rel = path.relative_to(context.root)
        if _git_path_dirty(context, path):
            print(f"[card prune-specs] Skipping {rel} (git reports modifications).")
            continue
        if not assume_yes:
            response = prompt(f"[card prune-specs] Delete {rel}? [y/N]: ").strip().lower()
            if response not in {"y", "yes"}:
                continue
        shutil.rmtree(path)
        removed.append(path)
        print(f"[card prune-specs] Removed {rel}")
    return removed


def find_orphan_spec_slugs(context: RexContext) -> List[str]:
    specs_root = context.root / SPEC_ROOT
    if not specs_root.exists():
        return []
    existing = {card.slug for card in discover_cards(context=context)}
    orphans: List[str] = []
    for path in sorted(specs_root.iterdir()):
        if not path.is_dir():
            continue
        slug = path.name
        if slug not in existing:
            orphans.append(slug)
    return orphans
