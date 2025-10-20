"""Deterministic spec generator implemented in Python."""

from __future__ import annotations

import ast
import difflib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import textwrap
import time
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .cards import FeatureCard, discover_cards, update_active_card
from .component_planner import ensure_component_plan
from .config import AGENT_SRC, DEFAULT_GENERATOR_MAX_FILES, DEFAULT_GENERATOR_MAX_LINES
from .events import emit_event, events_path
from .generator_ui import GeneratorHUD
from .hud import generator_snapshot_text
from .loop_state import register_loop_process, unregister_loop_process
from .monitoring import ensure_monitor_server
from .playbook import build_playbook_artifacts
from .self_update import self_update
from .utils import (
    RexContext,
    activate_venv,
    dump_json,
    ensure_dir,
    ensure_python,
    ensure_requirements_installed,
    load_json,
    lock_file,
    repo_root,
    run,
    shlex_join,
    update_llm_settings,
    which,
)

PROGRESS_INTERVAL_SECONDS = max(
    5, int(os.environ.get("GENERATOR_PROGRESS_SECONDS", "15"))
)

GENERATOR_EXIT_CONFIG_ERROR = 8
GENERATOR_EXIT_TIMEOUT = 9

GENERATOR_EXIT_REASONS = {
    0: "Specs updated",
    1: "No matching Feature Cards",
    2: "Codex CLI error (see generator logs)",
    3: "Diff rejected by guardrail",
    4: "Diff failed to apply cleanly",
    5: "Critic returned empty guidance",
    6: "Max passes reached without DONE",
    7: "Guardrail rejection (card edit or hermetic failure)",
    GENERATOR_EXIT_CONFIG_ERROR: "Missing Codex configuration",
    GENERATOR_EXIT_TIMEOUT: "Codex CLI timeout",
}

GENERATOR_EXIT_HINTS = {
    GENERATOR_EXIT_CONFIG_ERROR: "Run `npx @openai/codex login` with your GPT5-Pro account and set MODEL=<codex-model-id> before rerunning.",
    GENERATOR_EXIT_TIMEOUT: "Increase CODEX_TIMEOUT_SECONDS or retry after verifying network connectivity.",
}

CODEX_HELLO_PREFLIGHT_TTL_SECONDS = max(
    60, int(os.environ.get("CODEX_HELLO_PREFLIGHT_TTL", "1800"))
)


def _ansi_palette() -> SimpleNamespace:
    disable = bool(os.environ.get("NO_COLOR")) or not sys.stdout.isatty()
    if disable:
        return SimpleNamespace(
            header="",
            accent="",
            success="",
            warning="",
            error="",
            dim="",
            reset="",
        )
    return SimpleNamespace(
        header="\x1b[95m",
        accent="\x1b[36m",
        success="\x1b[32m",
        warning="\x1b[33m",
        error="\x1b[31m",
        dim="\x1b[2m",
        reset="\x1b[0m",
    )


def _extract_section(lines: list[str], heading: str) -> list[str]:
    target = f"## {heading}".lower()
    start: int | None = None
    for idx, line in enumerate(lines):
        if line.strip().lower() == target:
            start = idx + 1
            break
    if start is None:
        return []
    body: list[str] = []
    for line in lines[start:]:
        if line.strip().startswith("## "):
            break
        body.append(line.rstrip())
    while body and not body[0].strip():
        body.pop(0)
    while body and not body[-1].strip():
        body.pop()
    return body


def _extract_card_metadata(card_path: Path) -> dict[str, object]:
    metadata: dict[str, object] = {"title": card_path.stem.replace("-", " ").title()}
    try:
        text = card_path.read_text(encoding="utf-8")
    except OSError:
        return metadata
    lines = text.splitlines()
    for line in lines:
        if line.startswith("# "):
            metadata["title"] = line[2:].strip()
            break
    summary_section = _extract_section(lines, "Summary")
    metadata["summary"] = " ".join(summary_section).strip()
    acceptance_section = _extract_section(lines, "Acceptance Criteria")
    acceptance = [
        item.strip()[2:].strip()
        for item in acceptance_section
        if item.strip().startswith("- ")
    ]
    metadata["acceptance"] = acceptance
    return metadata


def _list_existing_specs(specs_dir: Path) -> list[str]:
    if not specs_dir.exists():
        return []
    items: list[str] = []
    for path in sorted(specs_dir.rglob("*.py")):
        try:
            items.append(str(path.relative_to(specs_dir)))
        except ValueError:
            items.append(path.name)
    return items


def _render_generator_dashboard(
    *,
    card: FeatureCard,
    specs_dir: Path,
    focus: str,
    passes: int,
    options: GeneratorOptions,
    metadata: dict[str, object] | None = None,
    existing_specs: list[str] | None = None,
) -> None:
    palette = _ansi_palette()
    metadata = metadata or _extract_card_metadata(card.path)
    existing_specs = existing_specs or _list_existing_specs(specs_dir)
    header = f"{palette.header}Generator Dashboard{palette.reset}"
    divider = "-" * 62
    print(f"\n{header}")
    print(divider)
    title = metadata.get("title", card.slug)
    summary_text = metadata.get("summary", "")
    acceptance = metadata.get("acceptance") or []
    print(f"{palette.accent}Feature{palette.reset}: {card.slug} ({title})")
    print(f"{palette.accent}Status{palette.reset}: {card.status}")
    if summary_text:
        print(f"{palette.accent}Summary{palette.reset}: {summary_text}")
    if acceptance:
        print(f"{palette.accent}Acceptance Criteria{palette.reset}:")
        for item in acceptance:
            print(f"  - {item}")
    if existing_specs:
        specs_list = ", ".join(existing_specs)
        print(f"{palette.accent}Existing specs{palette.reset}: {specs_list}")
    else:
        print(f"{palette.accent}Existing specs{palette.reset}: (none yet)")
    print(
        f"{palette.accent}Focus{palette.reset}: {focus or 'default coverage guidance'}"
    )
    print(
        f"{palette.accent}Pass budget{palette.reset}: {passes} (continuous={options.continuous})"
    )
    print(divider)


def _summarize_diff(diff_text: str) -> tuple[list[dict[str, object]], dict[str, int]]:
    entries: list[dict[str, object]] = []
    totals = defaultdict(int)
    current: dict[str, object] | None = None
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            if current:
                entries.append(current)
            parts = line.split()
            path = parts[-1] if parts else ""
            if path.startswith("b/"):
                path = path[2:]
            current = {
                "path": path,
                "status": "modified",
                "added": 0,
                "removed": 0,
                "added_tests": [],
                "removed_tests": [],
            }
        elif current is None:
            continue
        elif line.startswith("new file mode"):
            current["status"] = "new"
        elif line.startswith("deleted file mode"):
            current["status"] = "deleted"
        elif line.startswith("+++ b/"):
            if line.endswith("/dev/null"):
                current["status"] = "deleted"
        elif line.startswith("--- a/"):
            if line.endswith("/dev/null"):
                current["status"] = "new"
        elif line.startswith("+") and not line.startswith("+++"):
            current["added"] = current.get("added", 0) + 1
            totals["added_lines"] += 1
            stripped = line[1:].lstrip()
            if stripped.startswith("def test"):
                name = stripped.split("(", 1)[0].replace("def", "", 1).strip()
                current["added_tests"].append(name)
        elif line.startswith("-") and not line.startswith("---"):
            current["removed"] = current.get("removed", 0) + 1
            totals["removed_lines"] += 1
            stripped = line[1:].lstrip()
            if stripped.startswith("def test"):
                name = stripped.split("(", 1)[0].replace("def", "", 1).strip()
                current["removed_tests"].append(name)
    if current:
        entries.append(current)
    totals["files"] = len(entries)
    for entry in entries:
        added_tests = set(entry.get("added_tests", []))
        removed_tests = set(entry.get("removed_tests", []))
        modified_tests = sorted(added_tests & removed_tests)
        entry["modified_tests"] = modified_tests
        entry["added_tests"] = sorted(added_tests - removed_tests)
        entry["removed_tests"] = sorted(removed_tests - added_tests)
    return entries, totals


@dataclass
class _TestMetadata:
    name: str
    rel_path: Path
    docstring: str
    normalized_name: str
    normalized_doc: str
    tokens: set[str]
    acceptance_indexes: set[int]

    @property
    def display(self) -> str:
        return f"{self.rel_path.as_posix()}::{self.name}"


@dataclass
class _SpecTraceEntry:
    index: int
    text: str
    tests: list[_TestMetadata]


@dataclass
class _SpecTraceResult:
    entries: list[_SpecTraceEntry]
    missing: list[_SpecTraceEntry]
    orphans: list[_TestMetadata]
    section_lines: list[str]


def _spec_trace_payload(result: _SpecTraceResult) -> dict[str, Any]:
    def _entry_payload(entry: _SpecTraceEntry) -> dict[str, Any]:
        return {
            "index": entry.index,
            "text": entry.text,
            "tests": [test.display for test in entry.tests],
            "status": "covered" if entry.tests else "missing",
        }

    return {
        "entries": [_entry_payload(entry) for entry in result.entries],
        "missing": [_entry_payload(entry) for entry in result.missing],
        "orphans": [orphan.display for orphan in result.orphans],
    }


def _print_diff_summary(diff_text: str) -> None:
    entries, totals = _summarize_diff(diff_text)
    if not entries:
        return
    palette = _ansi_palette()
    files_changed = totals.get("files", 0)
    added_lines = totals.get("added_lines", 0)
    removed_lines = totals.get("removed_lines", 0)
    print(
        f"{palette.accent}Diff summary{palette.reset}: {files_changed} files, "
        f"+{added_lines} / -{removed_lines} lines"
    )
    for entry in entries:
        path = entry["path"]
        status = entry["status"]
        added = entry["added"]
        removed = entry["removed"]
        status_label = status
        if status == "new":
            status_label = f"{palette.success}new{palette.reset}"
        elif status == "deleted":
            status_label = f"{palette.warning}deleted{palette.reset}"
        changes = []
        if added:
            changes.append(f"+{added}")
        if removed:
            changes.append(f"-{removed}")
        change_text = ", ".join(changes) if changes else "no line changes"
        print(f"  • {path} ({status_label}, {change_text})")
        for label, tests in (
            ("added", entry["added_tests"]),
            ("modified", entry["modified_tests"]),
            ("removed", entry["removed_tests"]),
        ):
            if tests:
                joined = ", ".join(tests)
                print(f"      {label} tests: {joined}")


def _normalize_spec_text(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _tokenize_spec_text(text: str) -> set[str]:
    return {token for token in re.split(r"[^a-z0-9]+", text.lower()) if token}


_AC_PATTERN = re.compile(r"AC#(\d+)", re.IGNORECASE)


def _attribute_chain(node: ast.AST) -> list[str]:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return list(reversed(parts))


def _literal_int(node: ast.AST) -> int | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return int(node.value)
    if isinstance(node, ast.Num):  # pragma: no cover - python <3.8 compat
        return int(node.n)
    return None


def _extract_acceptance_indexes(node: ast.AST) -> set[int]:
    indexes: set[int] = set()
    docstring = ast.get_docstring(node) or ""
    for match in _AC_PATTERN.findall(docstring):
        try:
            indexes.add(int(match))
        except ValueError:
            continue
    for decorator in node.decorator_list:
        if isinstance(decorator, ast.Call):
            func = decorator.func
            if isinstance(func, ast.Attribute) and func.attr == "ac":
                chain = _attribute_chain(func.value)
                if chain and chain[-1] == "mark":
                    for arg in decorator.args:
                        value = _literal_int(arg)
                        if value is not None:
                            indexes.add(value)
            elif isinstance(func, ast.Name) and func.id == "ac":
                for arg in decorator.args:
                    value = _literal_int(arg)
                    if value is not None:
                        indexes.add(value)
    return indexes


def _collect_test_metadata(root: Path, specs_dir: Path) -> list[_TestMetadata]:
    if not specs_dir.exists():
        return []
    results: list[_TestMetadata] = []
    for path in sorted(specs_dir.rglob("*.py")):
        try:
            source = path.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            continue
        rel_path = path.relative_to(root)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not node.name.startswith("test"):
                    continue
                docstring = ast.get_docstring(node) or ""
                normalized_name = _normalize_spec_text(node.name)
                normalized_doc = _normalize_spec_text(docstring)
                tokens = _tokenize_spec_text(node.name) | _tokenize_spec_text(docstring)
                acceptance_indexes = _extract_acceptance_indexes(node)
                results.append(
                    _TestMetadata(
                        name=node.name,
                        rel_path=rel_path,
                        docstring=docstring.strip(),
                        normalized_name=normalized_name,
                        normalized_doc=normalized_doc,
                        tokens=tokens,
                        acceptance_indexes=acceptance_indexes,
                    )
                )
    return results


def _bullet_matches(
    bullet_norm: str,
    bullet_tokens: set[str],
    candidate: _TestMetadata,
) -> bool:
    if not bullet_norm and not bullet_tokens:
        return False
    if bullet_norm and bullet_norm in candidate.normalized_doc:
        return True
    if bullet_norm and bullet_norm in candidate.normalized_name:
        return True
    if not bullet_tokens:
        return False
    shared = bullet_tokens & candidate.tokens
    if shared == bullet_tokens:
        return True
    if len(shared) >= max(1, len(bullet_tokens) - 1):
        return True
    return False


def _build_spec_trace_result(
    *,
    card: FeatureCard,
    slug: str,
    context: RexContext,
) -> _SpecTraceResult | None:
    metadata = _extract_card_metadata(card.path)
    acceptance = metadata.get("acceptance") or []
    root = context.root
    specs_dir = root / "tests" / "feature_specs" / slug
    tests = _collect_test_metadata(root, specs_dir)
    if not acceptance and not tests:
        return None

    matched: set[str] = set()
    entries: list[_SpecTraceEntry] = []
    for index, text in enumerate(acceptance, start=1):
        matches = [
            candidate for candidate in tests if index in candidate.acceptance_indexes
        ]
        matches_sorted = sorted(matches, key=lambda c: c.display)
        for candidate in matches_sorted:
            matched.add(candidate.display)
        entries.append(_SpecTraceEntry(index=index, text=text, tests=matches_sorted))

    section_lines: list[str] = []
    if entries:
        for entry in entries:
            section_lines.append(f'- [AC#{entry.index}] "{entry.text}"')
            if entry.tests:
                for linked_test in entry.tests:
                    section_lines.append(
                        f"  -> [AC#{entry.index}] {linked_test.display}"
                    )
            else:
                section_lines.append(f"  -> [AC#{entry.index}] (missing)")
    else:
        section_lines.append("- (no acceptance criteria listed)")

    missing = [entry for entry in entries if not entry.tests]
    orphans = sorted(
        [test for test in tests if test.display not in matched],
        key=lambda item: item.display,
    )
    return _SpecTraceResult(
        entries=entries, missing=missing, orphans=orphans, section_lines=section_lines
    )


def _replace_card_section(
    card_path: Path, heading: str, content_lines: Sequence[str]
) -> bool:
    try:
        original = card_path.read_text(encoding="utf-8")
    except OSError:
        return False
    lines = original.splitlines()
    heading_lower = f"## {heading}".lower()
    start_idx: int | None = None
    for idx, line in enumerate(lines):
        if line.strip().lower() == heading_lower:
            start_idx = idx
            break
    if start_idx is None:
        # Append heading at the end
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"## {heading}")
        start_idx = len(lines) - 1
        lines.append("")
    end_idx = start_idx + 1
    while end_idx < len(lines) and not lines[end_idx].strip().startswith("## "):
        end_idx += 1

    replacement: list[str] = [""]
    replacement.extend(content_lines)
    if content_lines:
        replacement.append("")
    new_lines = lines[: start_idx + 1] + replacement + lines[end_idx:]

    # Remove duplicate trailing blank lines
    while (
        len(new_lines) > 1 and not new_lines[-1].strip() and not new_lines[-2].strip()
    ):
        new_lines.pop()

    updated = "\n".join(new_lines)
    if original.rstrip("\n") == updated.rstrip("\n"):
        return False
    card_path.write_text(updated + "\n", encoding="utf-8")
    return True


def _update_spec_trace(
    *,
    card: FeatureCard,
    slug: str,
    context: RexContext,
) -> tuple[_SpecTraceResult | None, bool]:
    result = _build_spec_trace_result(card=card, slug=slug, context=context)
    if result is None:
        return None, False
    changed = _replace_card_section(card.path, "Spec Trace", result.section_lines)
    return result, changed


def _print_spec_trace_result(result: _SpecTraceResult) -> None:
    palette = _ansi_palette()
    print(f"{palette.accent}Spec Trace coverage{palette.reset}:")
    if not result.entries:
        print("  (no acceptance criteria listed)")
    for entry in result.entries:
        label = f"[AC#{entry.index}] {entry.text}"
        print(f"  {label}")
        if entry.tests:
            for matched in entry.tests:
                print(f"      -> {matched.display}")
        else:
            print(f"      -> {palette.warning}(missing){palette.reset}")
    if result.missing:
        for entry in result.missing:
            print(
                f"{palette.warning}[generator] Acceptance criterion lacks coverage:{palette.reset} "
                f"[AC#{entry.index}] {entry.text}"
            )
    if result.orphans:
        print(
            f"{palette.warning}[generator] The following tests do not map to any acceptance bullet:{palette.reset}"
        )
        for orphan in result.orphans:
            hint = (
                f"docstring: {orphan.docstring}" if orphan.docstring else "no docstring"
            )
            print(f"      - {orphan.display} ({hint})")


def _iteration_metrics(
    result: _SpecTraceResult | None, card_trace_changed: bool
) -> dict[str, Any]:
    total = len(result.entries) if result else 0
    missing = len(result.missing) if result else 0
    covered = total - missing
    orphans = len(result.orphans) if result else 0
    coverage_ratio = covered / total if total else 0.0
    return {
        "fci_total": total,
        "fci_covered": covered,
        "missing": missing,
        "orphans": orphans,
        "coverage_ratio": round(coverage_ratio, 4),
        "card_trace_changed": card_trace_changed,
    }


def _load_pass_durations(context: RexContext) -> list[float]:
    data = load_json(context.rex_agent_file)
    generator_state = data.get("generator", {})
    durations = generator_state.get("pass_durations", [])
    if isinstance(durations, list):
        return [float(value) for value in durations if isinstance(value, (int, float))]
    return []


def _average_pass_duration(context: RexContext) -> float | None:
    durations = _load_pass_durations(context)
    if len(durations) < 2:
        return None
    return sum(durations) / len(durations)


def _record_pass_duration(context: RexContext, seconds: float) -> None:
    data = load_json(context.rex_agent_file)
    generator_state = data.setdefault("generator", {})
    durations = generator_state.get("pass_durations", [])
    if not isinstance(durations, list):
        durations = []
    durations.append(round(seconds, 2))
    generator_state["pass_durations"] = durations[-10:]
    dump_json(context.rex_agent_file, data)


def _emit_codex_updates(chunk: str, palette: SimpleNamespace, last_update: str) -> str:
    lines = [line.strip() for line in chunk.splitlines() if line.strip()]
    if not lines:
        return last_update
    interesting: list[str] = []
    for line in lines:
        if line.startswith(
            ("diff --git", "index ", "--- ", "+++ ", "@@ ", "+", "-", "Applying diff")
        ):
            continue
        if line.startswith("Total patch size"):
            continue
        interesting.append(line)
    candidates = interesting or lines
    for line in candidates[-3:]:
        snippet = line
        if len(snippet) > 160:
            snippet = snippet[:157] + "…"
        if snippet and snippet != last_update:
            print(f"{palette.accent}[generator] Codex: {snippet}{palette.reset}")
            last_update = snippet
    return last_update


def _diagnose_missing_cards(statuses: list[str], context: RexContext) -> None:
    cards = discover_cards(context=context)
    if not cards:
        print("[generator] No Feature Cards found in documents/feature_cards/.")
        return
    palette = _ansi_palette()
    print("[generator] Feature Cards present but none matched the requested statuses.")
    for card in cards:
        suggestion = ""
        for target in statuses:
            if not target:
                continue
            ratio = difflib.SequenceMatcher(None, card.status, target).ratio()
            if ratio >= 0.75 and card.status != target:
                suggestion = (
                    f' ({palette.warning}did you mean "{target}"?{palette.reset})'
                )
                break
        status_display = f"status={card.status}"
        print(f"  - {card.slug}: {status_display}{suggestion}")


def _default_ui_hz() -> float:
    raw = os.environ.get("GENERATOR_UI_HZ")
    if raw is None:
        return 1.0
    try:
        value = float(raw)
    except ValueError:
        return 5.0
    return value if value > 0 else 5.0


def _parse_env_toggle(raw: str | None) -> bool | None:
    if raw is None:
        return None
    value = raw.strip().lower()
    if not value or value == "auto":
        return None
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return None


def _default_popout_enabled() -> bool:
    env = _parse_env_toggle(os.environ.get("GENERATOR_UI_POPOUT"))
    if env is not None:
        return env
    return False


def _default_popout_linger() -> float:
    raw = os.environ.get("GENERATOR_UI_LINGER")
    if raw is None:
        if repo_root().name == "rex_codex_agent":
            return 30.0
        return 5.0
    try:
        value = float(raw)
    except ValueError:
        return 5.0
    return max(0.0, value)


def _default_scrub_specs_flag() -> bool | None:
    return _parse_env_toggle(os.environ.get("GENERATOR_SCRUB_SPECS"))


def _default_ui_mode() -> str:
    value = os.environ.get("GENERATOR_UI")
    if not value:
        return "off"
    normalized = value.strip().lower()
    if normalized == "auto":
        return "monitor"
    return normalized


@dataclass
class GeneratorOptions:
    continuous: bool = True
    max_passes: int = int(os.environ.get("GENERATOR_MAX_PASSES", "5"))
    focus: str = ""
    card_path: Path | None = None
    iterate_all: bool = False
    statuses: list[str] = field(default_factory=lambda: ["proposed"])
    codex_bin: str = os.environ.get("CODEX_BIN", "npx --yes @openai/codex")
    codex_flags: str = os.environ.get("CODEX_FLAGS", "")
    codex_model: str = os.environ.get("MODEL", "")
    verbose: bool = True
    tail_lines: int = 0
    reconcile_only: bool = False
    ui_mode: str = field(default_factory=_default_ui_mode)
    ui_refresh_hz: float = field(default_factory=_default_ui_hz)
    spawn_popout: bool = field(default_factory=_default_popout_enabled)
    popout_linger: float = field(default_factory=_default_popout_linger)
    scrub_specs: bool | None = field(default_factory=_default_scrub_specs_flag)
    prompt_file: Path | None = None
    prompt_target: Path | None = None
    prompt_label: str | None = None


@dataclass
class _CodexResult:
    returncode: int
    stdout: str
    stderr: str
    elapsed_seconds: int
    timeout: bool = False
    limit_seconds: int | None = None


def parse_statuses(raw: str | None) -> list[str]:
    if not raw:
        return ["proposed"]
    tokens = [piece.strip().lower() for piece in raw.split(",") if piece.strip()]
    return tokens or ["proposed"]


def _split_command(raw: str) -> list[str]:
    import shlex

    return shlex.split(raw)


_TERMINAL_CANDIDATES: list[tuple[str, list[str]]] = [
    ("gnome-terminal", ["--title", "{title}", "--", "bash", "-lc", "{command}"]),
    ("kitty", ["--title", "{title}", "bash", "-lc", "{command}"]),
    ("wezterm", ["start", "--", "bash", "-lc", "{command}"]),
    ("alacritty", ["-t", "{title}", "-e", "bash", "-lc", "{command}"]),
    ("x-terminal-emulator", ["-T", "{title}", "-e", "bash", "-lc", "{command}"]),
    ("xterm", ["-T", "{title}", "-hold", "-e", "bash", "-lc", "{command}"]),
]


def _format_terminal_args(
    executable: str, tokens: Sequence[str], *, title: str, command: str
) -> list[str]:
    args = [executable]
    for token in tokens:
        if token == "{title}":
            args.append(title)
        elif token == "{command}":
            args.append(command)
        else:
            args.append(token)
    return args


def _launch_terminal(title: str, command: str) -> tuple[subprocess.Popen, str] | None:
    for exe, tokens in _TERMINAL_CANDIDATES:
        exe_path = which(exe)
        if not exe_path:
            continue
        argv = _format_terminal_args(exe_path, tokens, title=title, command=command)
        try:
            proc = subprocess.Popen(argv, start_new_session=True)
            return proc, exe
        except OSError as exc:  # pragma: no cover - depends on local terminal setup
            print(f"[generator] Failed to launch {exe}: {exc}")
            continue
    print("[generator] Unable to launch HUD popout; no terminal emulator detected.")
    return None


def _spawn_generator_tui_popout(
    *,
    context: RexContext,
    slug: str,
) -> subprocess.Popen | None:
    env_toggle = _parse_env_toggle(os.environ.get("GENERATOR_UI_TUI"))
    if env_toggle is False:
        return None
    tui_dir = context.root / "tui"
    if not tui_dir.exists() or not tui_dir.is_dir():
        return None
    if which("npm") is None:
        return None
    if which("node") is None:
        return None
    events_file = context.codex_ci_dir / "events.jsonl"
    diff_file = context.codex_ci_dir / "generator_patch.diff"
    install_cmd = (
        "if [ ! -d tui/node_modules ]; then "
        "npm --prefix tui install --no-fund --no-audit >/dev/null 2>&1 || exit 1; "
        "fi"
    )
    build_cmd = (
        "if [ ! -f tui/dist/index.js ]; then "
        "npm --prefix tui run build >/dev/null 2>&1 || exit 1; "
        "fi"
    )
    env_assignments = " ".join(
        [
            "FORCE_COLOR=1",
            f"TUI_SLUG={shlex.quote(slug)}",
            f"TUI_REPO_ROOT={shlex.quote(str(context.root))}",
            f"TUI_EVENTS_FILE={shlex.quote(str(events_file))}",
            f"TUI_DIFF_FILE={shlex.quote(str(diff_file))}",
        ]
    )
    entry_path = tui_dir / "dist" / "index.js"
    npm_command = f"{env_assignments} node {shlex.quote(str(entry_path))}"
    shell_command = (
        f"cd {shlex.quote(str(context.root))} && "
        f"{install_cmd} && {build_cmd} && {npm_command}"
    )
    title = f"rex-codex HUD :: {slug}"
    launched = _launch_terminal(title, shell_command)
    if launched is None:
        return None
    process, exe = launched
    register_loop_process(
        process.pid,
        context=context,
        label="generator-hud-tui",
        command=shell_command[:1024],
    )
    print(f"[generator] HUD popout launched via {exe} (tui).")
    return process


def _spawn_generator_popout(
    *,
    context: RexContext,
    slug: str,
    refresh_hz: float,
    linger: float,
) -> subprocess.Popen | None:
    tui_process = _spawn_generator_tui_popout(context=context, slug=slug)
    if tui_process is not None:
        return tui_process
    refresh_seconds = max(0.2, 1.0 / max(refresh_hz, 0.1))
    command_parts = [
        "./bin/rex-codex",
        "hud",
        "generator",
        "--slug",
        slug,
        "--follow",
        f"--refresh={refresh_seconds:.2f}",
        f"--linger={linger:.2f}",
    ]
    hud_command = shlex.join(command_parts)
    shell_command = f"cd {shlex.quote(str(context.root))} && {hud_command}"
    title = f"rex-codex HUD :: {slug}"
    launched = _launch_terminal(title, shell_command)
    if launched is None:
        return None
    process, exe = launched
    register_loop_process(
        process.pid,
        context=context,
        label="generator-hud-popout",
        command=shell_command[:1024],
    )
    print(f"[generator] HUD popout launched via {exe}.")
    return process


def _should_scrub_specs(context: RexContext, option: bool | None) -> bool:
    if option is not None:
        return option
    return context.root.name == "rex_codex_agent"


def _is_interactive_session() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:
        return False


def _codex_login_status_ok(result: subprocess.CompletedProcess[str]) -> bool:
    if result.returncode != 0:
        return False
    text_parts: list[str] = []
    if isinstance(result.stdout, str):
        text_parts.append(result.stdout)
    if isinstance(result.stderr, str):
        text_parts.append(result.stderr)
    combined = " ".join(text_parts)
    return "logged in" in combined.lower()


def _env_truthy(raw: str | None) -> bool:
    if raw is None:
        return False
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_iso_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _codex_hello_probe(
    *,
    slug: str,
    options: GeneratorOptions,
    context: RexContext,
    base_cmd: list[str],
) -> dict[str, object] | None:
    if _env_truthy(os.environ.get("REX_SKIP_HELLO_PREFLIGHT")):
        return None
    if not options.codex_model.strip():
        return None

    snapshot = load_json(context.rex_agent_file)
    preflight = snapshot.setdefault("preflight", {})
    state = preflight.get("codex_hello") or {}

    ttl_seconds = CODEX_HELLO_PREFLIGHT_TTL_SECONDS
    if ttl_seconds > 0 and state.get("status") == "ok":
        if state.get("model") == options.codex_model:
            last_run = _parse_iso_timestamp(state.get("timestamp"))
            if last_run and datetime.now(UTC) - last_run < timedelta(seconds=ttl_seconds):
                return {"ok": True, "cached": True}

    cmd = base_cmd + ["exec"]
    if options.codex_flags.strip():
        cmd += _split_command(options.codex_flags)
    if options.codex_model:
        cmd += ["--model", options.codex_model]
    prompt = os.environ.get(
        "CODEX_HELLO_PREFLIGHT_PROMPT",
        "Return exactly the text 'Hello World' and nothing else.",
    )
    cmd += ["--cd", str(context.root), "--", prompt]

    try:
        result = run(
            cmd,
            cwd=context.root,
            capture_output=True,
            check=False,
            text=True,
        )
    except OSError as exc:
        message = f"Codex CLI preflight failed: {exc}"
        record = {
            "status": "error",
            "timestamp": _utc_now_iso(),
            "model": options.codex_model,
            "stdout": "",
            "stderr": str(exc),
        }
        preflight["codex_hello"] = record
        dump_json(context.rex_agent_file, snapshot)
        return {"ok": False, "message": message, "exit_code": GENERATOR_EXIT_CONFIG_ERROR}

    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    ok = result.returncode == 0 and stdout == "Hello World"

    record = {
        "status": "ok" if ok else "error",
        "timestamp": _utc_now_iso(),
        "model": options.codex_model,
        "returncode": result.returncode,
        "stdout": stdout,
    }
    if stderr:
        record["stderr"] = stderr
    record["bin"] = options.codex_bin
    record["flags"] = options.codex_flags
    preflight["codex_hello"] = record
    dump_json(context.rex_agent_file, snapshot)

    if ok:
        emit_event(
            "generator",
            "codex_preflight_hello",
            slug=slug,
            level="info",
            status="ok",
            cached=False,
            stdout=stdout,
        )
        return {"ok": True, "cached": False}

    emit_event(
        "generator",
        "codex_preflight_hello",
        slug=slug,
        level="error",
        status="error",
        cached=False,
        stdout=stdout[:200],
        stderr=stderr[:200],
        exit_code=result.returncode,
    )
    message = (
        "Codex CLI preflight failed: expected 'Hello World' but got "
        f"{stdout or '<empty>'!r} (exit {result.returncode})"
    )
    return {
        "ok": False,
        "message": message,
        "exit_code": result.returncode,
        "stderr": stderr,
    }


def _codex_preflight(
    *,
    slug: str,
    options: GeneratorOptions,
    context: RexContext,
) -> dict[str, object] | None:
    if _env_truthy(os.environ.get("REX_SKIP_LLM_PRECHECK")):
        return None

    missing: list[str] = []
    violations: list[str] = []
    agent_state = load_json(context.rex_agent_file)
    if isinstance(agent_state, dict):
        doctor_state = agent_state.get("doctor")
    else:
        doctor_state = None
    if isinstance(doctor_state, dict):
        doctor_status = str(doctor_state.get("status", "") or "").lower()
        last_run = doctor_state.get("last_run")
        if not last_run:
            missing.append("Run `./rex-codex doctor` to verify the environment.")
        elif doctor_status == "error":
            failing = doctor_state.get("errors") or []
            roster = ", ".join(str(item) for item in failing[:3] if item)
            detail = (
                f"Resolve ./rex-codex doctor failures ({roster})"
                if roster
                else "Resolve ./rex-codex doctor errors"
            )
            violations.append(detail)
        elif doctor_status == "warn":
            warnings = doctor_state.get("warnings") or []
            roster = ", ".join(str(item) for item in warnings[:3] if item)
            message = "Address ./rex-codex doctor warnings"
            if roster:
                message += f" ({roster})"
            missing.append(message)
    else:
        missing.append("Run `./rex-codex doctor` to seed diagnostics.")
    login_attempted = False

    forbidden_envs = [
        var for var in ("OPENAI_API_KEY", "CODEX_API_KEY") if os.environ.get(var)
    ]
    for var in forbidden_envs:
        violations.append(f"Unset {var}; interactive Codex login is required.")

    model_present = bool(options.codex_model.strip())
    if not model_present and options.codex_flags:
        tokens = _split_command(options.codex_flags)
        for idx, token in enumerate(tokens):
            if token.startswith("--model="):
                model_present = True
                break
            if token == "--model" and idx + 1 < len(tokens):
                model_present = True
                break
    if not model_present:
        missing.append("MODEL (Codex model identifier)")

    base_cmd = _split_command(options.codex_bin)
    status_cmd = base_cmd + ["login", "status"]
    status = run(
        status_cmd,
        cwd=context.root,
        capture_output=True,
        check=False,
    )
    logged_in = _codex_login_status_ok(status)
    interactive_allowed = (
        _is_interactive_session()
        and not _env_truthy(os.environ.get("REX_SKIP_CODEX_LOGIN_PROMPT"))
        and not forbidden_envs
    )
    if not logged_in and interactive_allowed:
        palette = _ansi_palette()
        print(
            f"{palette.warning}[generator] Codex CLI login required. Launching `npx @openai/codex login`…{palette.reset}"
        )
        login_cmd = base_cmd + ["login"]
        try:
            subprocess.run(login_cmd, cwd=context.root, check=False)
            login_attempted = True
        except OSError as exc:
            print(
                f"{palette.error}[generator] Failed to start Codex login: {exc}{palette.reset}"
            )
        status = run(
            status_cmd,
            cwd=context.root,
            capture_output=True,
            check=False,
        )
        logged_in = _codex_login_status_ok(status)
    if not logged_in:
        missing.append("Codex CLI login (run `npx @openai/codex login`)")

    hello_probe: dict[str, object] | None = None
    hello_exit_code: int | None = None
    if not missing and not violations:
        hello_probe = _codex_hello_probe(
            slug=slug,
            options=options,
            context=context,
            base_cmd=base_cmd,
        )
        if hello_probe and not hello_probe.get("ok"):
            violations.append(
                hello_probe.get(
                    "message",
                    "Codex CLI preflight failed to echo 'Hello World'.",
                )
            )
            hello_exit_code = hello_probe.get("exit_code")

    if not missing and not violations:
        return None

    palette = _ansi_palette()
    print(
        f"{palette.error}[generator] Preflight failed: Codex configuration is incomplete.{palette.reset}"
    )
    for item in missing:
        print(f"{palette.error}  - missing {item}{palette.reset}")
    for issue in violations:
        print(f"{palette.error}  - {issue}{palette.reset}")

    guidance_parts: list[str] = []
    if any("Codex CLI login" in item for item in missing):
        guidance_parts.append("Run `npx @openai/codex login` with your GPT5-Pro account.")
    if any("MODEL" in item for item in missing):
        guidance_parts.append("Export MODEL=<codex-model-id> before rerunning.")
    if any("./rex-codex doctor" in item for item in missing):
        guidance_parts.append("Run `./rex-codex doctor` and fix reported issues.")
    if hello_probe and not hello_probe.get("ok"):
        guidance_parts.append(
            "Verify the Codex CLI by running `npx --yes @openai/codex exec --model \"$MODEL\" -- \"Return exactly the text 'Hello World' and nothing else.\"`."
        )
    for var in forbidden_envs:
        guidance_parts.append(f"Unset {var}; API key auth is disabled.")
    if login_attempted and not logged_in:
        guidance_parts.append(
            "Login attempt ended without authentication; rerun and complete the Codex login prompt."
        )
    hint = " ".join(guidance_parts) or GENERATOR_EXIT_HINTS[GENERATOR_EXIT_CONFIG_ERROR]

    emit_event(
        "generator",
        "preflight_failed",
        slug=slug,
        level="error",
        exit_code=hello_exit_code or GENERATOR_EXIT_CONFIG_ERROR,
        reason="missing_codex_configuration",
        missing=missing,
        violations=violations,
        hint=hint,
        login_attempted=login_attempted,
        hello_exit_code=hello_exit_code,
    )
    return {
        "exit_code": hello_exit_code or GENERATOR_EXIT_CONFIG_ERROR,
        "hint": hint,
        "missing": missing,
        "violations": violations,
        "login_attempted": login_attempted,
        "hello_exit_code": hello_exit_code,
    }


def _scrub_spec_directory(slug: str, context: RexContext) -> None:
    specs_dir = context.root / "tests" / "feature_specs" / slug
    if not specs_dir.exists():
        return
    print(f"[generator] Scrubbing spec shard: {context.relative(specs_dir)}")
    shutil.rmtree(specs_dir, ignore_errors=True)


def _run_codex_with_progress(
    cmd: Sequence[str],
    *,
    cwd: Path,
    context: RexContext,
    verbose: bool,
    progress_label: str,
    slug: str | None = None,
) -> _CodexResult:
    start = time.time()
    try:
        max_seconds_raw = os.environ.get("CODEX_TIMEOUT_SECONDS", "300").strip()
        max_seconds = int(max_seconds_raw or "300")
    except ValueError:
        max_seconds = 300
    if max_seconds <= 0:
        max_seconds = 0
    emit_event(
        "generator",
        "codex_started",
        slug=slug,
        command=list(cmd[:-1]) + ["<prompt>"] if cmd else [],
        limit_seconds=max_seconds or None,
    )
    process = subprocess.Popen(
        cmd,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    command_display = (shlex_join(cmd) if cmd else "")[:1024]
    register_loop_process(
        process.pid,
        context=context,
        label="generator-codex",
        command=command_display,
    )
    stdout_buffer: list[str] = []
    stderr_buffer: list[str] = []
    palette = _ansi_palette()
    last_update = ""
    try:
        while True:
            try:
                stdout, stderr = process.communicate(timeout=PROGRESS_INTERVAL_SECONDS)
                if stdout:
                    if not isinstance(stdout, str):
                        stdout = stdout.decode()
                    stdout_buffer.append(stdout)
                    if verbose:
                        last_update = _emit_codex_updates(stdout, palette, last_update)
                if stderr:
                    if not isinstance(stderr, str):
                        stderr = stderr.decode()
                    stderr_buffer.append(stderr)
                break
            except subprocess.TimeoutExpired as exc:
                # exc.output / exc.stderr contain partial data when text=True and pipes are used
                if exc.output:
                    chunk = (
                        exc.output if isinstance(exc.output, str) else exc.output.decode()
                    )
                    stdout_buffer.append(chunk)
                    if verbose:
                        last_update = _emit_codex_updates(chunk, palette, last_update)
                if exc.stderr:
                    chunk_err = (
                        exc.stderr if isinstance(exc.stderr, str) else exc.stderr.decode()
                    )
                    stderr_buffer.append(chunk_err)
                elapsed = int(time.time() - start)
                if verbose:
                    print(f"[generator] {progress_label}… {elapsed}s elapsed", flush=True)
                emit_event(
                    "generator",
                    "codex_heartbeat",
                    slug=slug,
                    seconds=elapsed,
                    progress_label=progress_label,
                    limit_seconds=max_seconds or None,
                    progress=min(1.0, elapsed / max_seconds)
                    if max_seconds
                    else None,
                )
                if max_seconds and elapsed >= max_seconds:
                    print(
                        f"[generator] Codex CLI exceeded {max_seconds}s; terminating process.",
                        flush=True,
                    )
                    emit_event(
                        "generator",
                        "codex_timeout",
                        slug=slug,
                        elapsed_seconds=elapsed,
                        limit_seconds=max_seconds,
                    )
                    process.kill()
                    try:
                        stdout, stderr = process.communicate(timeout=5)
                        if stdout:
                            if not isinstance(stdout, str):
                                stdout = stdout.decode()
                            stdout_buffer.append(stdout)
                        if stderr:
                            if not isinstance(stderr, str):
                                stderr = stderr.decode()
                            stderr_buffer.append(stderr)
                    except subprocess.TimeoutExpired:
                        pass
                    elapsed_total = int(time.time() - start)
                    stdout_combined = "".join(stdout_buffer)
                    stderr_combined = "".join(stderr_buffer)
                    emit_event(
                        "generator",
                        "codex_completed",
                        slug=slug,
                        returncode=124,
                        elapsed_seconds=elapsed_total,
                        timeout=True,
                        limit_seconds=max_seconds or None,
                    )
                    return _CodexResult(
                        returncode=124,
                        stdout=stdout_combined,
                        stderr=stderr_combined,
                        elapsed_seconds=elapsed_total,
                        timeout=True,
                        limit_seconds=max_seconds or None,
                    )
    finally:
        unregister_loop_process(process.pid, context=context)
    elapsed_total = int(time.time() - start)
    stdout_combined = "".join(stdout_buffer)
    stderr_combined = "".join(stderr_buffer)
    emit_event(
        "generator",
        "codex_completed",
        slug=slug,
        returncode=int(process.returncode or 0),
        elapsed_seconds=elapsed_total,
        limit_seconds=max_seconds or None,
    )
    return _CodexResult(
        returncode=process.returncode or 0,
        stdout=stdout_combined,
        stderr=stderr_combined,
        elapsed_seconds=elapsed_total,
        timeout=False,
        limit_seconds=max_seconds or None,
    )


def _run_card_with_ui(
    card: FeatureCard, options: GeneratorOptions, context: RexContext
) -> int:
    ui_mode = (options.ui_mode or "monitor").lower()
    popout_requested = ui_mode == "popout"
    if ui_mode in {"auto", "plain"}:
        ui_mode = "monitor"
    if popout_requested:
        ui_mode = "monitor"
        options.spawn_popout = True
    options.ui_mode = ui_mode

    update_llm_settings(
        context,
        codex_bin=options.codex_bin,
        codex_flags=options.codex_flags,
        codex_model=options.codex_model,
    )

    if options.reconcile_only:
        return _process_card(card, options, context)

    if _should_scrub_specs(context, options.scrub_specs):
        _scrub_spec_directory(card.slug, context)

    preflight = _codex_preflight(slug=card.slug, options=options, context=context)
    if preflight is not None:
        exit_code = int(preflight.get("exit_code", GENERATOR_EXIT_CONFIG_ERROR))
        hint = str(preflight.get("hint", "") or "")
        reason = GENERATOR_EXIT_REASONS.get(
            exit_code, "Generator preflight failure"
        )
        missing = preflight.get("missing") or []
        violations = preflight.get("violations") or []
        login_attempted = bool(preflight.get("login_attempted"))
        emit_event(
            "generator",
            "feature_failed",
            slug=card.slug,
            iteration=0,
            exit_code=exit_code,
            reason=reason,
            hint=hint or None,
            missing=missing,
            violations=violations,
            login_attempted=login_attempted,
        )
        palette = _ansi_palette()
        print(f"{palette.error}[generator] Aborting before Codex invocation.{palette.reset}")
        if hint:
            print(f"{palette.dim}{hint}{palette.reset}")
        return exit_code

    events_file = events_path()
    if ui_mode != "off":
        try:
            events_file.unlink()
        except FileNotFoundError:
            pass
        emit_event("generator", "hud_reset", slug=card.slug)

    # Build deterministic playbook artefacts before planning/generation.
    try:
        build_playbook_artifacts(card=card, context=context)
    except Exception as exc:  # pragma: no cover - defensive logging
        print(f"[generator] Failed to build playbook artefacts: {exc}")

    if os.environ.get("REX_DISABLE_PLANNER", "").lower() not in {"1", "true", "yes"}:
        ensure_component_plan(
            card=card,
            context=context,
            codex_bin=options.codex_bin,
            codex_flags=options.codex_flags,
            codex_model=options.codex_model,
            verbose=options.verbose,
        )

    if options.spawn_popout and ui_mode == "monitor":
        popout_launched = _spawn_generator_popout(
            context=context,
            slug=card.slug,
            refresh_hz=options.ui_refresh_hz,
            linger=options.popout_linger,
        )
        if popout_launched is None and popout_requested:
            print(
                "[generator] Popout HUD requested but no terminal emulator was launched."
            )

    if ui_mode == "snapshot":
        exit_code = _process_card(card, options, context)
        try:
            snapshot = generator_snapshot_text(card.slug, events_file)
            if snapshot:
                print(snapshot, end="")
        except Exception:
            pass
        status_label = "PASS" if exit_code == 0 else f"EXIT {exit_code}"
        console_log = context.codex_ci_dir / f"generator_console_{card.slug}.log"
        print(
            f"[generator] Finished {card.slug} ({status_label}). Console log: {console_log}"
        )
        return exit_code

    if ui_mode == "off":
        return _process_card(card, options, context)

    hud = GeneratorHUD(
        slug=card.slug,
        codex_ci_dir=context.codex_ci_dir,
        ui_mode=ui_mode,
        refresh_hz=options.ui_refresh_hz,
    )
    if not hud.enabled:
        exit_code = _process_card(card, options, context)
        try:
            snapshot = generator_snapshot_text(card.slug, events_file)
            if snapshot:
                print(snapshot, end="")
        except Exception:
            pass
        status_label = "PASS" if exit_code == 0 else f"EXIT {exit_code}"
        console_log = context.codex_ci_dir / f"generator_console_{card.slug}.log"
        print(
            f"[generator] Finished {card.slug} ({status_label}). Console log: {console_log}"
        )
        return exit_code

    exit_code = 0
    with hud:
        exit_code = _process_card(card, options, context)
    try:
        snapshot = generator_snapshot_text(card.slug, events_file)
        if snapshot:
            print(snapshot, end="")
    except Exception:
        pass
    hud.print_footer(exit_code)
    return exit_code


def _run_prompt_only(options: GeneratorOptions, context: RexContext) -> int:
    if options.prompt_file is None:
        print("[generator] --prompt-file is required for prompt-only mode.")
        return 1
    prompt_path = options.prompt_file
    if not prompt_path.is_absolute():
        prompt_path = (Path.cwd() / prompt_path).resolve()
    if not prompt_path.exists():
        print(f"[generator] Prompt file not found: {prompt_path}")
        return 1

    try:
        prompt_text = prompt_path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"[generator] Failed to read prompt file: {exc}")
        return 1

    target_path: Path | None = None
    if options.prompt_target:
        target_path = options.prompt_target
        if not target_path.is_absolute():
            target_path = (Path.cwd() / target_path).resolve()

    label = options.prompt_label or prompt_path.stem
    emit_event(
        "generator",
        "prompt_only_started",
        slug=label,
        prompt_file=str(prompt_path),
        target=str(target_path) if target_path else None,
    )

    response_path = context.codex_ci_dir / "generator_response.log"
    diff_path = context.codex_ci_dir / "generator_patch.diff"
    prompt_log = context.codex_ci_dir / "generator_prompt.txt"
    prompt_log.write_text(prompt_text, encoding="utf-8")

    cmd = (
        _split_command(options.codex_bin)
        + ["exec"]
        + _split_command(options.codex_flags)
    )
    if options.codex_model:
        cmd += ["--model", options.codex_model]
    cmd += ["--cd", str(context.root), "--", prompt_text]

    if options.verbose:
        print(f"[generator] Running one-shot Codex prompt ({prompt_path})")
    completed = _run_codex_with_progress(
        cmd,
        cwd=context.root,
        context=context,
        verbose=options.verbose,
        progress_label=f"Codex CLI (prompt: {label})",
        slug=label,
    )
    response_path.write_text(
        (completed.stdout or "") + ("\n" if completed.stdout else ""),
        encoding="utf-8",
    )
    if completed.stderr:
        response_path.write_text(
            response_path.read_text(encoding="utf-8") + completed.stderr,
            encoding="utf-8",
        )
    if completed.returncode != 0:
        print(
            f"[generator] Codex CLI exited with status {completed.returncode} during prompt-only mode.",
            file=sys.stderr,
        )
        emit_event(
            "generator",
            "prompt_only_failed",
            slug=label,
            exit_code=completed.returncode,
        )
        return completed.returncode or 1

    diff_text = _extract_diff(response_path, None)
    diff_path.write_text(diff_text, encoding="utf-8")
    if not diff_text.strip():
        print("[generator] Codex response did not contain a unified diff.")
        emit_event(
            "generator",
            "prompt_only_failed",
            slug=label,
            exit_code=3,
            reason="empty_diff",
        )
        return 3

    if target_path:
        target_rel = context.relative(target_path)
        if target_rel not in diff_text:
            print(
                "[generator] Codex diff did not touch the requested target "
                f"({target_rel})."
            )
            emit_event(
                "generator",
                "prompt_only_failed",
                slug=label,
                exit_code=3,
                reason="target_not_modified",
            )
            return 3

    if not _enforce_patch_size(diff_text):
        emit_event(
            "generator",
            "prompt_only_failed",
            slug=label,
            exit_code=3,
            reason="patch_size",
        )
        return 3

    if options.verbose:
        print(f"[generator] Applying diff from {context.relative(diff_path)}")
        _print_diff_preview(diff_text)
        _print_diff_summary(diff_text)

    applied, patch_error = _apply_patch(diff_path, context.root)
    if not applied:
        print("[generator] Failed to apply Codex diff.")
        if patch_error:
            tail = "\n".join(patch_error.splitlines()[-8:])
            print(tail)
        emit_event(
            "generator",
            "prompt_only_failed",
            slug=label,
            exit_code=4,
            reason="apply_failed",
        )
        return 4

    emit_event(
        "generator",
        "prompt_only_completed",
        slug=label,
        prompt_file=str(prompt_path),
        target=str(target_path) if target_path else None,
    )
    print(f"[generator] Applied diff from prompt {prompt_path}")
    return 0


def run_generator(
    options: GeneratorOptions, *, context: RexContext | None = None
) -> int:
    context = context or RexContext.discover()
    ensure_monitor_server(context, open_browser=True)
    self_update()
    ensure_dir(context.codex_ci_dir)
    lock_path = context.codex_ci_dir / "rex_generator.lock"
    with lock_file(lock_path):
        ensure_python(context, quiet=True)
        env_verbose = os.environ.get("GENERATOR_DEBUG")
        if env_verbose is not None:
            options.verbose = env_verbose.lower() not in {"0", "false", ""}
        requirements_template = AGENT_SRC / "templates" / "requirements-dev.txt"
        ensure_requirements_installed(context, requirements_template)
        if options.scrub_specs is None:
            options.scrub_specs = _should_scrub_specs(context, None)
        if options.prompt_file is not None:
            return _run_prompt_only(options, context)
        cards: list[FeatureCard]
        if options.card_path:
            if not options.card_path.exists():
                print(f"[generator] Feature Card not found: {options.card_path}")
                return 1
            slug = options.card_path.stem
            cards = [
                FeatureCard(
                    path=options.card_path,
                    slug=slug,
                    status=options.statuses[0] if options.statuses else "unknown",
                )
            ]
        else:
            cards = discover_cards(statuses=options.statuses, context=context)

        if not cards:
            status_list = ", ".join(options.statuses)
            print(f"[generator] No Feature Cards with statuses: {status_list}")
            if options.statuses:
                _diagnose_missing_cards(options.statuses, context)
            return 1

        if options.reconcile_only:
            targets = cards if options.iterate_all else [cards[0]]
            exit_status = 0
            for card in targets:
                exit_status = max(exit_status, _reconcile_card(card, context))
            return exit_status

        if options.iterate_all:
            for card in cards:
                print(f"[generator] === Processing card {card.path} ===")
                exit_code = _run_card_with_ui(card, options, context)
                if exit_code != 0:
                    return exit_code
            return 0

        return _run_card_with_ui(cards[0], options, context)


def _process_card(
    card: FeatureCard, options: GeneratorOptions, context: RexContext
) -> int:
    slug = card.slug
    status = card.status
    focus = options.focus
    passes = options.max_passes if options.continuous else 1
    specs_dir = context.root / "tests" / "feature_specs" / slug
    metadata = _extract_card_metadata(card.path)
    existing_specs = _list_existing_specs(specs_dir)
    convergence_history: list[dict[str, Any]] = []
    total_elapsed = 0.0

    update_active_card(context, card=card)
    _render_generator_dashboard(
        card=card,
        specs_dir=specs_dir,
        focus=focus,
        passes=passes,
        options=options,
        metadata=metadata,
        existing_specs=existing_specs,
    )
    emit_event(
        "generator",
        "feature_started",
        slug=slug,
        title=str(metadata.get("title", card.slug)),
        status=status,
        card_path=str(card.relative_path),
        summary=metadata.get("summary"),
        acceptance=metadata.get("acceptance") or [],
        existing_specs=existing_specs,
        focus=focus,
        passes=passes,
        continuous=options.continuous,
    )

    for iteration in range(1, passes + 1):
        avg_duration = _average_pass_duration(context)
        if avg_duration and avg_duration >= 20:
            print(
                f"[generator] Recent passes averaged {avg_duration:.1f}s; Codex may report progress more slowly."
            )
        print(
            f"[generator] Iteration {iteration}/{passes} (slug: {slug}, status: {status})"
        )
        iteration_start = time.perf_counter()
        emit_event(
            "generator",
            "iteration_started",
            slug=slug,
            iteration=iteration,
            total_passes=passes,
            focus=focus,
            status=status,
        )
        exit_code, iteration_metrics = _run_once(
            card=card,
            slug=slug,
            status=status,
            focus=focus,
            generation_pass=iteration,
            total_passes=passes,
            options=options,
            context=context,
        )
        elapsed = time.perf_counter() - iteration_start
        total_elapsed += elapsed
        metrics_payload: dict[str, Any] | None = None
        if iteration_metrics is not None:
            metrics_payload = dict(iteration_metrics)
            metrics_payload["elapsed_seconds"] = round(elapsed, 2)
            metrics_payload["iteration"] = iteration
            convergence_history.append(metrics_payload)
        if exit_code == 0:
            _record_pass_duration(context, elapsed)
        emit_event(
            "generator",
            "iteration_completed",
            slug=slug,
            iteration=iteration,
            total_passes=passes,
            exit_code=exit_code,
            elapsed_seconds=round(elapsed, 2),
            metrics=metrics_payload,
        )
        if exit_code != 0:
            reason = GENERATOR_EXIT_REASONS.get(exit_code, "Generator failure")
            payload = {
                "slug": slug,
                "iteration": iteration,
                "exit_code": exit_code,
                "reason": reason,
            }
            hint = GENERATOR_EXIT_HINTS.get(exit_code)
            if hint:
                payload["hint"] = hint
            emit_event("generator", "feature_failed", **payload)
            if exit_code not in {GENERATOR_EXIT_TIMEOUT}:
                palette = _ansi_palette()
                print(
                    f"{palette.error}[generator] Iteration {iteration} failed: {reason} (exit {exit_code}).{palette.reset}"
                )
                if hint:
                    print(f"{palette.dim}{hint}{palette.reset}")
            return exit_code

        _run_pytest_snapshot(slug, context)
        critic_ok, critic_focus = _run_critic(
            card=card,
            slug=slug,
            generation_pass=iteration,
            options=options,
            context=context,
        )
        if critic_ok:
            print(f"[generator] Critic returned DONE after pass {iteration}")
            emit_event(
                "generator",
                "critic_guidance",
                slug=slug,
                iteration=iteration,
                done=True,
                guidance="DONE",
            )
            _emit_convergence_summary(
                slug=slug,
                reason="critic_done",
                history=convergence_history,
                passes_used=iteration,
                total_budget=passes,
                total_elapsed=total_elapsed,
                critic_guidance="DONE",
            )
            emit_event(
                "generator",
                "feature_completed",
                slug=slug,
                iteration=iteration,
            )
            return 0
        if not critic_focus:
            print("[generator] Critic response empty; stopping.")
            emit_event(
                "generator",
                "critic_guidance",
                slug=slug,
                iteration=iteration,
                done=False,
                guidance="",
            )
            _emit_convergence_summary(
                slug=slug,
                reason="critic_empty",
                history=convergence_history,
                passes_used=iteration,
                total_budget=passes,
                total_elapsed=total_elapsed,
                critic_guidance="",
            )
            return 5
        print("[generator] Critic requested coverage updates:")
        print(critic_focus)
        emit_event(
            "generator",
            "critic_guidance",
            slug=slug,
            iteration=iteration,
            done=False,
            guidance=critic_focus,
        )
        focus = critic_focus

    print(f"[generator] Hit max passes ({passes}) without critic approval.")
    _emit_convergence_summary(
        slug=slug,
        reason="max_passes",
        history=convergence_history,
        passes_used=passes,
        total_budget=passes,
        total_elapsed=total_elapsed,
        critic_guidance=focus,
    )
    emit_event(
        "generator",
        "feature_failed",
        slug=slug,
        iteration=passes,
        exit_code=6,
        reason="max_passes_exhausted",
    )
    return 6


def _run_once(
    *,
    card: FeatureCard,
    slug: str,
    status: str,
    focus: str,
    generation_pass: int,
    total_passes: int,
    options: GeneratorOptions,
    context: RexContext,
) -> tuple[int, dict[str, Any] | None]:
    root = context.root
    specs_dir = root / "tests" / "feature_specs" / slug
    specs_dir.mkdir(parents=True, exist_ok=True)

    try:
        build_playbook_artifacts(card=card, context=context)
    except Exception as exc:  # pragma: no cover - defensive logging
        print(f"[generator] Failed to refresh playbook artefacts in run loop: {exc}")

    card_path = root / "documents" / "feature_cards" / f"{slug}.md"
    baseline_card_text: str | None = None
    if card_path.exists():
        try:
            baseline_card_text = card_path.read_text(encoding="utf-8")
        except OSError:
            baseline_card_text = None
    spec_trace_result: _SpecTraceResult | None = None
    card_trace_changed = False

    prompt_path = context.codex_ci_dir / "generator_prompt.txt"
    response_path = context.codex_ci_dir / "generator_response.log"
    patch_path = context.codex_ci_dir / "generator_patch.diff"

    prompt = _build_prompt(card, slug, focus, generation_pass, context)
    prompt_path.write_text(prompt, encoding="utf-8")

    cmd = (
        _split_command(options.codex_bin)
        + ["exec"]
        + _split_command(options.codex_flags)
    )
    if options.codex_model:
        cmd += ["--model", options.codex_model]
    cmd += ["--cd", str(root), "--", prompt]

    if options.verbose:
        print("[generator] Calling Codex CLI…")
    completed = _run_codex_with_progress(
        cmd,
        cwd=root,
        context=context,
        verbose=options.verbose,
        progress_label=f"Codex CLI running (pass {generation_pass}/{total_passes})",
        slug=slug,
    )
    response_path.write_text(
        (completed.stdout or "") + ("\n" if completed.stdout else ""),
        encoding="utf-8",
    )
    if options.verbose:
        print(f"[generator] Codex CLI finished in {completed.elapsed_seconds}s.")
    if completed.timeout:
        limit = completed.limit_seconds or 0
        palette = _ansi_palette()
        limit_text = f"{limit}s" if limit else "configured limit"
        print(
            f"{palette.error}[generator] Codex CLI timed out after {completed.elapsed_seconds}s (limit {limit_text}).{palette.reset}"
        )
        hint = GENERATOR_EXIT_HINTS[GENERATOR_EXIT_TIMEOUT]
        print(f"{palette.dim}{hint}{palette.reset}")
        emit_event(
            "generator",
            "codex_timeout_summary",
            slug=slug,
            level="error",
            exit_code=GENERATOR_EXIT_TIMEOUT,
            elapsed_seconds=completed.elapsed_seconds,
            limit_seconds=completed.limit_seconds,
            hint=hint,
        )
        return GENERATOR_EXIT_TIMEOUT, None
    if completed.returncode != 0:
        stderr = completed.stderr or ""
        response_path.write_text(
            response_path.read_text(encoding="utf-8") + stderr,
            encoding="utf-8",
        )
        print(stderr, file=sys.stderr)
        return 2, None

    diff_text = _extract_diff(response_path, slug)
    patch_path.write_text(diff_text, encoding="utf-8")
    entries, totals = _summarize_diff(diff_text)
    emit_event(
        "generator",
        "diff_summary",
        slug=slug,
        files=entries,
        totals=dict(totals),
    )
    if not diff_text.strip():
        print("[generator] Codex response did not contain a usable diff")
        return 3, None

    if not _enforce_patch_size(diff_text):
        return 3, None

    if not _validate_card_diff(diff_text, slug):
        print(
            "[generator] Codex attempted to modify a protected part of the Feature Card (e.g. the `status:` line)."
        )
        print(
            "[generator] Proceeding after sanitising the card diff to preserve allowed sections."
        )

    if options.verbose:
        print(f"[generator] Codex response saved to {context.relative(response_path)}")
        print(f"[generator] Applying diff from {context.relative(patch_path)}:")
        _print_diff_preview(diff_text)
        _print_diff_summary(diff_text)

    applied, patch_error = _apply_patch(patch_path, root)
    if not applied:
        print("[generator] Failed to apply Codex diff.")
        if patch_error:
            tail = "\n".join(patch_error.splitlines()[-8:])
            print(tail)
        print(
            f"[generator] Inspect {context.relative(patch_path)} for the diff and {context.relative(response_path)} for raw output."
        )
        print(
            "[generator] Tip: rerun with `./rex-codex generator --tail 200` to review the Codex response."
        )
        return 4, None
    if options.verbose:
        print("[generator] Diff applied successfully.")

    guard_ok, card_sanitized = _guard_card_edits(
        slug, root, baseline_card_text, restore_on_violation=True
    )
    if not guard_ok:
        _revert_generated_files(slug, root)
        return 7, None

    if card_path.exists():
        spec_trace_result, card_trace_changed = _update_spec_trace(
            card=card, slug=slug, context=context
        )

    if not _enforce_hermetic_tests(slug, root):
        _revert_generated_files(slug, root)
        return 7, None

    if card_trace_changed:
        run(["git", "add", str(card_path)], cwd=root, check=False)
    metrics = _iteration_metrics(spec_trace_result, card_trace_changed)
    if spec_trace_result:
        _print_spec_trace_result(spec_trace_result)
        emit_event(
            "generator",
            "spec_trace_update",
            slug=slug,
            changed=card_trace_changed,
            coverage=_spec_trace_payload(spec_trace_result),
        )
        metrics["spec_trace"] = _spec_trace_payload(spec_trace_result)
    else:
        metrics["spec_trace"] = {}

    if status == "proposed":
        _update_metadata(card, slug, context)
    print(f"[generator] Specs updated from {card.path}")
    emit_event("generator", "feature_specs_updated", slug=slug)
    return 0, metrics


def _build_prompt(
    card: FeatureCard, slug: str, focus: str, generation_pass: int, context: RexContext
) -> str:
    agents_excerpt = (context.root / "AGENTS.md").read_text(
        encoding="utf-8", errors="ignore"
    )
    card_text = card.path.read_text(encoding="utf-8")
    existing = _append_existing_tests(slug, context)
    playbook_prompt_path = context.codex_ci_dir / f"playbook_{slug}.prompt"
    playbook_block = ""
    if playbook_prompt_path.exists():
        try:
            playbook_block = playbook_prompt_path.read_text(encoding="utf-8")
        except OSError:
            playbook_block = ""
    plan_summary = _load_component_plan_summary(slug=slug, context=context)
    prompt = textwrap.dedent(
        f"""\
        You are a senior test architect.
        Produce a *unified git diff* that adds deterministic pytest specs under tests/feature_specs/<feature>/ only.

        Card edits are STRICTLY limited to appending new lines under exactly these sections:
        - ## Links
        - ## Spec Trace

        Do NOT:
        - change or add the status: line
        - modify any other card header or section
        - inject new sections anywhere else in the card
        - rewrite, delete, or reorder any existing content outside those sections
        - include any diff hunk that touches a line containing `status:`

        Only touch:
        - tests/feature_specs/<feature>/...
        - documents/feature_cards/<same-card>.md (append under the sections above only)

        If you cannot append under those sections without touching protected content, return an empty diff (no card changes).

        Guardrails:
        - Follow AGENTS.md. Do NOT modify runtime.
        - Tests must import the intended module so first failure is ModuleNotFoundError.
        - Force offline defaults (no network/time.sleep).
        - Include happy-path, env toggle, and explicit error coverage.
        Diff contract: unified diff only (start each file with 'diff --git').
        Determinism:
        - Avoid non-determinism (seed randomness, freeze time, avoid sleeps and network).
        - Prefer explicit assertions and minimal fixtures; ensure failures point to the right module.

        Feature slug: {slug}
        All updates must remain under tests/feature_specs/{slug}/ and the card document.

        --- PASS NUMBER ---
        {generation_pass}
        """
    )
    if focus:
        prompt += "\nAdditional coverage goals from previous critic pass:\n"
        prompt += f"{focus}\n"
    prompt += "\n--- BEGIN AGENTS.md EXCERPT ---\n"
    prompt += agents_excerpt
    prompt += "\n--- END AGENTS.md EXCERPT ---\n\n"
    prompt += "--- BEGIN FEATURE CARD ---\n"
    prompt += card_text
    prompt += "\n--- END FEATURE CARD ---\n"
    if playbook_block:
        prompt += "\n--- BEGIN CANONICAL PLAYBOOK SUMMARY ---\n"
        prompt += playbook_block
        if not playbook_block.endswith("\n"):
            prompt += "\n"
        prompt += "--- END CANONICAL PLAYBOOK SUMMARY ---\n"
    if plan_summary:
        prompt += "\n"
        prompt += plan_summary
    prompt += existing
    return prompt


def _load_component_plan_summary(*, slug: str, context: RexContext) -> str:
    plan_path = context.codex_ci_dir / f"component_plan_{slug}.json"
    if not plan_path.exists():
        return ""
    try:
        payload = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    components = payload.get("components")
    if not isinstance(components, list) or not components:
        return ""
    lines: list[str] = ["--- BEGIN COMPONENT PLAN SUMMARY ---"]
    for component in components:
        if not isinstance(component, dict):
            continue
        comp_id = str(component.get("id", "")).strip()
        comp_name = str(component.get("name", "")).strip()
        summary = str(component.get("summary", "")).strip()
        header = f"* Component {comp_name} [{comp_id}]"
        if summary:
            header += f": {summary}"
        lines.append(header)
        for subcomponent in component.get("subcomponents", []):
            if not isinstance(subcomponent, dict):
                continue
            sub_id = str(subcomponent.get("id", "")).strip()
            sub_name = str(subcomponent.get("name", "")).strip()
            sub_summary = str(subcomponent.get("summary", "")).strip()
            sub_header = f"  - Subcomponent {sub_name} [{sub_id}]"
            if sub_summary:
                sub_header += f": {sub_summary}"
            lines.append(sub_header)
            for test in subcomponent.get("tests", []):
                if not isinstance(test, dict):
                    continue
                test_id = str(test.get("id", "")).strip()
                question = str(test.get("question", "")).strip()
                measurement = str(test.get("measurement", "")).strip()
                assumptions = test.get("assumptions", [])
                assumptions_display = ""
                if isinstance(assumptions, list):
                    filtered = [str(item).strip() for item in assumptions if str(item).strip()]
                    if filtered:
                        assumptions_display = ", ".join(filtered)
                lines.append(f"      • Test {test_id}: {question}")
                if measurement:
                    measurement_snippet = _truncate_measurement(measurement)
                    lines.append(f"        Measurement: {measurement_snippet}")
                if assumptions_display:
                    lines.append(f"        Assumptions: {assumptions_display}")
    lines.append("--- END COMPONENT PLAN SUMMARY ---")
    return "\n".join(lines)


def _truncate_measurement(measurement: str, *, limit: int = 220) -> str:
    snippet = " ".join(measurement.split())
    if len(snippet) <= limit:
        return snippet
    return snippet[: limit - 1] + "…"


def _emit_convergence_summary(
    *,
    slug: str,
    reason: str,
    history: list[dict[str, Any]],
    passes_used: int,
    total_budget: int,
    total_elapsed: float,
    critic_guidance: str | None,
) -> None:
    if not history:
        return
    final = history[-1]
    covered = int(final.get("fci_covered", 0))
    total = int(final.get("fci_total", 0))
    missing = int(final.get("missing", max(total - covered, 0)))
    orphans = int(final.get("orphans", 0))
    coverage_ratio = float(final.get("coverage_ratio", 0.0))
    deltas = _coverage_deltas(history)
    last_delta = deltas[-1] if deltas else 0.0
    zero_delta_streak = sum(1 for delta in reversed(deltas) if abs(delta) < 1e-6)
    summary = (
        "converged: "
        f"reason={reason}; passes={passes_used}/{total_budget}; "
        f"coverage={covered}/{total} ({coverage_ratio:.2%}); "
        f"delta_last={last_delta:+.2%}; zero_delta_streak={zero_delta_streak}; "
        f"missing={missing}; orphans={orphans}; time_used={total_elapsed:.1f}s"
    )
    print(f"[generator] {summary}")
    emit_event(
        "generator",
        "convergence_summary",
        slug=slug,
        reason=reason,
        passes_used=passes_used,
        total_passes=total_budget,
        coverage_ratio=round(coverage_ratio, 4),
        covered=covered,
        total=total,
        missing=missing,
        orphans=orphans,
        coverage_delta=round(last_delta, 4),
        zero_delta_streak=zero_delta_streak,
        total_elapsed_seconds=round(total_elapsed, 2),
        critic_guidance=critic_guidance or "",
        history=history,
    )


def _coverage_deltas(history: list[dict[str, Any]]) -> list[float]:
    deltas: list[float] = []
    previous: float | None = None
    for entry in history:
        coverage = float(entry.get("coverage_ratio", 0.0))
        if previous is not None:
            deltas.append(round(coverage - previous, 4))
        previous = coverage
    return deltas


def _append_existing_tests(slug: str, context: RexContext) -> str:
    specs_dir = context.root / "tests" / "feature_specs" / slug
    if not specs_dir.exists():
        return ""
    output = ["\n--- EXISTING TEST FILES ---"]
    for path in sorted(specs_dir.glob("**/*.py")):
        try:
            snippet = path.read_text(encoding="utf-8")
        except OSError:
            continue
        output.append(f"\n### {path}")
        output.append(snippet)
    return "\n".join(output)


def _normalize_unified_diff(diff_text: str) -> str:
    """Normalize line endings and ensure git-apply-friendly trailing newline."""
    normalized = diff_text.replace("\r\n", "\n").replace("\r", "\n")
    if normalized and not normalized.endswith("\n"):
        normalized += "\n"
    return normalized


def _sanitize_card_diff(block: str) -> tuple[str | None, bool]:
    """Remove forbidden hunks from a Feature Card diff.

    Returns a tuple of (sanitized_block, removed_forbidden_hunks). When all hunks
    are stripped, sanitized_block is None.
    """

    lines = block.splitlines()
    if not lines:
        return None, False
    # Preserve header and file metadata up to the first hunk.
    prefix: list[str] = []
    index = 0
    while index < len(lines) and not lines[index].startswith("@@"):
        prefix.append(lines[index])
        index += 1

    sanitized = list(prefix)
    removed_forbidden = False

    while index < len(lines):
        hunk_start = index
        index += 1
        while index < len(lines) and not lines[index].startswith("@@"):
            index += 1
        hunk = lines[hunk_start:index]
        if any(
            line.startswith(("+", "-"))
            and re.search(r"\bstatus\s*:", line, flags=re.IGNORECASE)
            for line in hunk
        ):
            removed_forbidden = True
            continue
        sanitized.extend(hunk)

    if len(sanitized) == len(prefix):
        return None, removed_forbidden
    return "\n".join(sanitized), removed_forbidden


def _extract_diff(response_path: Path, slug: str | None) -> str:
    text = response_path.read_text(encoding="utf-8", errors="replace")
    pattern = re.compile(r"^diff --git .*$", re.MULTILINE)
    segments: list[str] = []
    allowed_doc = f"documents/feature_cards/{slug}.md" if slug else None
    allowed_prefix = f"tests/feature_specs/{slug}/" if slug else None

    matches = list(pattern.finditer(text))
    for idx, match in enumerate(matches):
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        block = text[start:end]
        header = block.splitlines()[0]
        header_match = re.match(r"^diff --git a/(.*?) b/(.*?)$", header)
        if not header_match:
            continue
        a_path, b_path = header_match.groups()
        if slug is None or any(
            (
                (allowed_prefix and candidate.startswith(allowed_prefix))
                or (allowed_doc and candidate == allowed_doc)
            )
            for candidate in (a_path, b_path)
        ):
            if allowed_doc and (
                a_path == allowed_doc or b_path == allowed_doc
            ):
                sanitized_block, removed = _sanitize_card_diff(block.rstrip("\n"))
                if removed:
                    print(
                        "[generator] Dropped forbidden Feature Card edits (status line)."
                    )
                    if slug:
                        emit_event(
                            "generator",
                            "card_guardrail_partial_accept",
                            slug=slug,
                            path=allowed_doc,
                            reason="protected_header",
                        )
                if sanitized_block:
                    segments.append(sanitized_block)
                continue
            segments.append(block.rstrip("\n"))
    return _normalize_unified_diff("\n\n".join(segments))


def _enforce_patch_size(diff_text: str) -> bool:
    max_files = int(os.environ.get("GENERATOR_MAX_FILES", DEFAULT_GENERATOR_MAX_FILES))
    max_lines = int(os.environ.get("GENERATOR_MAX_LINES", DEFAULT_GENERATOR_MAX_LINES))
    files = 0
    lines = 0
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            files += 1
        elif line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
            lines += 1
    if files > max_files or lines > max_lines:
        print(
            f"[generator] diff touches {files} files / {lines} lines "
            f"(limits {max_files}/{max_lines})"
        )
        return False
    return True


def _validate_card_diff(diff_text: str, slug: str | None) -> bool:
    if not slug:
        return True
    card_target = f"documents/feature_cards/{slug}.md"
    if card_target not in diff_text:
        return True
    card_pattern = re.compile(
        rf"^diff --git a/{re.escape(card_target)} b/{re.escape(card_target)}$",
        re.MULTILINE,
    )
    match = card_pattern.search(diff_text)
    if not match:
        return True
    section = diff_text[match.start() :]
    next_diff = section.find("\ndiff --git ")
    if next_diff != -1:
        section = section[:next_diff]
    if re.search(r"^[+-]\s*status\s*:", section, flags=re.IGNORECASE | re.MULTILINE):
        print(
            "[generator] Diff includes forbidden Feature Card header edits; they will be discarded."
        )
        return True
    return True


def _print_diff_preview(diff_text: str) -> None:
    lines = diff_text.splitlines()
    if not lines:
        print("[generator] (no diff content to preview)")
        return
    limit_env = os.environ.get("GENERATOR_DIFF_PREVIEW_LINES")
    try:
        limit = int(limit_env) if limit_env else 200
    except ValueError:
        limit = 200
    preview = lines[:limit]
    for line in preview:
        print(line)
    remaining = len(lines) - len(preview)
    if remaining > 0:
        print(f"[generator] … (diff truncated, {remaining} more lines)")


def _apply_patch(patch_path: Path, root: Path) -> tuple[bool, str | None]:
    apply_index = run(
        ["git", "apply", "--index", str(patch_path)],
        cwd=root,
        check=False,
        capture_output=True,
    )
    if apply_index.returncode == 0:
        return True, None
    print("[generator] git apply --index failed; retrying without --index")
    apply_wc = run(
        ["git", "apply", str(patch_path)],
        cwd=root,
        check=False,
        capture_output=True,
    )
    if apply_wc.returncode == 0:
        run(["git", "add", "tests", "documents/feature_cards"], cwd=root, check=False)
        return True, None
    reverse_check = run(
        ["git", "apply", "--reverse", "--check", str(patch_path)],
        cwd=root,
        check=False,
        capture_output=True,
    )
    if reverse_check.returncode == 0:
        print("[generator] Patch already present; treating as applied.")
        return True, None
    combined_error = (apply_wc.stderr or "") + (apply_wc.stdout or "")
    if not combined_error:
        combined_error = (apply_index.stderr or "") + (apply_index.stdout or "")
    return False, combined_error or None


def _guard_card_edits(
    slug: str,
    root: Path,
    baseline_text: str | None,
    *,
    restore_on_violation: bool = False,
) -> tuple[bool, bool]:
    card_path = root / "documents" / "feature_cards" / f"{slug}.md"
    if not card_path.exists():
        return True, False

    try:
        after = card_path.read_text(encoding="utf-8")
    except OSError:
        print(f"[generator] Unable to read Feature Card {card_path}")
        return False, False

    if baseline_text is not None:
        before_text = baseline_text
    else:
        try:
            before_text = run(
                ["git", "show", f"HEAD:{card_path.as_posix()}"],
                capture_output=True,
                check=True,
            ).stdout
        except subprocess.CalledProcessError:
            before_text = ""

    before_lines = before_text.splitlines()
    after_lines = after.splitlines()

    if before_lines == after_lines:
        return True, False

    allowed_headers = {"## Links", "## Spec Trace"}

    def nearest_header(lines: list[str], idx: int) -> str | None:
        for pos in range(min(idx, len(lines)) - 1, -1, -1):
            stripped = lines[pos].strip()
            if stripped.startswith("## "):
                return stripped
        return None

    def header_key(header: str | None) -> str | None:
        if header is None:
            return None
        return next(
            (h for h in allowed_headers if header.lower().startswith(h.lower())),
            None,
        )

    sm = difflib.SequenceMatcher(a=before_lines, b=after_lines)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        removed = before_lines[i1:i2]
        added = after_lines[j1:j2]
        if any(
            re.search(r"\bstatus\s*:", line, flags=re.IGNORECASE)
            for line in removed + added
        ):
            print("[generator] Card edit touches status line; abort.")
            return _handle_card_violation(
                card_path,
                baseline_text,
                restore_on_violation=restore_on_violation,
            )
        header_before = header_key(nearest_header(before_lines, i1))
        header_after = header_key(nearest_header(after_lines, j1))
        allowed_here = header_before or header_after
        if tag == "insert":
            if not allowed_here:
                header = nearest_header(after_lines, j1)
                if header is None and added:
                    candidate = added[0].strip()
                    if candidate.startswith("## "):
                        header = candidate
                    header_after = header_key(header)
                    allowed_here = header_after
            if not allowed_here:
                header = nearest_header(after_lines, j1)
                if header is None:
                    print(
                        "[generator] Card edits must appear under an allowed section."
                    )
                else:
                    print(
                        f"[generator] Card edits under section '{header}' are not permitted."
                    )
                return _handle_card_violation(
                    card_path,
                    baseline_text,
                    restore_on_violation=restore_on_violation,
                )
        elif tag in {"delete", "replace"}:
            if not allowed_here:
                header = nearest_header(before_lines, i1)
                if header is None:
                    print("[generator] Card edits may only modify allowed sections.")
                else:
                    print(
                        f"[generator] Card edits under section '{header}' are not permitted."
                    )
                return _handle_card_violation(
                    card_path,
                    baseline_text,
                    restore_on_violation=restore_on_violation,
                )
            # Modifications within allowed sections are permitted.
    return True, False


def _handle_card_violation(
    card_path: Path,
    baseline_text: str | None,
    *,
    restore_on_violation: bool,
) -> tuple[bool, bool]:
    if not restore_on_violation:
        return False, False
    restored = False
    if baseline_text is not None:
        try:
            card_path.write_text(baseline_text, encoding="utf-8")
            restored = True
        except OSError:
            restored = False
    if not restored:
        try:
            repo_root = card_path.parents[2]
        except IndexError:
            repo_root = card_path.parent
        reset = run(
            ["git", "checkout", "--", str(card_path)],
            cwd=repo_root,
            capture_output=True,
            check=False,
        )
        if reset.returncode == 0:
            restored = True
    if restored:
        print("[generator] Disallowed Feature Card edits were discarded and the baseline restored.")
        return True, True
    print(
        "[generator] Unable to restore Feature Card after detecting disallowed edits."
    )
    return False, False


def _revert_generated_files(slug: str, root: Path) -> None:
    specs_dir = root / "tests" / "feature_specs" / slug
    if specs_dir.exists():
        tracked = run(
            ["git", "ls-files", str(specs_dir)],
            cwd=root,
            capture_output=True,
            check=False,
        ).stdout.splitlines()
        for path in tracked:
            path = path.strip()
            if not path:
                continue
            run(["git", "restore", "--worktree", "--", path], cwd=root, check=False)
        run(["git", "clean", "-fd", "--", str(specs_dir)], cwd=root, check=False)
    card = root / "documents" / "feature_cards" / f"{slug}.md"
    tracked_card = run(
        ["git", "ls-files", "--error-unmatch", str(card)],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if tracked_card.returncode == 0:
        run(
            ["git", "restore", "--staged", "--worktree", "--", str(card)],
            cwd=root,
            check=False,
        )
    elif card.exists():
        card.unlink()


def _enforce_hermetic_tests(slug: str, root: Path) -> bool:
    specs_dir = root / "tests" / "feature_specs" / slug
    if not specs_dir.exists():
        return True

    from .hermetic import ensure_hermetic  # Local import to avoid cycles

    return ensure_hermetic(specs_dir)


def _run_pytest_snapshot(slug: str, context: RexContext) -> None:
    specs_dir = context.root / "tests" / "feature_specs" / slug
    log = context.codex_ci_dir / "generator_tests.log"
    if not specs_dir.exists():
        log.write_text(
            f"[generator] No tests/feature_specs/{slug} directory yet; skipping pytest snapshot.\n",
            encoding="utf-8",
        )
        emit_event(
            "generator",
            "pytest_snapshot",
            slug=slug,
            status="skipped",
            reason="no_specs_dir",
        )
        return
    ensure_python(context, quiet=True)
    env = activate_venv(context)
    env["PYTHONHASHSEED"] = env.get("PYTHONHASHSEED", "0")
    timeout_sec = int(os.environ.get("GENERATOR_SNAPSHOT_TIMEOUT", "300"))
    pytest_cmd = ["pytest", str(specs_dir), "-q", "-x", "--maxfail=1"]

    def _tail(text: str, limit: int = 4000) -> str:
        if len(text) <= limit:
            return text
        return text[-limit:]

    try:
        completed = subprocess.run(
            pytest_cmd,
            cwd=context.root,
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout_sec,
            check=True,
        )
        log.write_text("", encoding="utf-8")
        emit_event(
            "generator",
            "pytest_snapshot",
            slug=slug,
            status="passed",
            command=pytest_cmd,
            output=_tail((completed.stdout or "") + (completed.stderr or "")),
        )
    except subprocess.TimeoutExpired:
        log.write_text(
            f"[generator] Pytest snapshot timed out after {timeout_sec}s\n",
            encoding="utf-8",
        )
        emit_event(
            "generator",
            "pytest_snapshot",
            slug=slug,
            status="timeout",
            command=pytest_cmd,
            timeout_seconds=timeout_sec,
        )
    except subprocess.CalledProcessError as exc:
        output = (exc.stdout or "") + (exc.stderr or "")
        log.write_text(output, encoding="utf-8")
        emit_event(
            "generator",
            "pytest_snapshot",
            slug=slug,
            status="failed",
            command=pytest_cmd,
            output=_tail(output),
        )


def _run_critic(
    *,
    card: FeatureCard,
    slug: str,
    generation_pass: int,
    options: GeneratorOptions,
    context: RexContext,
) -> tuple[bool, str]:
    root = context.root
    prompt_path = context.codex_ci_dir / "generator_critic_prompt.txt"
    response_path = context.codex_ci_dir / "generator_critic_response.log"
    tests_log = context.codex_ci_dir / "generator_tests.log"

    tests_summary = ""
    if tests_log.exists():
        tests_summary = tests_log.read_text(encoding="utf-8", errors="replace")

    card_text = card.path.read_text(encoding="utf-8")
    files_output = []
    specs_dir = root / "tests" / "feature_specs" / slug
    if specs_dir.exists():
        for path in sorted(specs_dir.glob("**/*.py")):
            files_output.append(
                f"### {path}\n{path.read_text(encoding='utf-8', errors='replace')}"
            )

    discriminator_tail = ""
    latest_log = root / ".codex_ci_latest.log"
    if latest_log.exists():
        lines = latest_log.read_text(encoding="utf-8", errors="replace").splitlines()
        discriminator_tail = "\n".join(lines[-120:])

    prompt_sections = [
        "You are reviewing pytest specs that were just generated for the following Feature Card.",
        "Decide whether the tests fully capture the acceptance criteria and obvious negative cases.",
        "Respond in ONE of two ways:",
        "1. `DONE` (exact uppercase word) if coverage is sufficient.",
        "2. `TODO:` followed by bullet items describing additional scenarios to cover.",
        "Do NOT provide code; only guidance.",
        "",
        "--- GENERATOR PASS ---",
        str(generation_pass),
        "",
        f"Feature slug: {slug}",
        "",
        "--- FEATURE CARD ---",
        card_text,
        "",
        "--- CURRENT TEST FILES ---",
        "\n\n".join(files_output),
        "--- END TEST FILES ---",
    ]
    prompt = "\n".join(prompt_sections)
    if tests_summary:
        prompt += (
            f"\n--- PYTEST OUTPUT (tests/feature_specs/{slug}) ---\n{tests_summary}\n"
        )
    if discriminator_tail:
        prompt += "\n--- MOST RECENT DISCRIMINATOR LOG (tail) ---\n"
        prompt += discriminator_tail + "\n"

    prompt_path.write_text(prompt, encoding="utf-8")

    cmd = (
        _split_command(options.codex_bin)
        + ["exec"]
        + _split_command(options.codex_flags)
    )
    if options.codex_model:
        cmd += ["--model", options.codex_model]
    cmd += ["--cd", str(root), "--", prompt]

    completed = subprocess.run(
        cmd,
        cwd=root,
        capture_output=True,
        text=True,
    )
    response_path.write_text(
        (completed.stdout or "") + ("\n" if completed.stdout else ""),
        encoding="utf-8",
    )
    if completed.returncode != 0:
        if completed.stderr:
            response_path.write_text(
                response_path.read_text(encoding="utf-8") + completed.stderr,
                encoding="utf-8",
            )
        return False, ""

    trimmed = (completed.stdout or "").strip()
    if not trimmed:
        return False, ""
    normalized = re.sub(r"\s+", " ", trimmed.replace("`", "")).strip().upper()
    if normalized == "DONE":
        return True, ""
    return False, trimmed


def _reconcile_card(card: FeatureCard, context: RexContext) -> int:
    palette = _ansi_palette()
    print(
        f"\n{palette.accent}Reconcile Feature Card{palette.reset}: "
        f"{card.slug} ({context.relative(card.path)})"
    )
    update_active_card(context, card=card)
    result = _build_spec_trace_result(card=card, slug=card.slug, context=context)
    if result is None:
        print("  No acceptance criteria or spec shard detected yet.")
        return 0
    _print_spec_trace_result(result)
    return 1 if (result.missing or result.orphans) else 0


def _update_metadata(card: FeatureCard, slug: str, context: RexContext) -> None:
    data = load_json(context.rex_agent_file)
    feature = data.setdefault("feature", {})
    feature["active_card"] = str(card.relative_path)
    feature["active_slug"] = slug
    feature["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    dump_json(context.rex_agent_file, data)
