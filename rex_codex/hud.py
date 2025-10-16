"""One-shot HUD renderer for use with `watch` or CI snapshots."""

from __future__ import annotations

import json
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from .cards import latest_card
from .events import events_path as default_events_path
from .generator_ui import GeneratorHUDModel
from .utils import RexContext


class _HUDPrinter:
    def __init__(self, *, width: int = 100) -> None:
        self.width = width

    def divider(self, title: str) -> str:
        title = title.strip()
        pad = max(0, self.width - len(title) - 4)
        return f"{title} {'-' * pad}"


def _load_events(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return []
    results = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            results.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return results


def _resolve_generator_slug(slug: Optional[str], *, context: RexContext) -> Optional[str]:
    if slug:
        return slug
    card = latest_card()
    return card.slug if card else None


def render_generator_snapshot(
    *,
    slug: str,
    events: Iterable[dict[str, Any]],
    printer: _HUDPrinter,
) -> str:
    model = GeneratorHUDModel(slug)
    relevant = [
        event for event in events if event.get("slug") in (slug, None)
    ]
    start_index = 0
    for idx, event in enumerate(reversed(relevant)):
        if event.get("type") == "feature_started" and event.get("slug") == slug:
            start_index = len(relevant) - idx - 1
            break
    for event in relevant[start_index:]:
        model.apply_event(event)
    snapshot = model.render(iteration_elapsed=None, codex_elapsed=None)
    header = printer.divider(f"Generator HUD :: {slug}")
    return f"{header}\n{snapshot}\n"


def generator_snapshot_text(slug: str, path: Path) -> str:
    events = _load_events(path)
    if not events:
        return ""
    printer = _HUDPrinter()
    return render_generator_snapshot(slug=slug, events=events, printer=printer)


def _format_elapsed(value: Any) -> Optional[str]:
    if isinstance(value, (int, float)):
        return f"{float(value):.2f}s"
    return None


class DiscriminatorHUDModel:
    def __init__(self) -> None:
        self.mode = "global"
        self.slug: Optional[str] = None
        self.pass_number: Optional[int] = None
        self.run_id: Optional[int] = None
        self.stage_groups: list[str] = []
        self.stages: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self.coverage_percent: Optional[float] = None
        self.coverage_threshold: Optional[str] = None
        self.coverage_targets: list[str] = []
        self.mechanical: Optional[dict[str, Any]] = None
        self.llm_decision: Optional[dict[str, Any]] = None
        self.result: Optional[bool] = None

    def _reset_for_run(self, pass_number: Optional[int], run_id: int) -> None:
        self.pass_number = pass_number
        self.run_id = run_id
        self.stage_groups = []
        self.stages = OrderedDict()
        self.coverage_percent = None
        self.coverage_threshold = None
        self.coverage_targets = []
        self.mechanical = None
        self.llm_decision = None
        self.result = None

    def apply_event(self, event: Dict[str, Any]) -> None:
        if event.get("phase") != "discriminator":
            return
        data: Dict[str, Any] = event.get("data", {}) or {}
        run_id = data.get("run_id")
        pass_number = data.get("pass_number")

        if run_id is not None:
            if self.run_id is None or run_id > self.run_id:
                self._reset_for_run(pass_number, run_id)
            elif run_id < self.run_id:
                return
        if pass_number is not None and self.pass_number is None:
            self.pass_number = pass_number

        etype = event.get("type")
        if etype == "run_started":
            self.mode = data.get("mode") or self.mode
            slug = event.get("slug")
            if slug is not None:
                self.slug = slug
            self.stage_groups = list(data.get("stage_groups") or [])
            return
        if etype == "stage_start":
            identifier = data.get("identifier")
            if not identifier:
                return
            stage = self.stages.setdefault(
                identifier,
                {
                    "description": data.get("description") or "",
                    "group": data.get("group") or "",
                    "status": "RUN",
                    "elapsed": None,
                    "failure_reason": "",
                },
            )
            stage["description"] = data.get("description") or stage["description"]
            stage["group"] = data.get("group") or stage["group"]
            stage["status"] = "RUN"
            return
        if etype == "stage_end":
            identifier = data.get("identifier")
            if not identifier:
                return
            stage = self.stages.setdefault(
                identifier,
                {
                    "description": data.get("description") or "",
                    "group": data.get("group") or "",
                    "status": "",
                    "elapsed": None,
                    "failure_reason": "",
                },
            )
            stage["description"] = data.get("description") or stage["description"]
            stage["group"] = data.get("group") or stage["group"]
            stage["status"] = "PASS" if data.get("ok") else "FAIL"
            elapsed = data.get("elapsed")
            stage["elapsed"] = float(elapsed) if isinstance(elapsed, (int, float)) else None
            stage["failure_reason"] = data.get("failure_reason") or ""
            return
        if etype == "coverage_update":
            percent = data.get("percent")
            if isinstance(percent, (int, float)):
                self.coverage_percent = float(percent)
            threshold = data.get("threshold")
            if threshold is not None:
                self.coverage_threshold = str(threshold)
            targets = data.get("targets")
            if isinstance(targets, list):
                self.coverage_targets = [str(item) for item in targets if str(item)]
            return
        if etype == "mechanical_fixes":
            self.mechanical = data
            return
        if etype == "llm_patch_decision":
            self.llm_decision = data
            return
        if etype == "run_completed":
            self.result = bool(data.get("ok"))
            self.mode = data.get("mode") or self.mode
            slug = event.get("slug")
            if slug is not None:
                self.slug = slug

    def render(self) -> str:
        slug_display = self.slug or "global"
        pass_label = f"pass {self.pass_number}" if self.pass_number is not None else "pass ?"
        run_label = f"run {self.run_id}" if self.run_id is not None else "run ?"
        lines = [f"Mode: {self.mode} | Slug: {slug_display} | {pass_label}, {run_label}", ""]
        lines.append("Stage Results")
        if not self.stages:
            lines.append("  (no stages recorded)")
        else:
            for identifier, info in self.stages.items():
                description = info.get("description") or ""
                status = info.get("status") or "pending"
                elapsed_text = ""
                formatted = _format_elapsed(info.get("elapsed"))
                if formatted:
                    elapsed_text = f" ({formatted})"
                lines.append(f"  [{identifier}] {description} :: {status}{elapsed_text}")
                failure_reason = info.get("failure_reason")
                if failure_reason:
                    lines.append(f"      ↳ {failure_reason}")
        if self.coverage_percent is not None:
            percent_display = int(round(self.coverage_percent))
            parts = [f"Coverage: {percent_display}%"]
            if self.coverage_threshold:
                parts.append(f"threshold {self.coverage_threshold}")
            if self.coverage_targets:
                parts.append(f"targets: {', '.join(self.coverage_targets)}")
            lines.append("")
            lines.append(" ".join(parts))
        if self.mechanical is not None:
            changed = self.mechanical.get("changed")
            tools = ", ".join(self.mechanical.get("tools") or [])
            reason = self.mechanical.get("reason")
            status = "applied" if changed else "skipped"
            entry = f"Mechanical fixes: {status}"
            if tools:
                entry += f" [{tools}]"
            lines.append(entry)
            if reason and not changed:
                lines.append(f"  ↳ {reason}")
        if self.llm_decision is not None:
            accepted = bool(self.llm_decision.get("accepted"))
            reason = self.llm_decision.get("reason") or ""
            lines.append(f"LLM patch: {'accepted' if accepted else 'rejected'} ({reason})")
        if self.result is not None:
            lines.append("")
            lines.append(f"Result: {'PASS' if self.result else 'FAIL'}")
        return "\n".join(lines)


def render_discriminator_snapshot(
    *,
    slug: Optional[str],
    events: Iterable[dict[str, Any]],
    printer: _HUDPrinter,
) -> str:
    model = DiscriminatorHUDModel()
    relevant: list[dict[str, Any]] = []
    for event in events:
        if event.get("phase") != "discriminator":
            continue
        event_slug = event.get("slug")
        if slug is not None and event_slug not in (slug, None):
            continue
        relevant.append(event)
    if not relevant:
        return ""
    for event in relevant:
        model.apply_event(event)
    header_slug = slug or model.slug or "global"
    snapshot = model.render()
    header = printer.divider(f"Discriminator HUD :: {header_slug}")
    return f"{header}\n{snapshot}\n"


def discriminator_snapshot_text(slug: Optional[str], path: Path) -> str:
    events = _load_events(path)
    if not events:
        return ""
    printer = _HUDPrinter()
    return render_discriminator_snapshot(slug=slug, events=events, printer=printer)


def render_hud(
    *,
    phase: str,
    slug: Optional[str],
    events_file: Optional[str],
    context: RexContext,
) -> None:
    path = Path(events_file).expanduser() if events_file else default_events_path()
    if phase == "generator":
        resolved_slug = _resolve_generator_slug(slug, context=context)
        if not resolved_slug:
            print("[hud] No feature slug provided and no active card detected.")
            raise SystemExit(1)
        snapshot = generator_snapshot_text(resolved_slug, path)
        if not snapshot:
            print(f"[hud] No events recorded yet at {path}.")
            raise SystemExit(1)
        print(snapshot, end="")
        return
    if phase == "discriminator":
        snapshot = discriminator_snapshot_text(slug, path)
        if not snapshot:
            print(f"[hud] No discriminator events recorded yet at {path}.")
            raise SystemExit(1)
        print(snapshot, end="")
        return
    raise SystemExit(f"[hud] Unsupported phase: {phase}")
