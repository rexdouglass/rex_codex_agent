"""Staged automation ladder (discriminator) implemented in Python."""

from __future__ import annotations

import glob
import hashlib
import os
import re
import shlex
import shutil
import subprocess
import textwrap
import time
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Iterable, List, Mapping, Optional, Sequence, Tuple
from collections import OrderedDict

from .cards import discover_cards, find_orphan_spec_slugs, load_rex_agent
from .config import (
    AGENT_SRC,
    DEFAULT_COVERAGE_MIN,
    DEFAULT_DISCRIMINATOR_MAX_FILES,
    DEFAULT_DISCRIMINATOR_MAX_LINES,
    DEFAULT_PROTECTED_PATHS,
    DEFAULT_RUNTIME_ALLOWLIST,
)
from .events import emit_event
from .generator import _split_command
from .self_update import self_update
from .utils import (
    RexContext,
    RexError,
    activate_venv,
    dump_json,
    ensure_dir,
    ensure_python,
    ensure_requirements_installed,
    load_json,
    lock_file,
    repo_root,
    run,
)


@dataclass
class DiscriminatorOptions:
    mode: str = "global"  # "feature" or "global"
    slug: Optional[str] = None
    continuous: bool = True
    max_passes: int = int(os.environ.get("DISCRIMINATOR_MAX_PASSES", "25"))
    disable_llm: bool = os.environ.get("DISABLE_LLM", "1") == "1"
    codex_bin: str = os.environ.get("CODEX_BIN", "npx --yes @openai/codex")
    codex_flags: str = os.environ.get("CODEX_FLAGS", "--yolo")
    codex_model: str = os.environ.get("MODEL", "")
    verbose: bool = True
    stage_timeout: Optional[int] = None


@dataclass
class Stage:
    identifier: str
    description: str
    command: str


@dataclass
class StageGroup:
    title: str
    stages: List[Stage]


def _write_discriminator_result(context: RexContext, payload: Mapping[str, object]) -> None:
    path = context.codex_ci_dir / "discriminator_result.json"
    dump_json(path, payload)


def _ansi_palette() -> SimpleNamespace:
    disable = bool(os.environ.get("NO_COLOR")) or not sys.stdout.isatty()
    if disable:
        return SimpleNamespace(
            green="",
            red="",
            yellow="",
            blue="",
            cyan="",
            magenta="",
            dim="",
            reset="",
            bold="",
            error="",
        )
    return SimpleNamespace(
        green="\x1b[32m",
        red="\x1b[31m",
        yellow="\x1b[33m",
        blue="\x1b[34m",
        cyan="\x1b[36m",
        magenta="\x1b[35m",
        dim="\x1b[2m",
        reset="\x1b[0m",
        bold="\x1b[1m",
        error="\x1b[31m",
    )


def run_discriminator(options: DiscriminatorOptions, *, context: RexContext | None = None) -> int:
    context = context or RexContext.discover()
    self_update()
    ensure_dir(context.codex_ci_dir)
    lock_path = context.codex_ci_dir / "rex_discriminator.lock"
    with lock_file(lock_path):
        return _run_locked(options, context)


def _run_locked(options: DiscriminatorOptions, context: RexContext) -> int:
    ensure_python(context, quiet=True)
    requirements_template = AGENT_SRC / "templates" / "requirements-dev.txt"
    ensure_requirements_installed(context, requirements_template)
    env = activate_venv(context)
    env.setdefault("PYTHONHASHSEED", "0")
    if "COVERAGE_TARGETS" not in env and (context.root / "src").exists():
        env["COVERAGE_TARGETS"] = "src"
    env.setdefault("COVERAGE_MIN", str(DEFAULT_COVERAGE_MIN))
    if options.stage_timeout:
        env["DISCRIMINATOR_STAGE_TIMEOUT"] = str(options.stage_timeout)

    slug = options.slug or _discover_active_slug(context)
    mode = options.mode
    if mode == "feature" and not slug:
        print("[discriminator] No active feature slug; falling back to global sweep")
        mode = "global"

    log_path = context.codex_ci_dir / "latest_discriminator.log"
    latest_log_path = context.root / ".codex_ci_latest.log"
    if options.verbose:
        print(f"[discriminator] Logs will be written to {context.relative(log_path)}")

    passes = 0
    run_counter = 0
    while passes < options.max_passes:
        passes += 1
        attempt = 1
        run_counter += 1
        print(f"=== rex-codex discriminator ({mode}) pass {passes}/{options.max_passes} ===")
        log_path.write_text("", encoding="utf-8")
        latest_log_path.write_text("", encoding="utf-8")

        ok = _run_stage_plan(
            mode=mode,
            slug=slug,
            env=env,
            context=context,
            log_path=log_path,
            latest_log_path=latest_log_path,
            pass_number=passes,
            run_id=run_counter,
            attempt=attempt,
        )
        if ok:
            print(f"✅ Green: {mode} suite passed")
            _record_success(mode, slug, context, env)
            return 0

        if not options.continuous:
            print("[discriminator] Stopping after first failing pass (--single-pass).")
            return 1

        # Mechanical fixes
        attempt += 1
        next_run_id = run_counter + 1
        if _apply_mechanical_fixes(
            mode,
            slug,
            context,
            env,
            pass_number=passes,
            attempt=attempt,
            run_id=next_run_id,
        ):
            run_counter = next_run_id
            if _run_stage_plan(
                mode=mode,
                slug=slug,
                env=env,
                context=context,
                log_path=log_path,
                latest_log_path=latest_log_path,
                pass_number=passes,
                run_id=run_counter,
                attempt=attempt,
            ):
                print("✅ Green after mechanical fixes")
                _record_success(mode, slug, context, env)
                return 0
            attempt += 1

        future_run_id = run_counter + 1
        llm_event_context = {
            "slug": slug,
            "mode": mode,
            "pass_number": passes,
            "run_id": run_counter,
            "next_run_id": future_run_id,
            "attempt": attempt,
        }
        if options.disable_llm:
            emit_event(
                "discriminator",
                "llm_patch_decision",
                accepted=False,
                reason="llm_disabled",
                **llm_event_context,
            )
            print("LLM disabled; stopping after mechanical fixes.")
            return 2

        if not _ensure_node_present():
            emit_event(
                "discriminator",
                "llm_patch_decision",
                accepted=False,
                reason="node_missing",
                **llm_event_context,
            )
            print("[discriminator] Node.js not found; forcing DISABLE_LLM=1.")
            return 2

        test_count_before = _collect_test_count(mode, slug, context, env)
        snapshot = _snapshot_protected_paths(context)
        _invoke_llm_once(options, mode, slug, context, env, log_path, latest_log_path)

        changed = _detect_protected_changes(snapshot, context)
        if changed:
            emit_event(
                "discriminator",
                "llm_patch_decision",
                accepted=False,
                reason="protected_paths_modified",
                paths=changed,
                **llm_event_context,
            )
            print("[discriminator] Aborting pass; LLM patch touched protected paths.")
            _revert_paths(changed, context)
            return 2

        if not _reject_non_runtime_changes(context):
            emit_event(
                "discriminator",
                "llm_patch_decision",
                accepted=False,
                reason="non_runtime_changes",
                **llm_event_context,
            )
            print("[discriminator] Aborting pass; LLM patch touched non-runtime paths.")
            _revert_all_changes(context)
            return 2

        if _git_diff_is_empty(context):
            emit_event(
                "discriminator",
                "llm_patch_decision",
                accepted=False,
                reason="no_diff",
                **llm_event_context,
            )
            print("No diff from LLM; aborting.")
            return 2

        test_count_after = _collect_test_count(mode, slug, context, env)
        if (
            test_count_before is not None
            and test_count_after is not None
            and test_count_after < test_count_before
        ):
            emit_event(
                "discriminator",
                "llm_patch_decision",
                accepted=False,
                reason="test_count_decreased",
                before=test_count_before,
                after=test_count_after,
                **llm_event_context,
            )
            print(
                f"[discriminator] Test collection decreased ({test_count_before} -> {test_count_after}); rejecting LLM patch."
            )
            _revert_all_changes(context)
            return 2

        if not _enforce_patch_size(context):
            emit_event(
                "discriminator",
                "llm_patch_decision",
                accepted=False,
                reason="patch_size_exceeded",
                **llm_event_context,
            )
            print("[discriminator] Aborting pass; LLM patch exceeded size limits.")
            return 2

        run(["git", "add", "-A"], cwd=context.root, check=False)
        commit_message = f"chore(rex-codex): discriminator {mode} pass {passes}"
        run(
            ["git", "commit", "-m", commit_message],
            cwd=context.root,
            check=False,
        )
        emit_event(
            "discriminator",
            "llm_patch_decision",
            accepted=True,
            reason="committed",
            commit_message=commit_message,
            **llm_event_context,
        )
        _record_success(mode, slug, context, env)
    print(f"Hit max passes ({options.max_passes}) without going green")
    return 1


def _discover_active_slug(context: RexContext) -> Optional[str]:
    data = load_rex_agent(context)
    feature = data.get("feature", {})
    slug = feature.get("active_slug")
    if slug:
        return slug
    cards = discover_cards(statuses=["proposed"], context=context)
    return cards[0].slug if cards else None


def _run_stage_plan(
    *,
    mode: str,
    slug: Optional[str],
    env: dict[str, str],
    context: RexContext,
    log_path: Path,
    latest_log_path: Path,
    pass_number: int,
    run_id: int,
    attempt: int,
) -> bool:
    pytest_flags = _configure_pytest_flags(mode, env, context)
    specs_dir = context.root / "tests" / "feature_specs" / (slug or "")
    palette = _ansi_palette()
    if mode == "feature":
        if not slug:
            print("[discriminator] Feature mode requested but no slug provided.")
            _write_discriminator_result(
                context,
                {
                    "mode": mode,
                    "slug": slug,
                    "ok": False,
                    "coverage_failed": False,
                    "coverage_targets": None,
                    "coverage_threshold": None,
                    "first_failure": {
                        "identifier": None,
                        "description": "Feature slug missing",
                        "command": "",
                    },
                },
            )
            return False
        if not specs_dir.is_dir():
            msg = f"[discriminator] Feature specs directory {specs_dir} missing"
            print(msg)
            with open(latest_log_path, "a", encoding="utf-8") as fh:
                fh.write(msg + "\n")
            _write_discriminator_result(
                context,
                {
                    "mode": mode,
                    "slug": slug,
                    "ok": False,
                    "coverage_failed": False,
                    "coverage_targets": None,
                    "coverage_threshold": None,
                    "first_failure": {
                        "identifier": None,
                        "description": "Feature specs directory missing",
                        "command": "",
                    },
                },
            )
            return False

    groups = _build_stage_groups(mode, slug, pytest_flags, env, context)
    overall_ok = True
    summary: List[dict[str, object]] = []
    first_failure: Optional[dict[str, object]] = None
    coverage_failed = False
    coverage_min = env.get("COVERAGE_MIN")
    coverage_targets_config = env.get("COVERAGE_TARGETS")
    coverage_targets = coverage_targets_config or "."
    coverage_targets_display: Optional[str] = coverage_targets_config or (coverage_targets if coverage_min else None)
    coverage_threshold = coverage_min
    emit_event(
        "discriminator",
        "run_started",
        slug=slug,
        mode=mode,
        pass_number=pass_number,
        run_id=run_id,
        attempt=attempt,
        stage_groups=[group.title for group in groups],
    )
    print_lock = threading.Lock()

    for group in groups:
        print("------------------------------------------------------------")
        print(f"{palette.blue}Stage: {group.title}{palette.reset}")
        executable_stages = [stage for stage in group.stages if stage.command.strip()]
        if not executable_stages:
            continue

        def _handle_stage_result(stage: Stage, ok: bool, elapsed: float, tail: str) -> None:
            nonlocal overall_ok, first_failure, coverage_failed
            status = f"{palette.green}PASS{palette.reset}" if ok else f"{palette.red}FAIL{palette.reset}"
            timing = f"{palette.dim}({elapsed:.2f}s){palette.reset}"
            print(f"    {palette.dim}[{stage.identifier}]{palette.reset} {status} {timing}")
            record = {
                "group": group.title,
                "identifier": stage.identifier,
                "description": stage.description,
                "command": stage.command,
                "elapsed": elapsed,
                "ok": ok,
                "tail": tail,
            }
            summary.append(record)
            failure_reason = _summarize_failure_reason(tail) if not ok else ""
            emit_event(
                "discriminator",
                "stage_end",
                slug=slug,
                mode=mode,
                pass_number=pass_number,
                run_id=run_id,
                attempt=attempt,
                identifier=stage.identifier,
                description=stage.description,
                command=stage.command,
                group=group.title,
                ok=ok,
                elapsed=elapsed,
                tail=tail,
                failure_reason=failure_reason,
            )
            if stage.identifier.startswith("04.") or "Coverage" in group.title:
                percent = _parse_coverage_percent(tail)
                if percent is not None:
                    emit_event(
                        "discriminator",
                        "coverage_update",
                        slug=slug,
                        mode=mode,
                        pass_number=pass_number,
                        run_id=run_id,
                        attempt=attempt,
                        identifier=stage.identifier,
                        percent=percent,
                        threshold=coverage_threshold,
                        targets=_split_targets_for_events(coverage_targets),
                    )
            if not ok:
                overall_ok = False
                if first_failure is None:
                    first_failure = record
                    banner = f"[discriminator] First failure: [{stage.identifier}] {stage.description}"
                    print(f"{palette.error}{banner}{palette.reset}")
                    with open(latest_log_path, "a", encoding="utf-8") as fh:
                        fh.write(banner + "\n")
                if stage.identifier.startswith("04.") or "Coverage" in group.title:
                    coverage_failed = True

        if group.title.startswith("Level 06"):
            for stage in executable_stages:
                emit_event(
                    "discriminator",
                    "stage_start",
                    slug=slug,
                    mode=mode,
                    pass_number=pass_number,
                    run_id=run_id,
                    attempt=attempt,
                    identifier=stage.identifier,
                    description=stage.description,
                    command=stage.command,
                    group=group.title,
                )
            for stage, ok, elapsed, tail in _run_parallel_stage_group(
                executable_stages,
                env,
                context,
                log_path,
                latest_log_path,
                print_lock,
            ):
                _handle_stage_result(stage, ok, elapsed, tail)
        else:
            for stage in executable_stages:
                emit_event(
                    "discriminator",
                    "stage_start",
                    slug=slug,
                    mode=mode,
                    pass_number=pass_number,
                    run_id=run_id,
                    attempt=attempt,
                    identifier=stage.identifier,
                    description=stage.description,
                    command=stage.command,
                    group=group.title,
                )
                ok, elapsed, tail = _execute_stage(
                    stage,
                    env,
                    context,
                    log_path,
                    latest_log_path,
                )
                _handle_stage_result(stage, ok, elapsed, tail)
    result = f"{palette.green}PASS{palette.reset}" if overall_ok else f"{palette.red}FAIL{palette.reset}"
    print(f"  Result: [{result}]")
    _render_stage_summary(summary, overall_ok, first_failure, palette, context, mode)
    payload: dict[str, object] = {
        "mode": mode,
        "slug": slug,
        "ok": overall_ok,
        "coverage_failed": coverage_failed,
        "coverage_targets": coverage_targets_display,
        "coverage_threshold": coverage_threshold,
        "first_failure": None,
    }
    if first_failure is not None:
        payload["first_failure"] = {
            "identifier": first_failure.get("identifier"),
            "description": first_failure.get("description"),
            "command": first_failure.get("command"),
        }
    _write_discriminator_result(context, payload)
    emit_event(
        "discriminator",
        "run_completed",
        slug=slug,
        mode=mode,
        pass_number=pass_number,
        run_id=run_id,
        attempt=attempt,
        ok=overall_ok,
        coverage_failed=coverage_failed,
        first_failure_identifier=(
            first_failure.get("identifier") if first_failure else None
        ),
        first_failure_description=(
            first_failure.get("description") if first_failure else None
        ),
    )
    return overall_ok


def _configure_pytest_flags(mode: str, env: dict[str, str], context: RexContext) -> List[str]:
    flags = ["-q", "-ra"]
    if mode == "feature":
        flags += ["-x", "--maxfail=1"]
        return flags
    probe = run(
        ["python", "-c", "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('xdist') else 1)"],
        env=env,
        cwd=context.root,
        check=False,
        capture_output=True,
    )
    if probe.returncode == 0:
        flags += ["-n", "auto", "--dist", "loadscope"]
    return flags


def _build_stage_groups(
    mode: str,
    slug: Optional[str],
    pytest_flags: Sequence[str],
    env: dict[str, str],
    context: RexContext,
) -> List[StageGroup]:
    pytest_flags_str = " ".join(shlex.quote(flag) for flag in pytest_flags)
    specs_dir = f"tests/feature_specs/{slug}" if slug else ""
    coverage_min = env.get("COVERAGE_MIN")
    coverage_targets = env.get("COVERAGE_TARGETS", ".")

    def _format_targets(raw: str) -> str:
        tokens = [token for token in re.split(r"[,\s]+", raw.strip()) if token]
        return " ".join(shlex.quote(token) for token in tokens) or "."

    if env.get("MYPY_TARGETS"):
        mypy_raw = env["MYPY_TARGETS"]
    elif env.get("COVERAGE_TARGETS"):
        mypy_raw = env["COVERAGE_TARGETS"]
    elif (context.root / "src").exists():
        mypy_raw = "src"
    else:
        mypy_raw = "."
    mypy_targets = _format_targets(mypy_raw)
    groups: List[StageGroup] = []

    level00 = StageGroup(
        title="Level 00 - Repo & System Health",
        stages=[
            Stage("00.1", "Git status", "git status -sb"),
            Stage("00.2", "Python version", "python3 --version"),
        ],
    )
    if (context.root / ".venv" / "bin" / "python").exists():
        level00.stages.append(Stage("00.3", "Venv Python", ".venv/bin/python --version"))
    groups.append(level00)

    groups.append(
        StageGroup(
            title="Level 01 - Tooling & Dependencies",
            stages=[
                Stage("01.1", "pytest importable?", "python -c 'import pytest; print(pytest.__version__)'"),
            ],
        )
    )

    if mode == "feature":
        groups.append(
            StageGroup(
                title=f"Level 02 - Feature Spec Smoke ({slug})",
                stages=[
                    Stage(
                        "02.1",
                        "Run feature specs",
                        f"pytest {pytest_flags_str} {shlex.quote(specs_dir)} --junitxml .codex_ci/discriminator_feature_{slug}.xml",
                    )
                ],
            )
        )
        groups.append(
            StageGroup(
                title=f"Level 03 - Feature Unit Grid ({slug})",
                stages=[
                    Stage(
                        "03.1",
                        "Run feature specs (no DB markers)",
                        f"pytest {pytest_flags_str} {shlex.quote(specs_dir)} -m 'not django_db'",
                    )
                ],
            )
        )
        if coverage_min:
            groups.append(
                StageGroup(
                    title=f"Level 04 - Feature Coverage ({slug})",
                    stages=[
                        Stage(
                            "04.1",
                            "Coverage threshold",
                            f"pytest {pytest_flags_str} {shlex.quote(specs_dir)} --cov={coverage_targets} --cov-report=term --cov-fail-under={coverage_min}",
                        )
                    ],
                )
            )
    else:
        groups.append(
            StageGroup(
                title="Level 02 - Inline Spec Smoke",
                stages=[
                    Stage(
                        "02.1",
                        "Do doctests/specs pass?",
                        f"pytest {pytest_flags_str} -k 'spec or doctest' --junitxml .codex_ci/discriminator_global_smoke.xml",
                    )
                ],
            )
        )
        groups.append(
            StageGroup(
                title="Level 03 - Unit Test Grid",
                stages=[
                    Stage(
                        "03.1",
                        "Run unit tests (no DB markers)",
                        f"pytest {pytest_flags_str} -m 'not django_db' --junitxml .codex_ci/discriminator_global_unit.xml",
                    )
                ],
            )
        )
        if coverage_min:
            groups.append(
                StageGroup(
                    title="Level 04 - Coverage",
                    stages=[
                        Stage(
                            "04.1",
                            "Coverage threshold",
                            f"pytest {pytest_flags_str} --cov={coverage_targets} --cov-report=term --cov-fail-under={coverage_min}",
                        )
                    ],
                )
            )

    level05_stages: List[Stage] = []
    if env.get("PIP_AUDIT") == "1":
        level05_stages.append(
            Stage(
                "05.1",
                "pip-audit (dependencies)",
                "python -m pip install -q pip-audit >/dev/null 2>&1 && pip-audit",
            )
        )
    if env.get("BANDIT") == "1":
        bandit_targets = env.get("BANDIT_TARGETS") or env.get("COVERAGE_TARGETS") or "src"
        if not (context.root / bandit_targets).exists():
            bandit_targets = "."
        level05_stages.append(
            Stage(
                "05.2",
                "bandit (static security)",
                f"python -m pip install -q bandit >/dev/null 2>&1 && bandit -q -r {bandit_targets}",
            )
        )
    if env.get("PACKAGE_CHECK") == "1":
        level05_stages.extend(
            [
                Stage(
                    "05.3",
                    "Build distribution artifacts",
                    "python -m pip install -q build twine >/dev/null 2>&1 && python -m build",
                ),
                Stage(
                    "05.4",
                    "twine check dist/*",
                    "python -m pip install -q build twine >/dev/null 2>&1 && twine check dist/*",
                ),
            ]
        )
    if level05_stages:
        groups.append(StageGroup(title="Level 05 - Security & Build", stages=level05_stages))

    if mode == "feature":
        target = shlex.quote(specs_dir)
        feature_style_stages = [
            Stage("06.1", "black --check (feature)", f"black {target} --check"),
            Stage("06.2", "isort --check-only (feature)", f"isort {target} --check-only"),
            Stage("06.3", "ruff check (feature)", f"ruff check {target}"),
            Stage("06.4", "flake8 (feature)", f"flake8 {target}"),
        ]
        if env.get("MYPY_INCLUDE_TESTS") == "1":
            feature_style_stages.append(Stage("06.5", "mypy (feature)", f"mypy {target}"))
        groups.append(
            StageGroup(
                title=f"Level 06 - Feature Style & Type Gates ({slug})",
                stages=feature_style_stages,
            )
        )
    else:
        groups.append(
            StageGroup(
                title="Level 06 - Style & Type Gates",
                stages=[
                    Stage("06.1", "black --check", "black . --check"),
                    Stage("06.2", "isort --check-only", "isort . --check-only"),
                    Stage("06.3", "ruff check", "ruff check ."),
                    Stage("06.4", "flake8", "flake8 ."),
                    Stage("06.5", "mypy", f"mypy {mypy_targets}"),
                ],
            )
        )
    return groups


def _execute_stage(
    stage: Stage,
    env: dict[str, str],
    context: RexContext,
    log_path: Path,
    latest_log_path: Path,
    print_lock: Optional[threading.Lock] = None,
) -> Tuple[bool, float, str]:
    with (print_lock or nullcontext()):
        print(f"\n  Question {stage.identifier}: {stage.description}")
        print(f"    Command: {stage.command}")
    stage_timeout = int(os.environ.get("DISCRIMINATOR_STAGE_TIMEOUT", "0") or "0")
    timeout_seconds = stage_timeout if stage_timeout > 0 else None
    cmd = ["bash", "-lc", stage.command]
    start = time.perf_counter()
    try:
        completed = subprocess.run(
            cmd,
            cwd=context.root,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        output = (completed.stdout or "") + (completed.stderr or "")
    except subprocess.TimeoutExpired:
        message = f"[discriminator] Stage {stage.identifier} timed out after {stage_timeout}s"
        output = message + "\n"
        completed = subprocess.CompletedProcess(cmd, returncode=124, stdout="", stderr=message)
    elapsed = time.perf_counter() - start
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(f"\n[{stage.identifier}] {stage.description}\n")
        fh.write(output)
    with open(latest_log_path, "a", encoding="utf-8") as fh:
        fh.write(output)
    with (print_lock or nullcontext()):
        print(output, end="")
        if completed.returncode == 124:
            print(f"[discriminator] Stage {stage.identifier} timed out after {stage_timeout}s")
    ok = completed.returncode == 0
    tail_lines = "\n".join((output or "").splitlines()[-8:])
    return ok, elapsed, tail_lines


def _run_parallel_stage_group(
    stages: Sequence[Stage],
    env: dict[str, str],
    context: RexContext,
    log_path: Path,
    latest_log_path: Path,
    print_lock: threading.Lock,
) -> Iterable[Tuple[Stage, bool, float, str]]:
    if not stages:
        return
    max_workers = min(5, len(stages))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(
                _execute_stage,
                stage,
                env,
                context,
                log_path,
                latest_log_path,
                print_lock,
            ): stage
            for stage in stages
        }
        for future in as_completed(future_map):
            stage = future_map[future]
            ok, elapsed, tail = future.result()
            yield stage, ok, elapsed, tail


_COVERAGE_TOTAL_RE = re.compile(r"TOTAL\s+\d+\s+\d+\s+\d+\s+(\d+)%")


def _parse_coverage_percent(text: str) -> Optional[float]:
    for line in reversed((text or "").splitlines()):
        match = _COVERAGE_TOTAL_RE.search(line)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                continue
    return None


def _split_targets_for_events(raw: str) -> List[str]:
    tokens = [token.strip() for token in re.split(r"[,\s]+", raw or "") if token.strip()]
    return tokens or []


def _render_stage_summary(
    summary: List[dict[str, object]],
    overall_ok: bool,
    first_failure: Optional[dict[str, object]],
    palette: SimpleNamespace,
    context: RexContext,
    mode: str,
) -> None:
    if not summary:
        return
    print("\n--- Summary -----------------------------------------------------")
    grouped: "OrderedDict[str, List[dict[str, object]]]" = OrderedDict()
    for record in summary:
        key = record["group"]  # type: ignore[index]
        grouped.setdefault(key, []).append(record)
    for group, rows in grouped.items():
        print(f"{palette.bold}{group}{palette.reset}")
        for record in rows:
            ok = bool(record["ok"])
            icon = f"{palette.green}✔{palette.reset}" if ok else f"{palette.red}✖{palette.reset}"
            identifier = record["identifier"]
            description = record["description"]
            elapsed = float(record["elapsed"])
            timing = f"{palette.dim}({elapsed:.2f}s){palette.reset}"
            print(f"  {icon} {palette.dim}[{identifier}]{palette.reset} {description} {timing}")
            if not ok:
                reason = _summarize_failure_reason(record.get("tail", ""))
                if reason:
                    print(f"      ↳ {palette.error}{reason}{palette.reset}")
    if not overall_ok and first_failure is not None:
        command = first_failure["command"]
        print(f"\n{palette.yellow}Next step:{palette.reset} rerun the first failing command locally:")
        print(f"  {command}")
        print(f"Inspect {palette.cyan}./rex-codex logs --discriminator --lines 200{palette.reset} for full output.")
    if mode == "global":
        orphans = find_orphan_spec_slugs(context)
        if orphans:
            paths = ", ".join(f"tests/feature_specs/{slug}" for slug in sorted(orphans))
            print(
                f"{palette.yellow}[discriminator] Orphan spec shards detected:{palette.reset} {paths}\n"
                f"  Run `./rex-codex card prune-specs` to tidy up."
            )


def _summarize_failure_reason(tail: object) -> str:
    text = str(tail or "")
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.lower().startswith("bringing up nodes"):
            continue
        if stripped.startswith("SKIPPED "):
            continue
        return stripped[:160]
    return ""


def shutil_which(name: str) -> Optional[str]:
    from shutil import which

    return which(name)


def _ensure_node_present() -> bool:
    return shutil_which("node") is not None


def _collect_test_count(
    mode: str,
    slug: Optional[str],
    context: RexContext,
    env: dict[str, str],
) -> Optional[int]:
    cmd = ["pytest", "--collect-only"]
    if mode == "feature" and slug:
        specs_dir = context.root / "tests" / "feature_specs" / slug
        if specs_dir.is_dir():
            cmd.append(str(specs_dir))
    completed = run(cmd, cwd=context.root, env=env, capture_output=True, check=False)
    text = (completed.stdout or "") + (completed.stderr or "")
    match = re.search(r"collected (\d+) items?", text, re.IGNORECASE)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _protected_patterns() -> List[str]:
    raw = os.environ.get("DISCRIMINATOR_PROTECTED_PATHS")
    if raw:
        return [token for token in raw.split() if token.strip()]
    return list(DEFAULT_PROTECTED_PATHS)


def _snapshot_protected_paths(context: RexContext) -> dict[str, str]:
    patterns = _protected_patterns()
    root = context.root
    files: set[Path] = set()

    def record_path(path: Path) -> None:
        if not path.exists():
            return
        if path.is_dir():
            for child in path.rglob("*"):
                if child.is_file():
                    files.add(child)
        elif path.is_file():
            files.add(path)

    for pattern in patterns:
        if not pattern:
            continue
        full_pattern = root / pattern
        matches = glob.glob(str(full_pattern), recursive=True)
        if not matches and not any(ch in pattern for ch in "*?[]"):
            candidate = root / pattern
            if candidate.exists():
                matches = [str(candidate)]
        for match in matches:
            record_path(Path(match))

    snapshot: dict[str, str] = {}
    for file_path in sorted(files):
        try:
            digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
        except OSError:
            continue
        snapshot[str(file_path.relative_to(root))] = digest
    return snapshot


def _detect_protected_changes(baseline: dict[str, str], context: RexContext) -> List[str]:
    current = _snapshot_protected_paths(context)
    changed: set[str] = set()
    for path, digest in baseline.items():
        if path not in current:
            changed.add(path)
        elif current[path] != digest:
            changed.add(path)
    for path in current:
        if path not in baseline:
            changed.add(path)
    return sorted(changed)


def _revert_paths(paths: Iterable[str], context: RexContext) -> None:
    for path in paths:
        target = context.root / path
        result = run(
            ["git", "ls-files", "--error-unmatch", path],
            cwd=context.root,
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            run(["git", "restore", "--staged", "--", path], cwd=context.root, check=False)
            run(["git", "restore", "--worktree", "--", path], cwd=context.root, check=False)
        elif target.exists():
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()


def _revert_all_changes(context: RexContext) -> None:
    result = run(
        ["git", "restore", "--staged", "--worktree", "--source=HEAD", ":/"],
        cwd=context.root,
        check=False,
    )
    if result.returncode != 0:
        run(["git", "reset", "--hard", "-q"], cwd=context.root, check=False)


def _reject_non_runtime_changes(context: RexContext) -> bool:
    runtime_targets = _detect_runtime_targets(context)
    if not runtime_targets:
        return True
    changed = run(
        ["bash", "-lc", "git diff --name-only; git ls-files --others --exclude-standard"],
        cwd=context.root,
        capture_output=True,
        check=False,
    ).stdout.splitlines()
    rejects: List[str] = []
    for path in sorted(set(changed)):
        if not path or path.startswith(".codex_ci/"):
            continue
        allowed = any(path == target or path.startswith(f"{target}/") for target in runtime_targets)
        if not allowed:
            rejects.append(path)
    if rejects:
        print(f"[discriminator] LLM edits outside runtime allowlist: {' '.join(rejects)}")
        _revert_paths(rejects, context)
        return False
    return True


def _git_diff_is_empty(context: RexContext) -> bool:
    result = run(["git", "diff", "--quiet"], cwd=context.root, check=False)
    return result.returncode == 0


def _enforce_patch_size(context: RexContext) -> bool:
    max_files = int(os.environ.get("DISCRIMINATOR_MAX_FILES", DEFAULT_DISCRIMINATOR_MAX_FILES))
    max_lines = int(os.environ.get("DISCRIMINATOR_MAX_LINES", DEFAULT_DISCRIMINATOR_MAX_LINES))
    completed = run(["git", "diff", "--numstat"], cwd=context.root, capture_output=True, check=False)
    files = 0
    lines = 0
    for line in completed.stdout.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        added, deleted = parts[0], parts[1]
        if added == "-" or deleted == "-":
            files += 1
            lines += max_lines + 1
            continue
        try:
            files += 1
            lines += int(added) + int(deleted)
        except ValueError:
            continue
    if files > max_files or lines > max_lines:
        print(
            f"[discriminator] LLM patch touched {files} files / {lines} lines "
            f"(limits {max_files}/{max_lines})."
        )
        _revert_all_changes(context)
        return False
    return True


def _apply_mechanical_fixes(
    mode: str,
    slug: Optional[str],
    context: RexContext,
    env: dict[str, str],
    *,
    pass_number: int,
    attempt: int,
    run_id: int,
) -> bool:
    print("Mechanical fixes (ruff/black/isort)…")
    tools = ["ruff", "black", "isort"]
    targets: List[str] = []
    reason: Optional[str] = None
    if mode == "feature":
        if not slug:
            reason = "missing_slug"
            emit_event(
                "discriminator",
                "mechanical_fixes",
                slug=slug,
                mode=mode,
                pass_number=pass_number,
                run_id=run_id,
                attempt=attempt,
                changed=False,
                tools=tools,
                targets=targets,
                reason=reason,
            )
            return False
        target = context.root / "tests" / "feature_specs" / slug
        if not target.is_dir():
            print("[discriminator] No feature specs directory; skipping mechanical fixes.")
            reason = "missing_feature_specs"
            emit_event(
                "discriminator",
                "mechanical_fixes",
                slug=slug,
                mode=mode,
                pass_number=pass_number,
                run_id=run_id,
                attempt=attempt,
                changed=False,
                tools=tools,
                targets=targets,
                reason=reason,
            )
            return False
        targets = [str(target)]
    else:
        targets = _detect_runtime_targets(context)
        if not targets:
            print("[discriminator] No runtime targets detected for mechanical style; skipping.")
            reason = "no_runtime_targets"
            emit_event(
                "discriminator",
                "mechanical_fixes",
                slug=slug,
                mode=mode,
                pass_number=pass_number,
                run_id=run_id,
                attempt=attempt,
                changed=False,
                tools=tools,
                targets=targets,
                reason=reason,
            )
            return False
    run(["ruff", "check", *targets, "--fix"], cwd=context.root, env=env, check=False)
    run(["black", *targets], cwd=context.root, env=env, check=False)
    run(["isort", *targets], cwd=context.root, env=env, check=False)
    changed = not _git_diff_is_empty(context)
    emit_event(
        "discriminator",
        "mechanical_fixes",
        slug=slug,
        mode=mode,
        pass_number=pass_number,
        run_id=run_id,
        attempt=attempt,
        changed=changed,
        tools=tools,
        targets=targets,
        reason=None if changed else (reason or "no_changes"),
    )
    if not changed:
        return False
    run(["git", "add", "-A"], cwd=context.root, check=False)
    label = "feature" if mode == "feature" else "global"
    run(["git", "commit", "-m", f"style(rex-codex): apply ruff/black/isort ({label})"], cwd=context.root, check=False)
    return True


def _detect_runtime_targets(context: RexContext) -> List[str]:
    overrides = os.environ.get("DISCRIMINATOR_RUNTIME_ALLOWLIST")
    if overrides:
        runtime = sorted({token.strip() for token in overrides.split() if token.strip()})
        return runtime
    root = context.root
    targets: set[str] = set()
    for default in DEFAULT_RUNTIME_ALLOWLIST:
        candidate = root / default
        if candidate.exists():
            targets.add(default)
    for pkg_init in root.glob("*/__init__.py"):
        pkg_dir = pkg_init.parent
        name = pkg_dir.name
        if name in {"tests", "test", "documents", "docs", ".git", ".github"}:
            continue
        targets.add(name)
    return sorted(targets)


def _tail_text(path: Path, lines: int = 120) -> str:
    if not path.exists():
        return ""
    content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(content[-lines:])


def _invoke_llm_once(
    options: DiscriminatorOptions,
    mode: str,
    slug: Optional[str],
    context: RexContext,
    env: dict[str, str],
    log_path: Path,
    latest_log_path: Path,
) -> None:
    runtime_allowlist = _detect_runtime_targets(context)
    agents_path = context.root / "AGENTS.md"
    agents_excerpt = ""
    if agents_path.exists():
        agents_excerpt = "\n".join(agents_path.read_text(encoding="utf-8", errors="replace").splitlines()[:300])
    log_excerpt = _tail_text(log_path) or _tail_text(latest_log_path)

    prompt_lines = [
        "You are a coding agent for this repository.",
        "Follow AGENTS.md guardrails (runtime vs tests, doc/spec/type, offline by default).",
        "Make ONE minimal change that most reduces non-compliance or failures.",
        "Do not weaken tests or remove functionality.",
        "After edits, run relevant commands locally to validate.",
        "",
        f"Current discriminator mode: {mode}",
    ]
    if slug:
        prompt_lines.append(f"Active feature slug: {slug}")
    prompt_lines.extend([
        "",
        "Runtime directories permitted for edits:",
    ])
    if runtime_allowlist:
        prompt_lines.extend([f" - {target}" for target in runtime_allowlist])
    else:
        prompt_lines.append(" - (none discovered; edits outside protected files likely to be rejected)")
    prompt_lines.extend([
        "",
        "--- BEGIN AGENTS.md EXCERPT ---",
        agents_excerpt,
        "--- END AGENTS.md EXCERPT ---",
    ])
    if log_excerpt:
        prompt_lines.extend([
            "",
            "Latest log excerpt:",
            "```",
            log_excerpt,
            "```",
        ])
    prompt_text = "\n".join(prompt_lines)
    prompt_file = context.codex_ci_dir / "discriminator_prompt.txt"
    prompt_file.write_text(prompt_text + "\n", encoding="utf-8")

    cmd = (
        _split_command(options.codex_bin)
        + ["exec"]
        + _split_command(options.codex_flags)
    )
    if options.codex_model:
        cmd += ["--model", options.codex_model]
    cmd += ["--cd", str(context.root), "--", prompt_text]

    print(f"[*] Invoking Codex with: {' '.join(cmd)}")
    completed = subprocess.run(
        cmd,
        cwd=context.root,
        env=env,
        capture_output=True,
        text=True,
    )
    log_file = context.codex_ci_dir / "discriminator_llm_response.log"
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with open(log_file, "a", encoding="utf-8") as fh:
        fh.write(f"=== {timestamp} ===\n")
        fh.write((completed.stdout or "") + (completed.stderr or ""))
        fh.write("\n")


def _record_success(
    mode: str,
    slug: Optional[str],
    context: RexContext,
    env: dict[str, str],
) -> None:
    data = load_json(context.rex_agent_file)
    disc = data.setdefault("discriminator", {})
    disc["last_mode"] = mode
    disc["last_slug"] = slug
    disc["last_green_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    test_count = _collect_test_count(mode, slug, context, env)
    if test_count is not None:
        disc["last_test_count"] = test_count
    dump_json(context.rex_agent_file, data)
