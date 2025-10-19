"""Generator → discriminator orchestration."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field, replace
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from .cards import card_content_hash, card_path_for, discover_cards, load_rex_agent
from .discriminator import DiscriminatorOptions, run_discriminator
from .doctor import run_doctor
from .generator import GeneratorOptions, run_generator
from .logs import show_latest_logs
from .loop_state import cleanup_loop_processes
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

_PRE_LOOP_CLEANUP_NOTES: list[str] = []
_AUDIT_EMITTED: bool = False


def _current_card_hash(context: RexContext, slug: str | None) -> str | None:
    if not slug:
        return None
    path = card_path_for(context, slug)
    return card_content_hash(path)


def _stored_card_hash(context: RexContext, slug: str | None) -> str | None:
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


def _card_drift_message(context: RexContext, slug: str | None) -> str | None:
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


def _missing_tooling(context: RexContext) -> list[str]:
    env = activate_venv(context)
    modules = ["pytest", "pytest_cov", "black", "isort", "ruff", "flake8", "mypy"]
    missing: list[str] = []
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
    notes: list[str] | None = None,
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
    notes: list[str] | None = None,
) -> list[str]:
    lines: list[str] = []
    gen_state, gen_message = _describe_generator_exit(generator_code)
    lines.append(f"Generator: {gen_state.upper()} — {gen_message}")
    disc_state, disc_message = _describe_discriminator_exit(discriminator_code)
    lines.append(f"Discriminator: {disc_state.upper()} — {disc_message}")
    if notes:
        lines.extend(notes)
    return lines


def _monitor_base_url(context: RexContext) -> str | None:
    env_url = os.environ.get("MONITOR_BASE_URL")
    if env_url:
        return env_url.rstrip("/")
    port_env = os.environ.get("MONITOR_PORT")
    if port_env and port_env.isdigit():
        return f"http://127.0.0.1:{port_env}".rstrip("/")
    port_file = context.monitor_log_dir / "monitor.port"
    try:
        payload = json.loads(port_file.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    port = payload.get("port")
    if isinstance(port, str) and port.isdigit():
        port = int(port)
    if isinstance(port, int):
        url = payload.get("url")
        if isinstance(url, str) and url.strip():
            return url.rstrip("/")
        return f"http://127.0.0.1:{port}".rstrip("/")
    return None


def _fetch_monitor_payload(url: str) -> tuple[bool, object | str]:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            data = response.read()
            text = data.decode("utf-8", errors="replace")
            if response.status != 200:
                return False, f"HTTP {response.status} from {url}: {text.strip()}"
    except urllib.error.URLError as exc:
        return False, f"Failed to fetch {url}: {exc}"
    except TimeoutError:
        return False, f"Timed out fetching {url}"
    except OSError as exc:
        return False, f"OS error fetching {url}: {exc}"
    try:
        return True, json.loads(text)
    except json.JSONDecodeError as exc:
        return False, f"Invalid JSON from {url}: {exc}"


def _truncate_text(value: object, *, limit: int = 120) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _summarize_monitor_summary(payload: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    started = payload.get("startedAt")
    last_event = payload.get("lastEventAt")
    if started:
        lines.append(f"startedAt: {started}")
    if last_event:
        lines.append(f"lastEventAt: {last_event}")
    totals = payload.get("totals")
    if isinstance(totals, dict):
        totals_line = ", ".join(
            f"{key}={totals.get(key)}" for key in ("all", "info", "warn", "error")
        )
        lines.append(f"Totals → {totals_line}")
    tasks = payload.get("tasks")
    if isinstance(tasks, dict) and tasks:
        lines.append(f"Active tasks: {len(tasks)} (showing up to 5)")
        for name, info in list(sorted(tasks.items()))[:5]:
            status = info.get("lastStatus") or "-"
            progress = info.get("progress")
            if isinstance(progress, (int, float)):
                progress_display = f"{progress * 100:.0f}%"
            else:
                progress_display = "-"
            count = info.get("count")
            lines.append(
                f"  - {name}: status={status}, progress={progress_display}, count={count}"
            )
        if len(tasks) > 5:
            lines.append(f"  … {len(tasks) - 5} more task(s)")
    component_plans = payload.get("componentPlans")
    if isinstance(component_plans, dict) and component_plans:
        lines.append("Component plans:")
        for slug in list(sorted(component_plans))[:3]:
            plan = component_plans.get(slug) or {}
            if not isinstance(plan, dict):
                continue
            status = plan.get("status") or "unknown"
            generated = plan.get("generated_at")
            component_count = len(plan.get("components") or [])
            playbook = plan.get("playbook_snapshot")
            event_emitters = 0
            feature_tags = 0
            if isinstance(playbook, dict):
                inventory = playbook.get("repository_inventory")
                if isinstance(inventory, dict):
                    event_emitters = len(inventory.get("event_emitters") or {})
                    feature_tags = len(inventory.get("feature_tags") or {})
            line = (
                f"  - {slug}: status={status}, components={component_count}, "
                f"event_emitters={event_emitters}, feature_tags={feature_tags}"
            )
            if generated:
                line += f", generated_at={generated}"
            lines.append(line)
            if isinstance(playbook, dict):
                prompt = playbook.get("prompt_block")
                if isinstance(prompt, str) and prompt.strip():
                    first_line = prompt.strip().splitlines()[0]
                    lines.append(f"      prompt: {_truncate_text(first_line, limit=90)}")
        remaining = len(component_plans) - min(len(component_plans), 3)
        if remaining > 0:
            lines.append(f"  … {remaining} additional plan(s) truncated")
    coding = payload.get("codingStrategies")
    if isinstance(coding, dict) and coding:
        lines.append(f"Coding strategy entries: {len(coding)}")
    statusbar = payload.get("statusbar")
    if statusbar:
        lines.append(f"Status bar: {_truncate_text(statusbar, limit=120)}")
    return lines or ["(no monitor summary details available)"]


def _summarize_monitor_events(payload: dict[str, Any]) -> list[str]:
    items = payload.get("items")
    count = payload.get("count")
    if not isinstance(items, list) or not items:
        return [f"No recent events (count={count or 0})."]
    display = items[-10:]
    lines = [
        f"Recent events: showing {len(display)} of {count or len(items)} (newest last)"
    ]
    for event in display:
        if not isinstance(event, dict):
            continue
        ts = event.get("ts")
        level = event.get("level") or "-"
        phase = event.get("phase")
        slug = event.get("slug")
        meta = event.get("meta")
        if isinstance(meta, dict):
            phase = phase or meta.get("phase")
            slug = slug or meta.get("slug")
        message = event.get("message") or ""
        lines.append(
            f"  - {ts} [{level}] {phase or '-'}::{slug or 'global'} {_truncate_text(message, limit=80)}"
        )
    if len(items) > len(display):
        lines.append("  … older events truncated")
    return lines


def _render_monitor_ui_text(
    summary: dict[str, Any], events: dict[str, Any] | None
) -> list[str]:
    lines: list[str] = []
    header = summary.get("statusbar")
    if isinstance(header, str) and header.strip():
        lines.append(header.strip())
    else:
        lines.append("Agent Monitor Snapshot")

    events_per_minute = summary.get("eventsPerMinute")
    if isinstance(events_per_minute, (int, float)):
        lines.append(f"{int(events_per_minute)} evt/min")

    totals = summary.get("totals")
    if isinstance(totals, dict) and totals:
        aggregate = ", ".join(
            f"{key}={totals.get(key)}" for key in ("all", "info", "warn", "error")
        )
        lines.append(f"Totals: {aggregate}")

    tasks = summary.get("tasks")
    if isinstance(tasks, dict) and tasks:
        lines.append("Tasks")
        for name, info in sorted(tasks.items()):
            status = info.get("lastStatus") or "-"
            progress = info.get("progress")
            if isinstance(progress, (int, float)):
                progress_text = f"{progress * 100:.0f}%"
            else:
                progress_text = "-"
            last_at = info.get("lastAt") or info.get("last_at") or "-"
            count = info.get("count")
            lines.append(
                f"  - {name}: status={status}, progress={progress_text}, seen={count}, last_at={last_at}"
            )

    component_plans = summary.get("componentPlans")
    if isinstance(component_plans, dict) and component_plans:
        lines.append("Component Plans")
        for slug, plan in list(sorted(component_plans.items()))[:3]:
            status = plan.get("status") or "unknown"
            generated = plan.get("generated_at") or plan.get("generatedAt") or "-"
            components = plan.get("components") or []
            lines.append(f"  Feature: {slug} — status={status}, components={len(components)}, generated={generated}")
            for component in components[:3]:
                comp_name = component.get("name") or component.get("id") or "component"
                lines.append(f"    Component: {comp_name}")
                comp_summary = component.get("summary")
                if isinstance(comp_summary, str) and comp_summary.strip():
                    lines.append(f"      Summary: {_truncate_text(comp_summary, limit=100)}")
                subcomponents = component.get("subcomponents") or []
                for sub in subcomponents[:3]:
                    sub_name = sub.get("name") or sub.get("id") or "subcomponent"
                    lines.append(f"      Subcomponent: {sub_name}")
                    sub_summary = sub.get("summary")
                    if isinstance(sub_summary, str) and sub_summary.strip():
                        lines.append(f"        Summary: {_truncate_text(sub_summary, limit=100)}")
                    tests = sub.get("tests") or []
                    for test in tests[:2]:
                        question = test.get("question") or ""
                        measurement = test.get("measurement") or ""
                        status_test = (test.get("status") or "").upper() or "-"
                        tags = test.get("tags")
                        tag_text = ""
                        if isinstance(tags, list) and tags:
                            tag_text = " " + " ".join(f"#{tag}" for tag in tags)
                        lines.append(f"        Test: {status_test}{tag_text}")
                        if question:
                            lines.append(f"          Q: {_truncate_text(question, limit=100)}")
                        if measurement:
                            lines.append(
                                f"          Measure: {_truncate_text(measurement, limit=100)}"
                            )
                    if len(tests) > 2:
                        lines.append("          … additional tests truncated")
                if len(subcomponents) > 3:
                    lines.append("      … additional subcomponents truncated")
            if len(components) > 3:
                lines.append("    … additional components truncated")
        remaining = len(component_plans) - min(len(component_plans), 3)
        if remaining > 0:
            lines.append(f"  … {remaining} additional feature plan(s) truncated")

    if isinstance(events, dict):
        items = events.get("items")
        if isinstance(items, list) and items:
            lines.append("Live Log")
            for event in items[-15:]:
                if not isinstance(event, dict):
                    continue
                ts = event.get("ts")
                formatted_time = ""
                if isinstance(ts, str):
                    try:
                        formatted_time = (
                            datetime.fromisoformat(ts.replace("Z", "+00:00"))
                            .strftime("%H:%M:%S")
                        )
                    except ValueError:
                        formatted_time = ts
                level = event.get("level") or "-"
                task = event.get("task")
                meta = event.get("meta")
                if isinstance(meta, dict):
                    task = task or meta.get("task")
                slug = event.get("slug")
                if isinstance(meta, dict):
                    slug = slug or meta.get("slug")
                message = event.get("message") or ""
                lines.append(
                    f"  - {formatted_time} [{level}] {slug or task or 'global'} :: {_truncate_text(message, limit=80)}"
                )
            if len(items) > 15:
                lines.append("  - … older events truncated")

    return lines


def _monitor_snapshot_sections(context: RexContext) -> list[tuple[str, list[str]]]:
    base_url = _monitor_base_url(context)
    if not base_url:
        return [
            (
                "Monitor Snapshot",
                ["Monitor UI unavailable (no active monitor port discovered)."],
            )
    ]
    sections: list[tuple[str, list[str]]] = []
    summary_url = f"{base_url}/api/summary"
    ok, payload = _fetch_monitor_payload(summary_url)
    summary_payload: dict[str, Any] | None = payload if ok and isinstance(payload, dict) else None
    if summary_payload is not None:
        summary_lines = _summarize_monitor_summary(summary_payload)
    else:
        summary_lines = [str(payload)]
    sections.append((f"Monitor Summary ({summary_url})", summary_lines))

    events_url = f"{base_url}/api/events?limit=20"
    ok, payload = _fetch_monitor_payload(events_url)
    events_payload: dict[str, Any] | None = payload if ok and isinstance(payload, dict) else None
    if events_payload is not None:
        events_lines = _summarize_monitor_events(events_payload)
    else:
        events_lines = [str(payload)]
    sections.append((f"Monitor Recent Events ({events_url})", events_lines))
    if summary_payload is not None:
        ui_lines = _render_monitor_ui_text(summary_payload, events_payload)
        sections.append(("Monitor UI Snapshot", ui_lines))
    return sections


def _perform_audit(context: RexContext, summary: list[str] | None = None) -> None:
    global _PRE_LOOP_CLEANUP_NOTES, _AUDIT_EMITTED
    try:
        extra_sections: list[tuple[str, list[str]]] = []
        if summary:
            extra_sections.append(("Loop Summary", summary))
        cleanup_notes: list[str] = []
        if _PRE_LOOP_CLEANUP_NOTES:
            cleanup_notes.extend(_PRE_LOOP_CLEANUP_NOTES)
        cleanup_notes.extend(cleanup_loop_processes(context))
        _PRE_LOOP_CLEANUP_NOTES = []
        if cleanup_notes:
            extra_sections.append(("Loop Cleanup Actions", cleanup_notes))
        extra_sections.extend(_monitor_snapshot_sections(context))
        create_audit_snapshot(context, extra_sections=extra_sections)
        _AUDIT_EMITTED = True
    except Exception as exc:  # pragma: no cover - filesystem/git errors
        print(f"[loop] Audit snapshot failed: {exc}")


def _print_batch_summary(entries: list[dict[str, int | None]]) -> None:
    if not entries:
        return
    palette = _ansi_palette()
    print("\n=== Loop Batch Summary =======================================")
    print(f"{'Slug':<24} {'Generator':<16} {'Discriminator':<16}")

    def format_status(code: int | None) -> str:
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


def _batch_summary_lines(entries: list[dict[str, int | None]]) -> list[str]:
    lines: list[str] = []
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
    global _PRE_LOOP_CLEANUP_NOTES, _AUDIT_EMITTED
    _AUDIT_EMITTED = False
    _PRE_LOOP_CLEANUP_NOTES = cleanup_loop_processes(context)
    for note in _PRE_LOOP_CLEANUP_NOTES:
        print(f"[loop] cleanup: {note}")
    try:
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
                print(
                    "[loop] Run `./rex-codex init` to install the development toolchain."
                )
                summary_lines = _collect_summary_lines(None, None, [f"Missing tooling: {roster}"])
                _perform_audit(context, summary_lines)
                return 1
            if options.each_features:
                return _run_each(options, context)
            return _run_single(options, context)
    except Exception as exc:
        if not _AUDIT_EMITTED:
            message = f"Loop crashed: {exc!r}"
            _perform_audit(context, [message])
        raise


def _describe_plan(options: LoopOptions, context: RexContext) -> list[str]:
    lines: list[str] = []
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
        summary_lines = _collect_summary_lines(None, None, [f"No Feature Cards with statuses: {statuses}"])
        _perform_audit(context, summary_lines)
        return 1

    batch_results: list[dict[str, int | None]] = []
    final_exit = 0

    for card in cards:
        print(f"=== rex-codex loop: processing {card.path} (slug: {card.slug}) ===")
        drift = _card_drift_message(context, card.slug)
        if drift:
            palette = _ansi_palette()
            print(f"{palette.warning}[loop] WARNING:{palette.reset} {drift}")

        generator_exit: int | None = None
        discriminator_exit: int | None = None

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
    summary_notes: list[str] = []
    seen_notes: set[str] = set()
    palette = _ansi_palette()

    def note_warning(message: str | None) -> None:
        if not message or message in seen_notes:
            return
        seen_notes.add(message)
        print(f"{palette.warning}[loop] WARNING:{palette.reset} {message}")
        summary_notes.append(message)

    slug_hint: str | None = None
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
