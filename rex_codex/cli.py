"""Command-line interface for rex-codex."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .burn import burn_repo
from .cards import create_card, discover_cards, lint_all_cards, sanitise_slug
from .discriminator import DiscriminatorOptions, run_discriminator
from .doctor import run_doctor
from .generator import GeneratorOptions, parse_statuses, run_generator
from .init import run_init
from .logs import show_latest_logs
from .loop import LoopOptions, run_loop
from .self_update import self_update
from .status import render_status
from .uninstall import uninstall_agent
from .utils import RexContext, prompt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rex-codex", description="Codex automation scaffold")
    parser.add_argument("--version", action="version", version=f"rex-codex {__version__}")
    sub = parser.add_subparsers(dest="command")

    # init
    init_parser = sub.add_parser("init", help="Seed guardrails and tooling")
    init_parser.add_argument("--no-self-update", action="store_true", help="Skip self-update before initializing")

    # generator
    gen_parser = sub.add_parser("generator", help="Generate deterministic specs for Feature Cards")
    gen_parser.add_argument("card", nargs="?", help="Feature Card path to focus on")
    gen_parser.add_argument("--single-pass", action="store_true", help="Run generator once and stop")
    gen_parser.add_argument("--max-passes", type=int, default=None, help="Maximum passes before giving up")
    gen_parser.add_argument("--focus", default="", help="Seed additional coverage focus")
    gen_parser.add_argument("--include-accepted", action="store_true", help="Consider cards with status: accepted")
    gen_parser.add_argument("--status", dest="statuses", default=None, help="Comma-separated statuses to include")
    gen_parser.add_argument("--each", action="store_true", help="Process each matching Feature Card sequentially")

    # discriminator
    disc_parser = sub.add_parser("discriminator", help="Run the automation ladder")
    disc_mode = disc_parser.add_mutually_exclusive_group()
    disc_mode.add_argument("--feature-only", action="store_true", help="Run only the active feature shard")
    disc_mode.add_argument("--global", dest="global_only", action="store_true", help="Run the global sweep")
    disc_parser.add_argument("--single-pass", action="store_true", help="Run one pass and stop")
    disc_parser.add_argument("--max-passes", type=int, default=None, help="Maximum passes before giving up")
    disc_parser.add_argument("--feature", dest="feature_slug", help="Override feature slug")

    # loop
    loop_parser = sub.add_parser("loop", help="Generator → discriminator orchestration")
    loop_parser.add_argument("--skip-generator", action="store_true")
    loop_parser.add_argument("--generator-only", action="store_true")
    loop_parser.add_argument("--discriminator-only", action="store_true")
    loop_parser.add_argument("--skip-feature", action="store_true")
    loop_parser.add_argument("--skip-global", action="store_true")
    loop_parser.add_argument("--feature-only", action="store_true")
    loop_parser.add_argument("--global-only", action="store_true")
    loop_parser.add_argument("--include-accepted", action="store_true")
    loop_parser.add_argument("--status", dest="statuses", default=None)
    loop_parser.add_argument("--each", action="store_true", help="Process each matching Feature Card sequentially")

    # card commands
    card_parser = sub.add_parser("card", help="Feature Card helpers")
    card_sub = card_parser.add_subparsers(dest="card_command")
    card_new = card_sub.add_parser("new", help="Create a new Feature Card")
    card_new.add_argument("slug", nargs="?", help="Slug for the card (derived from title if omitted)")
    card_new.add_argument("--title", help="Human-friendly title")
    card_new.add_argument("--summary", help="Summary paragraph")
    card_new.add_argument("--acceptance", action="append", default=[], help="Acceptance criteria bullet (use multiple times)")

    card_list = card_sub.add_parser("list", help="List Feature Cards")
    card_list.add_argument("--status", dest="statuses", default=None, help="Comma separated statuses to filter")

    card_sub.add_parser("validate", help="Validate Feature Card formatting")

    # logs & status
    sub.add_parser("logs", help="Tail recent discriminator/generator logs")
    sub.add_parser("status", help="Show rex-codex status summary")

    # doctor
    sub.add_parser("doctor", help="Print environment diagnostics")

    # burn/uninstall
    burn_parser = sub.add_parser("burn", help="Reset repository contents (preserve .git)")
    burn_parser.add_argument("--yes", action="store_true", help="Skip confirmation")
    burn_parser.add_argument("--purge-agent", action="store_true", help="Remove .rex_agent as well")
    burn_parser.add_argument("--dry-run", action="store_true", help="Preview deletions without executing")

    uninstall_parser = sub.add_parser("uninstall", help="Remove the rex-codex agent")
    uninstall_parser.add_argument("--yes", action="store_true", help="Skip confirmation")
    uninstall_parser.add_argument("--keep-wrapper", action="store_true", help="Preserve the ./rex-codex wrapper")

    # self-update
    update_parser = sub.add_parser("self-update", help="Force an agent self-update")
    update_parser.add_argument("--channel", choices=["stable", "main"], default=None, help="Update channel")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    context = RexContext.discover()

    if args.command == "init":
        run_init(context=context, perform_self_update=not args.no_self_update)
        return 0

    if args.command == "generator":
        options = GeneratorOptions()
        if args.single_pass:
            options.continuous = False
        if args.max_passes is not None:
            options.max_passes = args.max_passes
        options.focus = args.focus
        if args.statuses:
            options.statuses = parse_statuses(args.statuses)
        elif args.include_accepted:
            options.statuses.append("accepted")
        if args.card:
            options.card_path = Path(args.card)
        options.iterate_all = args.each
        return run_generator(options, context=context)

    if args.command == "discriminator":
        options = DiscriminatorOptions()
        if args.feature_only:
            options.mode = "feature"
        elif args.global_only:
            options.mode = "global"
        if args.single_pass:
            options.continuous = False
        if args.max_passes is not None:
            options.max_passes = args.max_passes
        if args.feature_slug:
            options.slug = args.feature_slug
        return run_discriminator(options, context=context)

    if args.command == "loop":
        loop_opts = LoopOptions()
        if args.skip_generator:
            loop_opts.run_generator = False
        if args.generator_only:
            loop_opts.run_discriminator = False
        if args.discriminator_only:
            loop_opts.run_generator = False
        if args.skip_feature:
            loop_opts.run_feature = False
        if args.skip_global:
            loop_opts.run_global = False
        if args.feature_only:
            loop_opts.run_feature = True
            loop_opts.run_global = False
        if args.global_only:
            loop_opts.run_feature = False
            loop_opts.run_global = True
        if args.statuses:
            loop_opts.generator_options.statuses = parse_statuses(args.statuses)
        elif args.include_accepted:
            statuses = loop_opts.generator_options.statuses
            if "accepted" not in statuses:
                statuses.append("accepted")
        loop_opts.each_features = args.each
        return run_loop(loop_opts, context=context)

    if args.command == "card":
        if args.card_command == "new":
            slug = args.slug
            title = args.title or prompt("Title: ")
            slug = slug or sanitise_slug(title)
            summary = args.summary or prompt("Summary: ")
            acceptance = args.acceptance or []
            if not acceptance:
                print("Enter acceptance criteria (blank line to finish):")
                while True:
                    item = prompt("- ")
                    if not item.strip():
                        break
                    acceptance.append(item.strip())
            card = create_card(context, slug=slug, title=title, summary=summary, acceptance=acceptance)
            print(f"[card] Created {card.path}")
            return 0
        if args.card_command == "list":
            statuses = parse_statuses(args.statuses) if args.statuses else None
            cards = discover_cards(statuses=statuses, context=context)
            if not cards:
                print("[card] No Feature Cards found.")
                return 0
            for card in cards:
                print(f"{card.status:>9}  {card.slug}  {card.path}")
            return 0
        if args.card_command == "validate":
            errors = lint_all_cards(context)
            if not errors:
                print("[card] All Feature Cards look good.")
                return 0
            for error in errors:
                print(error)
            return 1
        parser.error("card requires a sub-command (new/list/validate)")

    if args.command == "logs":
        show_latest_logs(context)
        return 0

    if args.command == "status":
        render_status(context)
        return 0

    if args.command == "doctor":
        run_doctor()
        return 0

    if args.command == "burn":
        burn_repo(force=args.yes, purge_agent=args.purge_agent, dry_run=args.dry_run, context=context)
        return 0

    if args.command == "uninstall":
        uninstall_agent(force=args.yes, keep_wrapper=args.keep_wrapper, context=context)
        return 0

    if args.command == "self-update":
        self_update(channel=args.channel)
        return 0

    parser.print_help()
    return 1


def app() -> None:  # pragma: no cover - Typer compatibility shim
    raise SystemExit(main())


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
