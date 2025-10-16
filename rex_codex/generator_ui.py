"""Terminal HUD for generator progress."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, List, Optional

from .events import events_path

STATUS_ICONS = {
    "planned": "[ ]",
    "missing": "[ ]",
    "covered": "[*]",
    "verified": "[*]",
    "failing": "[x]",
}


def _shorten(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    if width <= 1:
        return text[:width]
    return text[: width - 1] + "…"


def _format_duration(seconds: Optional[float]) -> str:
    if not seconds or seconds <= 0:
        return "0s"
    if seconds < 60:
        return f"{int(seconds)}s"
    minutes, rem = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m{rem:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


@dataclass
class AcceptanceItem:
    index: int
    text: str
    tests: List[str] = field(default_factory=list)
    status: str = "planned"


class GeneratorHUDModel:
    """Maintain generator progress state derived from events."""

    def __init__(self, slug: str) -> None:
        self.slug = slug
        self.feature_title = slug
        self.feature_status = ""
        self.feature_summary = ""
        self.acceptance: List[AcceptanceItem] = []
        self.iteration_current = 0
        self.iteration_total = 0
        self.iteration_status = "idle"
        self.iteration_history: List[float] = []
        self.codex_status = "idle"
        self.codex_elapsed_hint = 0.0
        self.codex_returncode: Optional[int] = None
        self.diff_files: List[Dict[str, Any]] = []
        self.diff_totals: Dict[str, int] = {}
        self.pytest_status = "pending"
        self.pytest_output = ""
        self.critic_status = "pending"
        self.critic_guidance = ""
        self.feature_outcome = "running"
        self.orphan_tests: List[str] = []
        self.messages: Deque[str] = deque(maxlen=8)
        self.coverage_percent: float = 0.0
        self.coverage_linked = 0
        self.coverage_total = 0
        self.coverage_failing = 0

    def _set_acceptance(self, items: Iterable[str]) -> None:
        self.acceptance = [
            AcceptanceItem(index=idx, text=item.strip())
            for idx, item in enumerate(items, start=1)
            if item.strip()
        ]
        self._recompute_coverage_metrics()

    def _update_acceptance_tests(self, coverage: Dict[str, Any]) -> None:
        entries = coverage.get("entries") or []
        indexed_tests = {
            entry.get("index"): entry.get("tests", []) for entry in entries
        }
        for item in self.acceptance:
            tests = indexed_tests.get(item.index, [])
            item.tests = tests
            item.status = "covered" if tests else "missing"
        missing = coverage.get("missing") or []
        for entry in missing:
            idx = entry.get("index")
            for item in self.acceptance:
                if item.index == idx:
                    item.status = "missing"
        self.orphan_tests = coverage.get("orphans") or []
        self._recompute_coverage_metrics()

    def _recompute_coverage_metrics(self) -> None:
        total = len(self.acceptance)
        if total == 0:
            self.coverage_percent = 0.0
            self.coverage_linked = 0
            self.coverage_total = 0
            self.coverage_failing = 0
            return
        contributions: List[float] = []
        linked = 0
        failing = 0
        for item in self.acceptance:
            if item.tests:
                linked += 1
                if self.pytest_status == "passed":
                    item.status = "verified"
                    contribution = 1.0
                elif self.pytest_status in {"failed", "timeout"}:
                    item.status = "failing"
                    contribution = 0.5
                    failing += 1
                else:
                    item.status = "covered"
                    contribution = 0.5
            else:
                item.status = "missing"
                contribution = 0.0
            contributions.append(contribution)
        total_score = sum(contributions)
        percent = (total_score / total) * 100 if total else 0.0
        percent = max(0.0, min(100.0, percent))
        self.coverage_percent = round(percent, 1)
        self.coverage_linked = linked
        self.coverage_total = total
        self.coverage_failing = failing

    def _add_message(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        if self.messages and self.messages[-1] == text:
            return
        self.messages.append(text)

    def apply_event(self, event: Dict[str, Any]) -> None:
        data = event.get("data", {})
        etype = event.get("type", "")
        if etype == "feature_started":
            self.feature_title = data.get("title") or self.slug
            self.feature_status = data.get("status") or ""
            self.feature_summary = data.get("summary") or ""
            self._set_acceptance(data.get("acceptance") or [])
            self.iteration_total = int(data.get("passes") or 0)
            self.feature_outcome = "running"
            focus = data.get("focus")
            if focus:
                self._add_message(f"Focus: {_shorten(str(focus), 80)}")
        elif etype == "iteration_started":
            self.iteration_current = int(data.get("iteration") or 0)
            self.iteration_total = int(data.get("total_passes") or self.iteration_total)
            self.iteration_status = "running"
            self._add_message(
                f"Iteration {self.iteration_current}/{self.iteration_total} started"
            )
        elif etype == "iteration_completed":
            self.iteration_status = "waiting"
            elapsed = data.get("elapsed_seconds")
            if isinstance(elapsed, (float, int)):
                self.iteration_history.append(float(elapsed))
            exit_code = data.get("exit_code")
            if exit_code not in (0, None):
                self._add_message(f"Iteration ended with exit code {exit_code}")
        elif etype == "codex_started":
            self.codex_status = "running"
            self.codex_returncode = None
        elif etype == "codex_heartbeat":
            seconds = data.get("seconds")
            if isinstance(seconds, (int, float)):
                self.codex_elapsed_hint = max(self.codex_elapsed_hint, float(seconds))
        elif etype == "codex_completed":
            self.codex_status = "completed"
            self.codex_returncode = data.get("returncode")
            elapsed = data.get("elapsed_seconds")
            if isinstance(elapsed, (int, float)):
                self.codex_elapsed_hint = float(elapsed)
            rc = self.codex_returncode
            label = "success" if rc == 0 else f"exit {rc}"
            self._add_message(f"Codex run finished ({label})")
        elif etype == "diff_summary":
            self.diff_files = data.get("files") or []
            self.diff_totals = data.get("totals") or {}
        elif etype == "pytest_snapshot":
            status = data.get("status") or "pending"
            self.pytest_status = status
            output = data.get("output")
            if isinstance(output, str):
                self.pytest_output = output.strip()
            if status == "failed":
                self._add_message("Pytest snapshot failed")
            elif status == "timeout":
                self._add_message("Pytest snapshot timed out")
            elif status == "passed":
                self._add_message("Pytest snapshot passed")
            self._recompute_coverage_metrics()
        elif etype == "critic_guidance":
            done = bool(data.get("done"))
            self.critic_status = "done" if done else "todo"
            guidance = data.get("guidance") or ""
            self.critic_guidance = guidance.strip()
            if guidance:
                label = "DONE" if done else "Critic guidance"
                self._add_message(f"{label}: {_shorten(guidance, 80)}")
        elif etype == "spec_trace_update":
            coverage = data.get("coverage") or {}
            if isinstance(coverage, dict):
                self._update_acceptance_tests(coverage)
        elif etype == "feature_completed":
            self.feature_outcome = "completed"
            self.iteration_status = "completed"
            self._add_message("Feature completed")
            self._recompute_coverage_metrics()
        elif etype == "feature_failed":
            self.feature_outcome = "failed"
            reason = data.get("reason")
            if reason:
                self._add_message(f"Feature failed: {reason}")
            else:
                self._add_message("Feature failed.")
            self._recompute_coverage_metrics()

    def _acceptance_rows(self) -> List[str]:
        if not self.acceptance:
            return ["  (no acceptance criteria listed)"]
        rows: List[str] = []
        for item in self.acceptance:
            icon = STATUS_ICONS.get(item.status, "[ ]")
            tests = ", ".join(_shorten(t, 40) for t in item.tests) or "(missing)"
            rows.append(f"  {icon} {_shorten(item.text, 40):<40} │ {tests}")
        if self.orphan_tests:
            rows.append("  --- Orphan tests ---")
            for test in self.orphan_tests[:5]:
                rows.append(f"    • {_shorten(test, 64)}")
        return rows

    def _coverage_line(self) -> str:
        if not self.acceptance:
            return "Coverage: (no acceptance criteria listed)"
        percent_display = int(round(self.coverage_percent))
        percent_display = max(0, min(100, percent_display))
        total_blocks = 10
        filled_blocks = max(0, min(total_blocks, int(round(percent_display / 10))))
        bar = "█" * filled_blocks + "░" * (total_blocks - filled_blocks)
        summary_parts: List[str] = []
        if self.coverage_total:
            summary_parts.append(
                f"{self.coverage_linked}/{self.coverage_total} bullets linked"
            )
        if self.coverage_failing:
            summary_parts.append(f"{self.coverage_failing} failing")
        missing = self.coverage_total - self.coverage_linked
        if missing and self.coverage_total:
            summary_parts.append(f"{missing} missing")
        if (
            self.coverage_total
            and not self.coverage_failing
            and missing == 0
            and self.pytest_status == "passed"
        ):
            summary_parts.append("all passing")
        if not summary_parts:
            summary_parts.append("no coverage data")
        summary = "; ".join(summary_parts)
        return f"Coverage: {bar} {percent_display}% ({summary})"

    def _diff_summary(self) -> str:
        totals = self.diff_totals or {}
        files = totals.get("files", 0)
        added = totals.get("added_lines", 0)
        removed = totals.get("removed_lines", 0)
        parts = []
        if files:
            parts.append(f"{files} file{'s' if files != 1 else ''}")
        if added or removed:
            parts.append(f"+{added}/-{removed} lines")
        return ", ".join(parts) if parts else "pending"

    def _iteration_summary(self, elapsed: Optional[float]) -> str:
        if not self.iteration_total:
            return "Idle"
        current = self.iteration_current or 1
        status = self.iteration_status
        avg = None
        if self.iteration_history:
            avg = sum(self.iteration_history) / len(self.iteration_history)
        parts = [f"{current}/{self.iteration_total} ({status})"]
        if elapsed:
            parts.append(f"elapsed {_format_duration(elapsed)}")
        if avg:
            parts.append(f"avg {_format_duration(avg)}")
        return ", ".join(parts)

    def _codex_summary(self, elapsed: Optional[float]) -> str:
        status = self.codex_status
        if status == "idle":
            return "Idle"
        parts = [status]
        if elapsed or self.codex_elapsed_hint:
            duration = elapsed if elapsed is not None else self.codex_elapsed_hint
            parts.append(_format_duration(duration))
        if status == "completed" and self.codex_returncode not in (0, None):
            parts.append(f"exit {self.codex_returncode}")
        return ", ".join(parts)

    def _pytest_summary(self) -> str:
        status = self.pytest_status
        if status == "pending":
            return "Not run"
        if status == "passed":
            return "Passed"
        if status == "failed":
            return "Failed"
        if status == "timeout":
            return "Timeout"
        if status == "skipped":
            return "Skipped"
        return status

    def _critic_summary(self) -> str:
        if self.critic_status == "done":
            return "DONE"
        if self.critic_guidance:
            return _shorten(self.critic_guidance, 80)
        return "Waiting"

    def render(
        self, iteration_elapsed: Optional[float], codex_elapsed: Optional[float]
    ) -> str:
        lines: List[str] = []
        state = self.feature_outcome.upper()
        header = f"Feature: {self.feature_title}  [status: {self.feature_status or 'unknown'}]  → {state}"
        lines.append(header)
        if self.feature_summary:
            lines.append(f"Summary: {_shorten(self.feature_summary, 100)}")
        lines.append("")
        lines.append("Acceptance → Tests")
        lines.extend(self._acceptance_rows())
        lines.append(self._coverage_line())
        lines.append("")
        lines.append("Stages")
        lines.append(f"  Iteration     : {self._iteration_summary(iteration_elapsed)}")
        lines.append(f"  Codex         : {self._codex_summary(codex_elapsed)}")
        lines.append(f"  Diff summary  : {self._diff_summary()}")
        lines.append(f"  Pytest shard  : {self._pytest_summary()}")
        lines.append(f"  Critic        : {self._critic_summary()}")
        if self.pytest_status == "failed" and self.pytest_output:
            lines.append("")
            lines.append("Pytest output (tail)")
            tail = self.pytest_output.splitlines()[-6:]
            lines.extend(f"  {line}" for line in tail)
        if self.messages:
            lines.append("")
            lines.append("Recent notes")
            for message in list(self.messages)[-6:]:
                lines.append(f"  - {_shorten(message, 100)}")
        return "\n".join(lines)


class _HUDCapture(io.TextIOBase):
    """Redirect stdout/stderr into a log file to avoid scrolling output."""

    def __init__(self, handle: io.TextIOBase) -> None:
        self._handle = handle

    def write(self, s: str) -> int:  # type: ignore[override]
        try:
            self._handle.write(s)
        except ValueError:
            return 0
        return len(s)

    def flush(self) -> None:  # type: ignore[override]
        if getattr(self._handle, "closed", False):
            return
        try:
            self._handle.flush()
        except ValueError:
            return

    def isatty(self) -> bool:  # type: ignore[override]
        return False


class GeneratorHUD(contextlib.AbstractContextManager["GeneratorHUD"]):
    """Manage the generator HUD lifecycle and stdout redirection."""

    def __init__(
        self,
        *,
        slug: str,
        codex_ci_dir: Path,
        ui_mode: str = "auto",
        refresh_hz: float = 1.0,
        terminal: io.TextIOBase | None = None,
    ) -> None:
        self.slug = slug
        self.codex_ci_dir = codex_ci_dir
        self.ui_mode = (ui_mode or "monitor").lower()
        self.refresh_interval = max(0.2, 1.0 / max(refresh_hz, 0.1))
        self.terminal = terminal or getattr(sys, "__stdout__", None)  # type: ignore[name-defined]
        if self.terminal is None:
            import sys as _sys

            self.terminal = _sys.__stdout__
        self.enabled = self._should_enable()
        self.log_path = codex_ci_dir / f"generator_console_{slug}.log"
        self._stack: Optional[contextlib.ExitStack] = None
        self._capture: Optional[_HUDCapture] = None
        self._log_handle: Optional[io.TextIOBase] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._events_path = events_path()
        self._offset = 0
        self._model = GeneratorHUDModel(slug)
        self._last_render = ""
        self._cursor_hidden = False
        self._alt_screen = False
        self._use_alternate = self.ui_mode == "monitor"
        self._iteration_start: Optional[float] = None
        self._codex_start: Optional[float] = None

    def _should_enable(self) -> bool:
        if self.ui_mode == "off":
            return False
        is_tty = getattr(self.terminal, "isatty", lambda: False)()
        if self.ui_mode in {"monitor", "auto"}:
            return bool(is_tty)
        return False

    # Terminal helpers -------------------------------------------------

    def _term_write(self, text: str) -> None:
        try:
            self.terminal.write(text)
            self.terminal.flush()
        except Exception:
            return

    def _activate_alternate(self) -> None:
        if not self.enabled or self._alt_screen or not self._use_alternate:
            return
        self._term_write("\033[?1049h\033[H")
        self._alt_screen = True

    def _release_alternate(self) -> None:
        if not self.enabled or not self._alt_screen or not self._use_alternate:
            return
        self._term_write("\033[?1049l")
        self._alt_screen = False

    def _hide_cursor(self) -> None:
        if not self.enabled or self._cursor_hidden:
            return
        self._term_write("\033[?25l")
        self._cursor_hidden = True

    def _show_cursor(self) -> None:
        if not self.enabled or not self._cursor_hidden:
            return
        self._term_write("\033[?25h")
        self._cursor_hidden = False

    def _clear_screen(self) -> None:
        if not self.enabled:
            return
        self._term_write("\033[2J\033[H")

    # Context manager --------------------------------------------------

    def __enter__(self) -> "GeneratorHUD":
        if not self.enabled:
            return self
        self._offset = 0
        if self._events_path.exists():
            self._offset = self._events_path.stat().st_size
        self.codex_ci_dir.mkdir(parents=True, exist_ok=True)
        self._log_handle = self.log_path.open("w", encoding="utf-8")
        self._capture = _HUDCapture(self._log_handle)
        self._stack = contextlib.ExitStack()
        self._stack.enter_context(contextlib.redirect_stdout(self._capture))
        self._stack.enter_context(contextlib.redirect_stderr(self._capture))
        self._activate_alternate()
        self._hide_cursor()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.enabled:
            self._stop()
            if self._stack:
                self._stack.close()
            if self._log_handle:
                self._log_handle.flush()
                self._log_handle.close()
            self._release_alternate()
            self._show_cursor()
        return None

    # Public helpers ---------------------------------------------------

    def print_footer(self, exit_code: int) -> None:
        if not self.enabled:
            return
        status = "PASS" if exit_code == 0 else f"EXIT {exit_code}"
        self._term_write(
            f"\n[generator] Finished {self.slug} ({status}). Console log: {self.log_path}\n"
        )

    # Internal event loop ----------------------------------------------

    def _loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                self._poll_events()
                self._render()
                self._stop_event.wait(self.refresh_interval)
            self._poll_events()
            self._render(final=True)
        except Exception:
            # Fallback: ensure cursor is visible even if rendering fails
            self._show_cursor()

    def _stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None

    def _poll_events(self) -> None:
        if not self._events_path.exists():
            return
        try:
            with self._events_path.open("r", encoding="utf-8") as fh:
                fh.seek(self._offset)
                for line in fh:
                    self._handle_line(line)
                self._offset = fh.tell()
        except OSError:
            return

    def _handle_line(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return
        slug = event.get("slug")
        if slug not in (self.slug, None):
            return
        self._model.apply_event(event)
        etype = event.get("type")
        now = time.monotonic()
        if etype == "iteration_started":
            self._iteration_start = now
        elif etype == "iteration_completed":
            self._iteration_start = None
        elif etype == "feature_failed":
            self._iteration_start = None
        elif etype == "feature_completed":
            self._iteration_start = None
        elif etype == "codex_started":
            self._codex_start = now
        elif etype == "codex_completed":
            self._codex_start = None
        elif etype == "codex_heartbeat":
            seconds = event.get("data", {}).get("seconds")
            if isinstance(seconds, (int, float)):
                self._model.codex_elapsed_hint = float(seconds)

    def _render(self, *, final: bool = False) -> None:
        if not self.enabled:
            return
        now = time.monotonic()
        iteration_elapsed = (
            now - self._iteration_start if self._iteration_start else None
        )
        codex_elapsed = now - self._codex_start if self._codex_start else None
        snapshot = self._model.render(iteration_elapsed, codex_elapsed)
        if snapshot == self._last_render and not final:
            return
        self._last_render = snapshot
        self._clear_screen()
        self._term_write(snapshot + "\n")
