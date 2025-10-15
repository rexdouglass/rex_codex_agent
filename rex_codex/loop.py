"""Generator → discriminator orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import List

from .cards import discover_cards, load_rex_agent
from .discriminator import DiscriminatorOptions, run_discriminator
from .doctor import run_doctor
from .generator import GeneratorOptions, run_generator
from .logs import show_latest_logs
from .self_update import self_update
from .utils import RexContext, lock_file


@dataclass
class LoopOptions:
    generator_options: GeneratorOptions = field(default_factory=GeneratorOptions)
    discriminator_options: DiscriminatorOptions = field(default_factory=DiscriminatorOptions)
    run_generator: bool = True
    run_discriminator: bool = True
    run_feature: bool = True
    run_global: bool = True
    each_features: bool = False
    perform_self_update: bool = True
    explain: bool = False
    verbose: bool = False
    tail_lines: int = 0


def run_loop(options: LoopOptions, *, context: RexContext | None = None) -> int:
    context = context or RexContext.discover()
    if options.explain:
        for line in _describe_plan(options, context):
            print(line)
    if options.perform_self_update:
        self_update()
    if options.verbose:
        options.generator_options.verbose = True
        options.discriminator_options.verbose = True
    lock_path = context.codex_ci_dir / "rex.lock"
    with lock_file(lock_path):
        run_doctor()
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
    lines.append(f"Generator phase: {'enabled' if options.run_generator else 'skipped'}")
    if options.run_generator:
        if options.generator_options.card_path:
            target = str(options.generator_options.card_path)
        else:
            target = ", ".join(statuses)
        lines.append(f"  target: {target}")
        lines.append(f"  iterate-each: {'yes' if options.each_features else 'no'}")
    lines.append(f"Discriminator phase: {'enabled' if options.run_discriminator else 'skipped'}")
    if options.run_discriminator:
        lines.append(f"  feature shard: {'yes' if options.run_feature else 'no'}")
        lines.append(f"  global sweep: {'yes' if options.run_global else 'no'}")
        lines.append(
            f"  LLM runtime edits: "
            f"{'disabled' if options.discriminator_options.disable_llm else 'enabled'}"
        )
    if options.each_features and options.run_generator:
        cards = discover_cards(statuses=options.generator_options.statuses, context=context)
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
    for card in cards:
        print(f"=== rex-codex loop: processing {card.path} (slug: {card.slug}) ===")
        if options.run_generator:
            generator_opts = replace(options.generator_options, card_path=card.path)
            result = run_generator(generator_opts, context=context)
            if result != 0:
                _maybe_tail_logs("generator", options.tail_lines, context)
                print(f"[loop] Generator failed on {card.path} (exit {result})")
                return result
            if options.verbose:
                _announce_log(context, "generator_response.log")
        else:
            print("[loop] Generator skipped.")
        if options.run_discriminator:
            exit_code = _run_discriminator_phases(options, card.slug, context)
            if exit_code != 0:
                return exit_code
    return 0


def _run_single(options: LoopOptions, context: RexContext) -> int:
    gen_status = 1
    if options.run_generator:
        print("=== rex-codex loop: generator phase ===")
        gen_status = run_generator(options.generator_options, context=context)
        if gen_status == 0:
            print("[loop] Generator produced new specs; running discriminator…")
            if options.verbose:
                _announce_log(context, "generator_response.log")
        elif gen_status == 1:
            print("[loop] Generator found no matching Feature Cards; running discriminator anyway.")
        else:
            print(f"[loop] Generator failed (exit {gen_status}); aborting.")
            _maybe_tail_logs("generator", options.tail_lines, context)
            return gen_status
    else:
        print("[loop] Generator skipped; running discriminator only.")

    if options.run_discriminator:
        slug = _discover_active_slug(context)
        print("=== rex-codex loop: discriminator phase ===")
        result = _run_discriminator_phases(options, slug, context)
        if result == 0 and options.verbose:
            _announce_log(context, "latest_discriminator.log")
        return result
    print("[loop] Discriminator skipped; generator phase complete.")
    return 0


def _run_discriminator_phases(options: LoopOptions, slug: str | None, context: RexContext) -> int:
    if options.run_feature:
        if slug:
            feature_opts = replace(options.discriminator_options, mode="feature", slug=slug)
            result = run_discriminator(feature_opts, context=context)
            if result != 0:
                _maybe_tail_logs("discriminator", options.tail_lines, context)
                return result
        else:
            print("[loop] No active feature slug; skipping feature-only discriminator run.")
    if options.run_global:
        global_opts = replace(options.discriminator_options, mode="global", slug=None)
        result = run_discriminator(global_opts, context=context)
        if result != 0:
            _maybe_tail_logs("discriminator", options.tail_lines, context)
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
