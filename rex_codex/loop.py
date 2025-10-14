"""Generator → discriminator orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import List

from .cards import discover_cards, load_rex_agent
from .discriminator import DiscriminatorOptions, run_discriminator
from .doctor import run_doctor
from .generator import GeneratorOptions, run_generator
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


def run_loop(options: LoopOptions, *, context: RexContext | None = None) -> int:
    context = context or RexContext.discover()
    self_update()
    lock_path = context.codex_ci_dir / "rex.lock"
    with lock_file(lock_path):
        run_doctor()
        if options.each_features:
            return _run_each(options, context)
        return _run_single(options, context)


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
                print(f"[loop] Generator failed on {card.path} (exit {result})")
                return result
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
        elif gen_status == 1:
            print("[loop] Generator found no matching Feature Cards; running discriminator anyway.")
        else:
            print(f"[loop] Generator failed (exit {gen_status}); aborting.")
            return gen_status
    else:
        print("[loop] Generator skipped; running discriminator only.")

    if options.run_discriminator:
        slug = _discover_active_slug(context)
        print("=== rex-codex loop: discriminator phase ===")
        return _run_discriminator_phases(options, slug, context)
    print("[loop] Discriminator skipped; generator phase complete.")
    return 0


def _run_discriminator_phases(options: LoopOptions, slug: str | None, context: RexContext) -> int:
    if options.run_feature:
        if slug:
            feature_opts = replace(options.discriminator_options, mode="feature", slug=slug)
            result = run_discriminator(feature_opts, context=context)
            if result != 0:
                return result
        else:
            print("[loop] No active feature slug; skipping feature-only discriminator run.")
    if options.run_global:
        global_opts = replace(options.discriminator_options, mode="global", slug=None)
        return run_discriminator(global_opts, context=context)
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
