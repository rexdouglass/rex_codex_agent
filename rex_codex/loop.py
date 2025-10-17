"""Generator → discriminator orchestration."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field, replace
from types import SimpleNamespace
from typing import List, Optional

from .cards import card_content_hash, card_path_for, discover_cards, load_rex_agent
from .discriminator import DiscriminatorOptions, run_discriminator
from .doctor import run_doctor
from .generator import GeneratorOptions, run_generator
from .logs import show_latest_logs
from .monitoring import ensure_monitor_server
from .self_update import self_update
from .utils import (
    RexContext,
    activate_venv,
    create_audit_snapshot,
    dump_json,
    lock_file,
    run,
)

GENERATOR_EXIT_MESSAGES = {
    0: "Specs updated",
    1: "No matching Feature Cards",
    2: "Codex CLI error (see generator logs)",
    3: "Diff rejected by guardrail",
    4: "Diff failed to apply cleanly",
    5: "Critic returned empty guidance",
    6: "Max passes reached without DONE",
    7: "Guardrail rejection (card edit or hermetic failure)",
}

DISCRIMINATOR_EXIT_MESSAGES = {
    0: "Ladder passed",
    1: "Stage failure (see summary above)",
    2: "LLM disabled or patch rejected",
}


def _current_card_hash(context: RexContext, slug: str | None) -> Optional[str]:
    if not slug:
        return None
    path = card_path_for(context, slug)
    return card_content_hash(path)


def _stored_card_hash(context: RexContext, slug: str | None) -> Optional[str]:
    if not slug:
        return None
    data = load_rex_agent(context)
    feature = data.get("feature", {})
    hashes = feature.get("card_hashes", {})
    return hashes.get(slug)


def _record_card_hash(context: RexContext, slug: str | None) -> None:
    if not slug:
        return
    digest = _current_card_hash(context, slug)
    if digest is None:
        return
    data = load_rex_agent(context)
    feature = data.setdefault("feature", {})
    hashes = feature.setdefault("card_hashes", {})
    hashes[slug] = digest
    dump_json(context.rex_agent_file, data)


def _card_drift_message(context: RexContext, slug: str | None) -> Optional[str]:
    if not slug:
        return None
    stored = _stored_card_hash(context, slug)
    current = _current_card_hash(context, slug)
    if stored and current and stored != current:
        return f"Feature Card '{slug}' changed since last green; regenerate specs before proceeding."
    return None


def _load_discriminator_metadata(context: RexContext) -> dict[str, object]:
    path = context.codex_ci_dir / "discriminator_result.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):  # pragma: no cover - corruption
        return {}


def _missing_tooling(context: RexContext) -> List[str]:
    env = activate_venv(context)
    modules = ["pytest", "pytest_cov", "black", "isort", "ruff", "flake8", "mypy"]
    missing: List[str] = []
    for module in modules:
        result = run(
            ["python", "-c", f"import {module}"],
            cwd=context.root,
            env=env,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            missing.append(module)
    return missing


def _ansi_palette() -> SimpleNamespace:
    disable = bool(os.environ.get("NO_COLOR")) or not sys.stdout.isatty()
    if disable:
        return SimpleNamespace(
            success="",
            warning="",
            error="",
            label="",
            dim="",
            reset="",
        )
    return SimpleNamespace(
        success="\x1b[32m",
        warning="\x1b[33m",
        error="\x1b[31m",
        label="\x1b[36m",
        dim="\x1b[2m",
        reset="\x1b[0m",
    )


def _describe_generator_exit(code: int | None) -> tuple[str, str]:
    if code is None:
        return "skipped", "Skipped (flagged off)"
    message = GENERATOR_EXIT_MESSAGES.get(code, "Unknown generator exit")
    if code == 0:
        return "pass", message
    if code in (1, 2):
        return "warn", message
    return "fail", message


def _describe_discriminator_exit(code: int | None) -> tuple[str, str]:
    if code is None:
        return "skipped", "Skipped (flagged off)"
    message = DISCRIMINATOR_EXIT_MESSAGES.get(code, "Unknown discriminator exit")
    if code == 0:
        return "pass", message
    return "fail", message


def _render_loop_summary(
    *,
    generator_code: int | None,
    discriminator_code: int | None,
    notes: Optional[List[str]] = None,
) -> None:
    palette = _ansi_palette()
    gen_state, gen_message = _describe_generator_exit(generator_code)
    disc_state, disc_message = _describe_discriminator_exit(discriminator_code)

    def _format(state: str, label: str) -> str:
        if state == "pass":
            color = palette.success
        elif state == "warn":
            color = palette.warning
        elif state == "fail":
            color = palette.error
        else:
            color = palette.dim
        return f"{color}{label}{palette.reset}"

    print("\n=== Loop Summary =============================================")
    print(
        f"{palette.label}Generator{palette.reset}: {_format(gen_state, gen_state.upper())} — {gen_message}"
    )
    print(
        f"{palette.label}Discriminator{palette.reset}: {_format(disc_state, disc_state.upper())} — {disc_message}"
    )
    if notes:
        for note in notes:
            print(f"  - {note}")
    print("==============================================================")


def _collect_summary_lines(
    generator_code: int | None,
    discriminator_code: int | None,
    notes: Optional[List[str]] = None,
) -> List[str]:
    lines: List[str] = []
    gen_state, gen_message = _describe_generator_exit(generator_code)
    lines.append(f"Generator: {gen_state.upper()} — {gen_message}")
    disc_state, disc_message = _describe_discriminator_exit(discriminator_code)
    lines.append(f"Discriminator: {disc_state.upper()} — {disc_message}")
    if notes:
        lines.extend(notes)
    return lines


def _perform_audit(context: RexContext, summary: Optional[List[str]] = None) -> None:
    try:
        extra = [("Loop Summary", summary)] if summary else None
        create_audit_snapshot(context, extra_sections=extra)
    except Exception as exc:  # pragma: no cover - filesystem/git errors
        print(f"[loop] Audit snapshot failed: {exc}")


def _print_batch_summary(entries: List[dict[str, Optional[int]]]) -> None:
    if not entries:
        return
    palette = _ansi_palette()
    print("\n=== Loop Batch Summary =======================================")
    print(f"{'Slug':<24} {'Generator':<16} {'Discriminator':<16}")

    def format_status(code: Optional[int]) -> str:
        if code is None:
            return f"{palette.dim}SKIP{palette.reset}"
        if code == 0:
            return f"{palette.success}PASS{palette.reset}"
        return f"{palette.error}FAIL({code}){palette.reset}"

    for entry in entries:
        slug = entry.get("slug", "")
        gen = format_status(entry.get("generator"))
        disc = format_status(entry.get("discriminator"))
        print(f"{slug:<24} {gen:<16} {disc:<16}")
    print("==============================================================")


def _batch_summary_lines(entries: List[dict[str, Optional[int]]]) -> List[str]:
    lines: List[str] = []
    for entry in entries:
        slug = entry.get("slug", "")
        gen = entry.get("generator")
        disc = entry.get("discriminator")
        gen_state, gen_message = _describe_generator_exit(gen)
        disc_state, disc_message = _describe_discriminator_exit(disc)
        lines.append(f"{slug}: Generator {gen_state.upper()} — {gen_message}")
        lines.append(f"{slug}: Discriminator {disc_state.upper()} — {disc_message}")
    return lines


@dataclass
class LoopOptions:
    generator_options: GeneratorOptions = field(default_factory=GeneratorOptions)
    discriminator_options: DiscriminatorOptions = field(
        default_factory=DiscriminatorOptions
    )
    run_generator: bool = True
    run_discriminator: bool = True
    run_feature: bool = True
    run_global: bool = True
    each_features: bool = False
    perform_self_update: bool = True
    explain: bool = False
    verbose: bool = True
    tail_lines: int = 0
    continue_on_fail: bool = False


def run_loop(options: LoopOptions, *, context: RexContext | None = None) -> int:
    context = context or RexContext.discover()
    ensure_monitor_server(context, open_browser=True)
    if options.explain:
        for line in _describe_plan(options, context):
            print(line)
    if options.perform_self_update:
        self_update()
    options.generator_options.verbose = options.verbose
    options.discriminator_options.verbose = options.verbose
    lock_path = context.codex_ci_dir / "rex.lock"
    with lock_file(lock_path):
        run_doctor()
        missing_tools = _missing_tooling(context)
        if missing_tools:
            roster = ", ".join(missing_tools)
            print(f"[loop] Required tooling missing: {roster}")
            print("[loop] Run `./rex-codex init` to install the development toolchain.")
            return 1
        if options.each_features:
            return _run_each(options, context)
        return _run_single(options, context)


def _describe_plan(options: LoopOptions, context: RexContext) -> List[str]:
    lines: List[str] = []
    statuses = options.generator_options.statuses or ["proposed"]
    lines.append(
        f"Self-update: {'enabled' if options.perform_self_update else 'disabled'} "
        "(honours REX_AGENT_NO_UPDATE)"
    )
    lines.append(
        f"Generator phase: {'enabled' if options.run_generator else 'skipped'}"
    )
    if options.run_generator:
        if options.generator_options.card_path:
            target = str(options.generator_options.card_path)
        else:
            target = ", ".join(statuses)
        lines.append(f"  target: {target}")
        lines.append(f"  iterate-each: {'yes' if options.each_features else 'no'}")
    lines.append(
        f"Discriminator phase: {'enabled' if options.run_discriminator else 'skipped'}"
    )
    if options.run_discriminator:
        lines.append(f"  feature shard: {'yes' if options.run_feature else 'no'}")
        lines.append(f"  global sweep: {'yes' if options.run_global else 'no'}")
        lines.append(
            f"  LLM runtime edits: "
            f"{'disabled' if options.discriminator_options.disable_llm else 'enabled'}"
        )
    if options.each_features and options.run_generator:
        cards = discover_cards(
            statuses=options.generator_options.statuses, context=context
        )
        if cards:
            preview = ", ".join(card.slug for card in cards[:5])
            if len(cards) > 5:
                preview += f", … (+{len(cards) - 5} more)"
            lines.append(f"  queued cards: {preview}")
        else:
            lines.append("  queued cards: none")
    return lines


def _run_each(options: LoopOptions, context: RexContext) -> int:
    cards = discover_cards(statuses=options.generator_options.statuses, context=context)
    if not cards:
        statuses = ", ".join(options.generator_options.statuses)
        print(f"[loop] No Feature Cards with statuses: {statuses}")
        return 1

    batch_results: List[dict[str, Optional[int]]] = []
    final_exit = 0

    for card in cards:
        print(f"=== rex-codex loop: processing {card.path} (slug: {card.slug}) ===")
        drift = _card_drift_message(context, card.slug)
        if drift:
            palette = _ansi_palette()
            print(f"{palette.warning}[loop] WARNING:{palette.reset} {drift}")

        generator_exit: Optional[int] = None
        discriminator_exit: Optional[int] = None

        if options.run_generator:
            generator_opts = replace(options.generator_options, card_path=card.path)
            result = run_generator(generator_opts, context=context)
            generator_exit = result
            if result != 0:
                _maybe_tail_logs("generator", options.tail_lines, context)
                print(f"[loop] Generator failed on {card.path} (exit {result})")
                if not options.continue_on_fail:
                    summary_lines = _batch_summary_lines(
                        [
                            {
                                "slug": card.slug,
                                "generator": result,
                                "discriminator": None,
                            }
                        ]
                    )
                    _perform_audit(context, summary_lines)
                    return result
                final_exit = final_exit or result
                batch_results.append(
                    {"slug": card.slug, "generator": result, "discriminator": None}
                )
                continue
            if options.verbose:
                _announce_log(context, "generator_response.log")
        else:
            print("[loop] Generator skipped.")

        if options.run_discriminator:
            exit_code = _run_discriminator_phases(options, card.slug, context)
            discriminator_exit = exit_code
            metadata = _load_discriminator_metadata(context)
            if metadata.get("coverage_failed"):
                palette = _ansi_palette()
                target = metadata.get("coverage_targets") or "coverage targets"
                threshold = metadata.get("coverage_threshold")
                target_display = str(target).strip() or "coverage targets"
                message = f"Coverage shortfall on {target_display}"
                if threshold:
                    message += f" (min {threshold}%)"
                print(f"{palette.warning}[loop] WARNING:{palette.reset} {message}")
            if exit_code != 0:
                if not options.continue_on_fail:
                    summary_lines = _batch_summary_lines(
                        [
                            {
                                "slug": card.slug,
                                "generator": generator_exit,
                                "discriminator": exit_code,
                            }
                        ]
                    )
                    _perform_audit(context, summary_lines)
                    return exit_code
                final_exit = final_exit or exit_code
        else:
            print("[loop] Discriminator skipped.")

        batch_results.append(
            {
                "slug": card.slug,
                "generator": generator_exit,
                "discriminator": discriminator_exit,
            }
        )

    if options.continue_on_fail:
        _print_batch_summary(batch_results)
    summary_lines = _batch_summary_lines(batch_results)
    _perform_audit(context, summary_lines)
    return final_exit


def _run_single(options: LoopOptions, context: RexContext) -> int:
    summary_notes: List[str] = []
    seen_notes: set[str] = set()
    palette = _ansi_palette()

    def note_warning(message: Optional[str]) -> None:
        if not message or message in seen_notes:
            return
        seen_notes.add(message)
        print(f"{palette.warning}[loop] WARNING:{palette.reset} {message}")
        summary_notes.append(message)

    slug_hint: Optional[str] = None
    if options.generator_options.card_path:
        slug_hint = options.generator_options.card_path.stem
    else:
        slug_hint = _discover_active_slug(context)
    note_warning(_card_drift_message(context, slug_hint))

    generator_code: int | None = None
    if options.run_generator:
        print("=== rex-codex loop: generator phase ===")
        generator_code = run_generator(options.generator_options, context=context)
        if generator_code == 0:
            print("[loop] Generator produced new specs; running discriminator…")
            if options.verbose:
                _announce_log(context, "generator_response.log")
        elif generator_code == 1:
            print(
                "[loop] Generator found no matching Feature Cards; running discriminator anyway."
            )
        else:
            print(f"[loop] Generator failed (exit {generator_code}); aborting.")
            _maybe_tail_logs("generator", options.tail_lines, context)
            _render_loop_summary(generator_code=generator_code, discriminator_code=None)
            summary_lines = _collect_summary_lines(generator_code, None, summary_notes)
            _perform_audit(context, summary_lines)
            return generator_code
    else:
        print("[loop] Generator skipped; running discriminator only.")
        generator_code = None

    discriminator_code: int | None = None
    exit_code = 0
    if options.run_discriminator:
        slug = _discover_active_slug(context) or slug_hint
        note_warning(_card_drift_message(context, slug))
        print("=== rex-codex loop: discriminator phase ===")
        discriminator_code = _run_discriminator_phases(options, slug, context)
        exit_code = discriminator_code
        if discriminator_code == 0 and options.verbose:
            _announce_log(context, "latest_discriminator.log")
        metadata = _load_discriminator_metadata(context)
        if metadata.get("coverage_failed"):
            target = metadata.get("coverage_targets") or "coverage targets"
            threshold = metadata.get("coverage_threshold")
            target_display = str(target).strip() or "coverage targets"
            note = f"Coverage shortfall on {target_display}"
            if threshold:
                note += f" (min {threshold}%)"
            note_warning(note)
    else:
        print("[loop] Discriminator skipped; generator phase complete.")
        exit_code = generator_code if generator_code not in (None, 0, 1) else 0

    _render_loop_summary(
        generator_code=generator_code,
        discriminator_code=discriminator_code,
        notes=summary_notes,
    )
    summary_lines = _collect_summary_lines(
        generator_code, discriminator_code, summary_notes
    )
    _perform_audit(context, summary_lines)
    return exit_code


def _run_discriminator_phases(
    options: LoopOptions, slug: str | None, context: RexContext
) -> int:
    if options.run_feature:
        if slug:
            feature_opts = replace(
                options.discriminator_options, mode="feature", slug=slug
            )
            result = run_discriminator(feature_opts, context=context)
            if result != 0:
                _maybe_tail_logs("discriminator", options.tail_lines, context)
                return result
        else:
            print(
                "[loop] No active feature slug; skipping feature-only discriminator run."
            )
    if options.run_global:
        global_opts = replace(options.discriminator_options, mode="global", slug=None)
        result = run_discriminator(global_opts, context=context)
        if result != 0:
            _maybe_tail_logs("discriminator", options.tail_lines, context)
        else:
            _record_card_hash(context, slug)
        return result
    print("[loop] Global discriminator run skipped by flag.")
    return 0


def _discover_active_slug(context: RexContext) -> str | None:
    data = load_rex_agent(context)
    feature = data.get("feature", {})
    slug = feature.get("active_slug")
    if slug:
        return slug
    cards = discover_cards(statuses=["proposed"], context=context)
    return cards[0].slug if cards else None


def _maybe_tail_logs(kind: str, lines: int, context: RexContext) -> None:
    if lines <= 0:
        return
    if kind == "generator":
        show_latest_logs(context, lines=lines, generator=True)
    elif kind == "discriminator":
        show_latest_logs(context, lines=lines, discriminator=True)


def _announce_log(context: RexContext, filename: str) -> None:
    path = context.codex_ci_dir / filename
    if path.exists():
        print(f"[loop] Logs: {context.relative(path)}")
