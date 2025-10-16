"""Command-line interface for rex-codex."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import __version__
from .burn import burn_repo
from .cards import (
    archive_card,
    create_card,
    discover_cards,
    lint_all_cards,
    prune_spec_directories,
    rename_card,
    sanitise_slug,
    spec_directory,
    split_card,
)
from .discriminator import DiscriminatorOptions, run_discriminator
from .doctor import run_doctor
from .generator import GeneratorOptions, parse_statuses, run_generator
from .init import run_init
from .install import run_install
from .logs import show_latest_logs
from .loop import LoopOptions, run_loop
from .self_update import self_update
from .status import render_status
from .uninstall import uninstall_agent
from .utils import RexContext, prompt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rex-codex", description="Codex automation scaffold"
    )
    parser.add_argument(
        "--version", action="version", version=f"rex-codex {__version__}"
    )
    sub = parser.add_subparsers(dest="command")

    # install / init
    install_parser = sub.add_parser(
        "install", help="Install or refresh the rex-codex agent"
    )
    install_parser.add_argument(
        "--force",
        action="store_true",
        help="Remove existing .rex_agent before installing",
    )
    install_parser.add_argument(
        "--channel", help="Install a specific channel/tag (e.g. stable, main)"
    )
    install_parser.add_argument(
        "--skip-init",
        action="store_true",
        help="Skip running ./rex-codex init after install",
    )
    install_parser.add_argument(
        "--skip-doctor",
        action="store_true",
        help="Skip running ./rex-codex doctor after install",
    )

    init_parser = sub.add_parser("init", help="Seed guardrails and tooling")
    init_parser.add_argument(
        "--no-self-update",
        action="store_true",
        help="Skip self-update before initializing",
    )

    # generator
    gen_parser = sub.add_parser(
        "generator", help="Generate deterministic specs for Feature Cards"
    )
    gen_parser.add_argument("card", nargs="?", help="Feature Card path to focus on")
    gen_parser.add_argument(
        "--single-pass", action="store_true", help="Run generator once and stop"
    )
    gen_parser.add_argument(
        "--max-passes", type=int, default=None, help="Maximum passes before giving up"
    )
    gen_parser.add_argument(
        "--focus", default="", help="Seed additional coverage focus"
    )
    gen_parser.add_argument(
        "--include-accepted",
        action="store_true",
        help="Consider cards with status: accepted",
    )
    gen_parser.add_argument(
        "--status",
        dest="statuses",
        default=None,
        help="Comma-separated statuses to include",
    )
    gen_parser.add_argument(
        "--each",
        action="store_true",
        help="Process each matching Feature Card sequentially",
    )
    gen_parser.add_argument(
        "--reconcile",
        action="store_true",
        help="Report Spec Trace coverage without writing diffs",
    )
    gen_parser.add_argument(
        "--tail",
        type=int,
        default=0,
        help="Tail log output (N lines) when the generator fails",
    )
    gen_parser.add_argument(
        "--ui",
        choices=["monitor", "snapshot", "off", "auto"],
        default=None,
        help="Generator HUD mode (default: monitor when attached to a TTY)",
    )
    gen_verbose = gen_parser.add_mutually_exclusive_group()
    gen_verbose.add_argument(
        "--verbose", action="store_true", help="Print Codex diffs (default)"
    )
    gen_verbose.add_argument(
        "--quiet", action="store_true", help="Suppress Codex diff output"
    )

    # discriminator
    disc_parser = sub.add_parser("discriminator", help="Run the automation ladder")
    disc_mode = disc_parser.add_mutually_exclusive_group()
    disc_mode.add_argument(
        "--feature-only", action="store_true", help="Run only the active feature shard"
    )
    disc_mode.add_argument(
        "--global", dest="global_only", action="store_true", help="Run the global sweep"
    )
    disc_llm = disc_parser.add_mutually_exclusive_group()
    disc_llm.add_argument(
        "--enable-llm", action="store_true", help="Allow guarded runtime edits via LLM"
    )
    disc_llm.add_argument(
        "--disable-llm", action="store_true", help="Disable LLM runtime edits (default)"
    )
    disc_parser.add_argument(
        "--single-pass", action="store_true", help="Run one pass and stop"
    )
    disc_parser.add_argument(
        "--max-passes", type=int, default=None, help="Maximum passes before giving up"
    )
    disc_parser.add_argument(
        "--feature", dest="feature_slug", help="Override feature slug"
    )
    disc_verbose = disc_parser.add_mutually_exclusive_group()
    disc_verbose.add_argument(
        "--verbose",
        action="store_true",
        help="Print discriminator debug output (default)",
    )
    disc_verbose.add_argument(
        "--quiet", action="store_true", help="Reduce discriminator verbosity"
    )
    disc_parser.add_argument(
        "--tail",
        type=int,
        default=0,
        help="Tail log output (N lines) when the discriminator fails",
    )
    disc_parser.add_argument(
        "--stage-timeout",
        type=int,
        default=None,
        help="Timeout (seconds) for each discriminator stage",
    )

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
    loop_parser.add_argument(
        "--each",
        action="store_true",
        help="Process each matching Feature Card sequentially",
    )
    loop_parser.add_argument(
        "--no-self-update", action="store_true", help="Skip self-update before running"
    )
    loop_parser.add_argument(
        "--explain", action="store_true", help="Describe planned actions and exit"
    )
    loop_verbose = loop_parser.add_mutually_exclusive_group()
    loop_verbose.add_argument(
        "--verbose",
        action="store_true",
        help="Print generator/discriminator debug output (default)",
    )
    loop_verbose.add_argument(
        "--quiet", action="store_true", help="Reduce generator/discriminator output"
    )
    loop_parser.add_argument(
        "--tail", type=int, default=0, help="Tail log output (N lines) after failures"
    )
    loop_llm = loop_parser.add_mutually_exclusive_group()
    loop_llm.add_argument(
        "--enable-llm", action="store_true", help="Allow guarded runtime edits via LLM"
    )
    loop_llm.add_argument(
        "--disable-llm", action="store_true", help="Disable LLM runtime edits"
    )
    loop_parser.add_argument(
        "--stage-timeout",
        type=int,
        default=None,
        help="Timeout (seconds) applied to discriminator stages",
    )
    loop_parser.add_argument(
        "--continue-on-fail",
        action="store_true",
        help="Process remaining Feature Cards even if one fails",
    )

    # card commands
    card_parser = sub.add_parser("card", help="Feature Card helpers")
    card_sub = card_parser.add_subparsers(dest="card_command")
    card_new = card_sub.add_parser("new", help="Create a new Feature Card")
    card_new.add_argument(
        "slug", nargs="?", help="Slug for the card (derived from title if omitted)"
    )
    card_new.add_argument("--title", help="Human-friendly title")
    card_new.add_argument("--summary", help="Summary paragraph")
    card_new.add_argument(
        "--acceptance",
        action="append",
        default=[],
        help="Acceptance criteria bullet (use multiple times)",
    )

    card_list = card_sub.add_parser("list", help="List Feature Cards")
    card_list.add_argument(
        "--status",
        dest="statuses",
        default=None,
        help="Comma separated statuses to filter",
    )

    card_sub.add_parser("validate", help="Validate Feature Card formatting")

    card_rename = card_sub.add_parser(
        "rename", help="Rename a Feature Card and its spec shard"
    )
    card_rename.add_argument("old_slug")
    card_rename.add_argument("new_slug")

    card_split_parser = card_sub.add_parser(
        "split", help="Split a Feature Card into two new cards"
    )
    card_split_parser.add_argument("source_slug")
    card_split_parser.add_argument("slug_a")
    card_split_parser.add_argument("slug_b")

    card_archive_parser = card_sub.add_parser(
        "archive", help="Mark a Feature Card as archived (status: archived)"
    )
    card_archive_parser.add_argument("slug")

    card_prune = card_sub.add_parser(
        "prune-specs", help="Remove orphan or archived spec shards"
    )
    card_prune.add_argument(
        "--yes", action="store_true", help="Delete without confirmation prompts"
    )
    arch_group = card_prune.add_mutually_exclusive_group()
    arch_group.add_argument(
        "--archived",
        dest="include_archived",
        action="store_true",
        help="Include archived cards (default)",
    )
    arch_group.add_argument(
        "--no-archived",
        dest="include_archived",
        action="store_false",
        help="Skip archived cards",
    )
    card_prune.set_defaults(include_archived=True)

    # logs & status
    logs_parser = sub.add_parser(
        "logs", help="Tail recent discriminator/generator logs"
    )
    logs_parser.add_argument(
        "--generator", action="store_true", help="Show generator logs"
    )
    logs_parser.add_argument(
        "--discriminator", action="store_true", help="Show discriminator logs"
    )
    logs_parser.add_argument(
        "--lines", type=int, default=120, help="Number of log lines to display"
    )
    logs_parser.add_argument(
        "--follow", action="store_true", help="Stream log output until interrupted"
    )
    status_parser = sub.add_parser("status", help="Show rex-codex status summary")
    status_parser.add_argument(
        "--json", action="store_true", help="Emit raw JSON summary"
    )

    hud_parser = sub.add_parser(
        "hud", help="Render a one-shot HUD snapshot from event streams"
    )
    hud_parser.add_argument(
        "phase", choices=["generator", "discriminator"], help="HUD phase to render"
    )
    hud_parser.add_argument(
        "--slug", help="Feature slug to focus (defaults to active card)"
    )
    hud_parser.add_argument("--events", help="Override events JSONL path")

    # doctor
    sub.add_parser("doctor", help="Print environment diagnostics")

    # burn/uninstall
    burn_parser = sub.add_parser(
        "burn", help="Reset repository contents (preserve .git)"
    )
    burn_parser.add_argument("--yes", action="store_true", help="Skip confirmation")
    burn_parser.add_argument(
        "--purge-agent", action="store_true", help="Remove .rex_agent as well"
    )
    burn_parser.add_argument(
        "--dry-run", action="store_true", help="Preview deletions without executing"
    )

    uninstall_parser = sub.add_parser("uninstall", help="Remove the rex-codex agent")
    uninstall_parser.add_argument(
        "--yes", "--force", action="store_true", dest="force", help="Skip confirmation"
    )
    uninstall_parser.add_argument(
        "--keep-wrapper", action="store_true", help="Preserve the ./rex-codex wrapper"
    )

    # self-update
    update_parser = sub.add_parser("self-update", help="Force an agent self-update")
    update_parser.add_argument(
        "--channel", choices=["stable", "main"], default=None, help="Update channel"
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    context = RexContext.discover()

    if args.command == "install":
        run_install(
            force=args.force,
            channel=args.channel,
            run_init_after=not args.skip_init,
            run_doctor_after=not args.skip_doctor,
            context=context,
        )
        return 0

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
        options.verbose = not args.quiet
        if args.verbose:
            options.verbose = True
        options.tail_lines = args.tail
        options.reconcile_only = args.reconcile
        if args.ui:
            options.ui_mode = "monitor" if args.ui == "auto" else args.ui
        exit_code = run_generator(options, context=context)
        if exit_code != 0 and args.tail:
            show_latest_logs(context, lines=args.tail, generator=True)
        return exit_code

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
        if args.enable_llm:
            options.disable_llm = False
        elif args.disable_llm:
            options.disable_llm = True
        options.verbose = not args.quiet
        if args.verbose:
            options.verbose = True
        if args.stage_timeout is not None:
            options.stage_timeout = args.stage_timeout
        exit_code = run_discriminator(options, context=context)
        if exit_code != 0 and args.tail:
            show_latest_logs(context, lines=args.tail, discriminator=True)
        return exit_code

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
        loop_opts.perform_self_update = not args.no_self_update
        loop_opts.explain = args.explain
        loop_opts.verbose = not args.quiet
        if args.verbose:
            loop_opts.verbose = True
        loop_opts.tail_lines = args.tail
        if args.enable_llm:
            loop_opts.discriminator_options.disable_llm = False
        elif args.disable_llm:
            loop_opts.discriminator_options.disable_llm = True
        if args.stage_timeout is not None:
            loop_opts.discriminator_options.stage_timeout = args.stage_timeout
        loop_opts.continue_on_fail = args.continue_on_fail
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
            card = create_card(
                context, slug=slug, title=title, summary=summary, acceptance=acceptance
            )
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
        if args.card_command == "rename":
            card = rename_card(context, args.old_slug, args.new_slug)
            spec_dir = spec_directory(context, card.slug)
            print(f"[card] Renamed Feature Card → {card.slug} ({card.path})")
            if spec_dir.exists():
                print(f"[card] Spec shard relocated to {spec_dir}")
            return 0
        if args.card_command == "split":
            card_a, card_b = split_card(
                context, args.source_slug, args.slug_a, args.slug_b
            )
            print(f"[card] Created {card_a.slug} ({card_a.path})")
            print(f"[card] Created {card_b.slug} ({card_b.path})")
            print(
                "[card] Review acceptance criteria for each new card and adjust tests as needed."
            )
            return 0
        if args.card_command == "archive":
            card = archive_card(context, args.slug)
            print(f"[card] Updated {card.path} to status: {card.status}")
            return 0
        if args.card_command == "prune-specs":
            removed = prune_spec_directories(
                context,
                include_archived=args.include_archived,
                assume_yes=args.yes,
            )
            if not removed:
                print("[card prune-specs] No spec shards removed.")
            return 0
        parser.error(
            "card requires a sub-command (new/list/validate/rename/split/archive/prune-specs)"
        )

    if args.command == "logs":
        show_latest_logs(
            context,
            lines=args.lines,
            generator=args.generator,
            discriminator=args.discriminator,
            follow=args.follow,
        )
        return 0

    if args.command == "status":
        render_status(context, json_output=getattr(args, "json", False))
        return 0

    if args.command == "doctor":
        run_doctor()
        return 0

    if args.command == "hud":
        from .hud import render_hud

        render_hud(
            phase=args.phase, slug=args.slug, events_file=args.events, context=context
        )
        return 0

    if args.command == "burn":
        burn_repo(
            force=args.yes,
            purge_agent=args.purge_agent,
            dry_run=args.dry_run,
            context=context,
        )
        return 0

    if args.command == "uninstall":
        uninstall_agent(
            force=args.force, keep_wrapper=args.keep_wrapper, context=context
        )
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
