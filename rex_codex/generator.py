"""Deterministic spec generator implemented in Python."""

from __future__ import annotations

import difflib
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from .cards import FeatureCard, discover_cards, load_rex_agent, update_active_card
from .config import (
    DEFAULT_GENERATOR_MAX_FILES,
    DEFAULT_GENERATOR_MAX_LINES,
    REPO_ROOT,
)
from .self_update import self_update
from .utils import (
    RexContext,
    RexError,
    activate_venv,
    dump_json,
    ensure_dir,
    ensure_python,
    lock_file,
    load_json,
    print_header,
    repo_root,
    run,
)


@dataclass
class GeneratorOptions:
    continuous: bool = True
    max_passes: int = int(os.environ.get("GENERATOR_MAX_PASSES", "5"))
    focus: str = ""
    card_path: Optional[Path] = None
    iterate_all: bool = False
    statuses: List[str] = field(default_factory=lambda: ["proposed"])
    codex_bin: str = os.environ.get("CODEX_BIN", "npx --yes @openai/codex")
    codex_flags: str = os.environ.get("CODEX_FLAGS", "--yolo")
    codex_model: str = os.environ.get("MODEL", "")


def parse_statuses(raw: str | None) -> List[str]:
    if not raw:
        return ["proposed"]
    tokens = [piece.strip().lower() for piece in raw.split(",") if piece.strip()]
    return tokens or ["proposed"]


def _split_command(raw: str) -> List[str]:
    import shlex

    return shlex.split(raw)


def run_generator(options: GeneratorOptions, *, context: RexContext | None = None) -> int:
    context = context or RexContext.discover()
    self_update()
    ensure_dir(context.codex_ci_dir)
    lock_path = context.codex_ci_dir / "rex_generator.lock"
    with lock_file(lock_path):
        ensure_python(context, quiet=True)
        cards: List[FeatureCard]
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
            return 1

        if options.iterate_all:
            for card in cards:
                print(f"[generator] === Processing card {card.path} ===")
                if _process_card(card, options, context) != 0:
                    return 1
            return 0

        return _process_card(cards[0], options, context)


def _process_card(card: FeatureCard, options: GeneratorOptions, context: RexContext) -> int:
    slug = card.slug
    status = card.status
    focus = options.focus
    passes = options.max_passes if options.continuous else 1

    update_active_card(context, card=card)

    for iteration in range(1, passes + 1):
        print(f"[generator] Iteration {iteration}/{passes} (slug: {slug}, status: {status})")
        exit_code, critic_feedback = _run_once(
            card=card,
            slug=slug,
            status=status,
            focus=focus,
            generation_pass=iteration,
            options=options,
            context=context,
        )
        if exit_code != 0:
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
            return 0
        if not critic_focus:
            print("[generator] Critic response empty; stopping.")
            return 5
        print("[generator] Critic requested coverage updates:")
        print(critic_focus)
        focus = critic_focus

    print(f"[generator] Hit max passes ({passes}) without critic approval.")
    return 6


def _run_once(
    *,
    card: FeatureCard,
    slug: str,
    status: str,
    focus: str,
    generation_pass: int,
    options: GeneratorOptions,
    context: RexContext,
) -> Tuple[int, Optional[str]]:
    root = context.root
    specs_dir = root / "tests" / "feature_specs" / slug
    specs_dir.mkdir(parents=True, exist_ok=True)

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

    completed = subprocess.run(
        cmd,
        cwd=root,
        text=True,
        capture_output=True,
    )
    response_path.write_text(
        (completed.stdout or "") + ("\n" if completed.stdout else ""),
        encoding="utf-8",
    )
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
    if not diff_text.strip():
        print("[generator] Codex response did not contain a usable diff")
        return 3, None

    if not _enforce_patch_size(diff_text):
        return 3, None

    if not _apply_patch(patch_path, root):
        print("[generator] Failed to apply Codex diff")
        return 4, None

    if not _guard_card_edits(slug, root):
        _revert_generated_files(slug, root)
        return 7, None

    if not _enforce_hermetic_tests(slug, root):
        _revert_generated_files(slug, root)
        return 7, None

    if status == "proposed":
        _update_metadata(card, slug, context)
    print(f"[generator] Specs updated from {card.path}")
    return 0, None


def _build_prompt(card: FeatureCard, slug: str, focus: str, generation_pass: int, context: RexContext) -> str:
    agents_excerpt = (context.root / "AGENTS.md").read_text(encoding="utf-8", errors="ignore")
    card_text = card.path.read_text(encoding="utf-8")
    existing = _append_existing_tests(slug, context)
    prompt = textwrap.dedent(
        f"""\
        You are a senior test architect.
        Produce a *unified git diff* that adds deterministic pytest specs under tests/feature_specs/<feature>/.
        Only touch:
        - tests/feature_specs/<feature>/...
        - documents/feature_cards/<same-card>.md  (to update state/links once tests are created)

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
    prompt += existing
    return prompt


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


def _extract_diff(response_path: Path, slug: str) -> str:
    text = response_path.read_text(encoding="utf-8", errors="replace")
    pattern = re.compile(r"^diff --git .*$", re.MULTILINE)
    segments: List[str] = []
    allowed_doc = f"documents/feature_cards/{slug}.md"
    allowed_prefix = f"tests/feature_specs/{slug}/"

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
        if any(
            candidate.startswith(allowed_prefix) or candidate == allowed_doc
            for candidate in (a_path, b_path)
        ):
            segments.append(block.strip())
    return "\n\n".join(segments)


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


def _apply_patch(patch_path: Path, root: Path) -> bool:
    apply_index = run(
        ["git", "apply", "--index", str(patch_path)],
        cwd=root,
        check=False,
    )
    if apply_index.returncode == 0:
        return True
    print("[generator] git apply --index failed; retrying without --index")
    apply_wc = run(["git", "apply", str(patch_path)], cwd=root, check=False)
    if apply_wc.returncode == 0:
        run(["git", "add", "tests", "documents/feature_cards"], cwd=root, check=False)
        return True
    return False


def _guard_card_edits(slug: str, root: Path) -> bool:
    card_path = root / "documents" / "feature_cards" / f"{slug}.md"
    if not card_path.exists():
        return True
    diff_cached = run(
        ["git", "diff", "--cached", "--unified=0", "--", str(card_path)],
        cwd=root,
        capture_output=True,
        check=False,
    ).stdout
    if not diff_cached.strip():
        diff_cached = run(
            ["git", "diff", "--unified=0", "--", str(card_path)],
            cwd=root,
            capture_output=True,
            check=False,
        ).stdout
    if not diff_cached.strip():
        return True

    if re.search(r"^[+-]\s*status\s*:", diff_cached, flags=re.IGNORECASE | re.MULTILINE):
        print("[generator] Card edit touches status line; abort.")
        return False

    try:
        before = run(
            ["git", "show", f"HEAD:{card_path.as_posix()}"],
            capture_output=True,
            check=True,
        ).stdout
    except subprocess.CalledProcessError:
        before = ""
    after = card_path.read_text(encoding="utf-8")

    before_lines = before.splitlines()
    after_lines = after.splitlines()
    allowed_headers = {"## Links", "## Spec Trace"}

    def nearest_header(idx: int) -> Optional[str]:
        for pos in range(idx - 1, -1, -1):
            stripped = after_lines[pos].strip()
            if stripped.startswith("## "):
                return stripped
        return None

    sm = difflib.SequenceMatcher(a=before_lines, b=after_lines)
    for tag, _i1, _i2, j1, j2 in sm.get_opcodes():
        if tag in {"delete", "replace"}:
            print("[generator] Card edits may only append new lines inside allowed sections.")
            return False
        if tag == "insert":
            added = after_lines[j1:j2]
            for line in added:
                if re.search(r"\bstatus\s*:", line, flags=re.IGNORECASE):
                    print("[generator] Card edit attempts to add a status line; abort.")
                    return False
            header = nearest_header(j1)
            if header is None and added:
                candidate = added[0].strip()
                if candidate.startswith("## "):
                    header = candidate
            if header is None:
                print("[generator] Card edits must appear under an allowed section.")
                return False
            header_key = next(
                (h for h in allowed_headers if header.lower().startswith(h.lower())),
                None,
            )
            if header_key is None:
                print(f"[generator] Card edits under section '{header}' are not permitted.")
                return False
    return True


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
        run(["git", "restore", "--staged", "--worktree", "--", str(card)], cwd=root, check=False)
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
        return
    ensure_python(context, quiet=True)
    env = activate_venv(context)
    env["PYTHONHASHSEED"] = env.get("PYTHONHASHSEED", "0")
    timeout_sec = int(os.environ.get("GENERATOR_SNAPSHOT_TIMEOUT", "300"))
    pytest_cmd = ["pytest", str(specs_dir), "-q", "-x", "--maxfail=1"]
    try:
        run(pytest_cmd, cwd=context.root, env=env, check=True)
        log.write_text("", encoding="utf-8")
    except subprocess.CalledProcessError as exc:
        output = (exc.stdout or "") + (exc.stderr or "")
        if exc.returncode == 124:
            output += f"\n[generator] Pytest snapshot timed out after {timeout_sec}s\n"
        log.write_text(output, encoding="utf-8")


def _run_critic(
    *,
    card: FeatureCard,
    slug: str,
    generation_pass: int,
    options: GeneratorOptions,
    context: RexContext,
) -> Tuple[bool, str]:
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
            files_output.append(f"### {path}\n{path.read_text(encoding='utf-8', errors='replace')}")

    discriminator_tail = ""
    latest_log = root / ".codex_ci_latest.log"
    if latest_log.exists():
        lines = latest_log.read_text(encoding="utf-8", errors="replace").splitlines()
        discriminator_tail = "\n".join(lines[-120:])

    prompt = textwrap.dedent(
        f"""\
        You are reviewing pytest specs that were just generated for the following Feature Card.
        Decide whether the tests fully capture the acceptance criteria and obvious negative cases.
        Respond in ONE of two ways:
        1. `DONE` (exact uppercase word) if coverage is sufficient.
        2. `TODO:` followed by bullet items describing additional scenarios to cover.
        Do NOT provide code; only guidance.

        --- GENERATOR PASS ---
        {generation_pass}

        Feature slug: {slug}

        --- FEATURE CARD ---
        {card_text}

        --- CURRENT TEST FILES ---
        {'\\n\\n'.join(files_output)}
        --- END TEST FILES ---
        """
    )
    if tests_summary:
        prompt += f"\n--- PYTEST OUTPUT (tests/feature_specs/{slug}) ---\n{tests_summary}\n"
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


def _update_metadata(card: FeatureCard, slug: str, context: RexContext) -> None:
    data = load_json(context.rex_agent_file)
    feature = data.setdefault("feature", {})
    feature["active_card"] = str(card.relative_path)
    feature["active_slug"] = slug
    feature["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    dump_json(context.rex_agent_file, data)
