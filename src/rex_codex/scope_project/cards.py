"""Feature card helpers."""

from __future__ import annotations

import hashlib
import re
import shutil
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .utils import RexContext, dump_json, ensure_dir, load_json, prompt, repo_root, run

CARD_DIR = Path("documents/feature_cards")
CARD_FILENAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
STATUS_RE = re.compile(r"^[ \t]*status:[ \t]*([A-Za-z0-9_.-]+)", re.IGNORECASE)
SPEC_ROOT = Path("tests/feature_specs")
REQUIRED_HEADERS = ("## Summary", "## Acceptance Criteria", "## Links", "## Spec Trace")


def card_path_for(context: RexContext, slug: str) -> Path:
    return card_directory(context) / f"{slug}.md"


def read_card_sections(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    title = path.stem.replace("-", " ").title()
    for line in lines:
        if line.startswith("# "):
            title = line[2:].strip()
            break
    current_section: str | None = None
    summary_lines: list[str] = []
    acceptance: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            current_section = stripped.lower()
            continue
        if current_section == "## summary":
            if stripped:
                if not stripped.startswith("- "):
                    raise ValueError("Summary bullets must start with '- '.")
                summary_lines.append(line.rstrip())
        elif current_section == "## acceptance criteria":
            if stripped:
                if not stripped.startswith("- "):
                    raise ValueError("Acceptance Criteria bullets must start with '- '.")
                acceptance.append(stripped[2:].strip())
    summary = "\n".join([line for line in summary_lines if line.strip()]).strip()
    return {"title": title, "summary": summary, "acceptance": acceptance}


def spec_directory(context: RexContext, slug: str) -> Path:
    return context.root / SPEC_ROOT / slug


def card_content_hash(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(8192), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _list_test_functions(path: Path) -> list[str]:
    try:
        source = path.read_text(encoding="utf-8")
    except OSError:
        return []
    import ast

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []
    names: list[str] = []
    for node in tree.body:
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef)
        ) and node.name.startswith("test"):
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


@dataclass(frozen=True)
class CardLintIssue:
    path: Path
    code: str
    message: str
    line: int = 1
    column: int = 1
    hint: str | None = None

    def describe(self) -> str:
        location = f"{self.path}:{self.line}:{self.column}"
        detail = f"{location} {self.code}: {self.message}"
        if self.hint:
            return f"{detail} ({self.hint})"
        return detail

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "path": str(self.path),
            "code": self.code,
            "message": self.message,
            "line": self.line,
            "column": self.column,
        }
        if self.hint:
            payload["hint"] = self.hint
        return payload


@dataclass(frozen=True)
class CardFixReport:
    slug: str
    path: Path
    changed: bool
    before: list[CardLintIssue]
    after: list[CardLintIssue]

    def to_dict(self) -> dict[str, object]:
        return {
            "slug": self.slug,
            "path": str(self.path),
            "changed": self.changed,
            "issues_before": [issue.to_dict() for issue in self.before],
            "issues_after": [issue.to_dict() for issue in self.after],
        }


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
) -> list[FeatureCard]:
    context = context or RexContext.discover()
    directory = card_directory(context)
    if not directory.exists():
        return []
    normalized_statuses = {s.lower() for s in (statuses or [])}
    matches: list[FeatureCard] = []
    for path in sorted(directory.glob("*.md")):
        slug = slug_from_filename(path)
        status = read_status(path)
        if normalized_statuses and status not in normalized_statuses:
            continue
        matches.append(FeatureCard(path, slug, status))
    return matches


def latest_card(statuses: Sequence[str] | None = None) -> FeatureCard | None:
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


def lint_card(path: Path) -> list[str]:
    return [issue.describe() for issue in collect_card_issues(path)]


def collect_card_issues(path: Path) -> list[CardLintIssue]:
    issues: list[CardLintIssue] = []
    if not path.exists():
        issues.append(
            CardLintIssue(
                path=path,
                code="CARD001",
                message="Feature Card file is missing",
                hint="Run `./rex-codex card new` to create it.",
            )
        )
        return issues
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        issues.append(
            CardLintIssue(
                path=path,
                code="CARD002",
                message=f"Unable to read Feature Card: {exc}",
            )
        )
        return issues

    status_entries: list[tuple[int, str]] = [
        (idx, line) for idx, line in enumerate(lines, start=1) if STATUS_RE.match(line)
    ]
    if not status_entries:
        issues.append(
            CardLintIssue(
                path=path,
                code="CARD100",
                message="missing `status:` line",
                hint="Add a leading line like `status: proposed`.",
            )
        )
    else:
        first_index, first_line = status_entries[0]
        match = STATUS_RE.match(first_line)
        value = match.group(1).strip() if match else ""
        if not value:
            issues.append(
                CardLintIssue(
                    path=path,
                    code="CARD101",
                    message="`status:` line is missing a value",
                    line=first_index,
                    hint="Set a status such as `proposed`, `accepted`, or `archived`.",
                )
            )
        first_non_empty = next(
            (idx for idx, line in enumerate(lines, start=1) if line.strip()), None
        )
        if first_non_empty is not None and first_index != first_non_empty:
            issues.append(
                CardLintIssue(
                    path=path,
                    code="CARD102",
                    message="`status:` should be the first non-empty line",
                    line=first_index,
                    hint="Move the status line to the top of the file.",
                )
            )
        if len(status_entries) > 1:
            for dup_index, _ in status_entries[1:]:
                issues.append(
                    CardLintIssue(
                        path=path,
                        code="CARD103",
                        message="Duplicate `status:` line",
                        line=dup_index,
                        hint="Remove additional status lines.",
                    )
                )

    headers = {
        line.strip(): idx
        for idx, line in enumerate(lines, start=1)
        if line.strip().startswith("## ")
    }
    for header in REQUIRED_HEADERS:
        if header not in headers:
            issues.append(
                CardLintIssue(
                    path=path,
                    code="CARD110",
                    message=f"Missing header {header!r}",
                    hint=f"Add a `{header}` section to the card.",
                )
            )

    current_section: str | None = None
    for idx, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("## "):
            current_section = stripped.lower()
            continue
        if current_section == "## acceptance criteria":
            if stripped and not stripped.startswith("- "):
                issues.append(
                    CardLintIssue(
                        path=path,
                        code="CARD120",
                        message="Acceptance criteria bullets must start with `- `",
                        line=idx,
                        hint="Prefix the line with `- `.",
                    )
                )

    return issues


def lint_all_cards(context: RexContext | None = None) -> list[str]:
    issues = collect_all_card_issues(context)
    if not issues:
        return []
    return [issue.describe() for issue in issues]


def collect_all_card_issues(
    context: RexContext | None = None,
    *,
    slugs: Iterable[str] | None = None,
) -> list[CardLintIssue]:
    context = context or RexContext.discover()
    directory = card_directory(context)
    if not directory.exists():
        missing_path = directory / "(missing)"
        return [
            CardLintIssue(
                path=missing_path,
                code="CARD000",
                message="No Feature Cards found; run `rex-codex card new` first.",
            )
        ]

    issues: list[CardLintIssue] = []
    if slugs:
        for slug in slugs:
            issues.extend(collect_card_issues(card_path_for(context, slug)))
        return issues

    for card in discover_cards(context=context):
        issues.extend(collect_card_issues(card.path))
    return issues


def fix_card(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        original_text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    lines = original_text.splitlines()
    changed = False

    status_indices = [idx for idx, line in enumerate(lines) if STATUS_RE.match(line)]
    if not status_indices:
        lines.insert(0, "status: proposed")
        lines.insert(1, "")
        changed = True
    else:
        first_idx = status_indices[0]
        match = STATUS_RE.match(lines[first_idx])
        value = match.group(1).strip().lower() if match and match.group(1) else "proposed"
        normalized_line = f"status: {value}"
        if lines[first_idx].strip() != normalized_line:
            lines[first_idx] = normalized_line
            changed = True
        # Remove duplicates
        for dup_idx in reversed(status_indices[1:]):
            del lines[dup_idx]
            changed = True
        # Move to top if needed
        if first_idx != 0:
            status_line = lines.pop(first_idx if first_idx < len(lines) else len(lines) - 1)
            lines.insert(0, status_line)
            changed = True
        if len(lines) < 2 or lines[1].strip():
            lines.insert(1, "")
            changed = True

    existing_headers = {line.strip() for line in lines if line.strip().startswith("## ")}
    for header in REQUIRED_HEADERS:
        if header not in existing_headers:
            if lines and lines[-1].strip():
                lines.append("")
            lines.append(header)
            lines.append("")
            changed = True

    current_section: str | None = None
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("## "):
            current_section = stripped.lower()
            continue
        if current_section == "## acceptance criteria" and stripped:
            if not stripped.startswith("- "):
                lines[idx] = f"- {stripped}"
                changed = True

    normalised = "\n".join(lines).rstrip() + "\n"
    if normalised != original_text:
        path.write_text(normalised, encoding="utf-8")
        return True
    return changed


def fix_cards(
    context: RexContext,
    *,
    slugs: Iterable[str] | None = None,
) -> list[CardFixReport]:
    reports: list[CardFixReport] = []
    if slugs:
        targets = [(slug, card_path_for(context, slug)) for slug in slugs]
    else:
        targets = [(card.slug, card.path) for card in discover_cards(context=context)]
    for slug, path in targets:
        before = collect_card_issues(path)
        changed = False
        if path.exists():
            changed = fix_card(path)
        after = collect_card_issues(path)
        reports.append(
            CardFixReport(
                slug=slug,
                path=path,
                changed=changed,
                before=before,
                after=after,
            )
        )
    return reports


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


def archive_card(
    context: RexContext, slug: str, *, status: str = "archived"
) -> FeatureCard:
    path = card_path_for(context, slug)
    if not path.exists():
        raise FileNotFoundError(f"Feature Card not found: {path}")
    lines = path.read_text(encoding="utf-8").splitlines()
    replaced = False
    new_lines: list[str] = []
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
) -> tuple[FeatureCard, FeatureCard]:
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

    card_a = create_card(
        context, slug=slug_a, title=title, summary=summary, acceptance=acceptance
    )
    card_b = create_card(
        context, slug=slug_b, title=title, summary=summary, acceptance=acceptance
    )

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
            print(
                f"[card split] Moved {path.relative_to(context.root)} → {dest.relative_to(context.root)}"
            )
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
) -> list[Path]:
    specs_root = context.root / SPEC_ROOT
    if not specs_root.exists():
        return []
    cards = {card.slug: card.status for card in discover_cards(context=context)}
    targets: list[Path] = []
    for path in sorted(specs_root.iterdir()):
        if not path.is_dir():
            continue
        slug = path.name
        if slug not in cards:
            targets.append(path)
            continue
        if include_archived and cards[slug].lower() == "archived":
            targets.append(path)
    removed: list[Path] = []
    for path in targets:
        rel = path.relative_to(context.root)
        if _git_path_dirty(context, path):
            print(f"[card prune-specs] Skipping {rel} (git reports modifications).")
            continue
        if not assume_yes:
            response = (
                prompt(f"[card prune-specs] Delete {rel}? [y/N]: ").strip().lower()
            )
            if response not in {"y", "yes"}:
                continue
        shutil.rmtree(path)
        removed.append(path)
        print(f"[card prune-specs] Removed {rel}")
    return removed


def find_orphan_spec_slugs(context: RexContext) -> list[str]:
    specs_root = context.root / SPEC_ROOT
    if not specs_root.exists():
        return []
    existing = {card.slug for card in discover_cards(context=context)}
    orphans: list[str] = []
    for path in sorted(specs_root.iterdir()):
        if not path.is_dir():
            continue
        slug = path.name
        if slug not in existing:
            orphans.append(slug)
    return orphans
