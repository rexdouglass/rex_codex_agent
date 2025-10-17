"""Structured component planning for Feature Cards prior to spec generation."""

from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from .cards import FeatureCard
from .events import emit_event
from .utils import RexContext


@dataclass
class PlannerResult:
    plan: Dict[str, Any]
    path: Path


def ensure_component_plan(
    *,
    card: FeatureCard,
    context: RexContext,
    codex_bin: str,
    codex_flags: str,
    codex_model: str,
    verbose: bool = True,
) -> PlannerResult:
    """Build (or reuse) the component/subcomponent/test map for a Feature Card."""

    card_path = card.path
    slug = card.slug
    card_hash = _hash_path(card_path)
    plan_path = context.codex_ci_dir / f"component_plan_{slug}.json"

    if plan_path.exists():
        try:
            cached = json.loads(plan_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            cached = None
        if cached and cached.get("card_hash") == card_hash:
            return PlannerResult(plan=cached, path=plan_path)

    if verbose:
        print(f"[planner] Generating component plan for {slug}")
    emit_event(
        "generator",
        "component_plan_started",
        slug=slug,
        task=f"plan/{slug}",
        card_path=str(card_path),
    )

    base_plan: Dict[str, Any] = {
        "card_path": str(card_path),
        "card_hash": card_hash,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "status": "in_progress",
        "components": [],
    }

    _emit_plan_snapshot(slug, base_plan)

    card_text = card_path.read_text(encoding="utf-8")
    other_cards = _collect_other_cards(card.path.parent, exclude=card_path)

    components_payload = _run_codex_json(
        label="component-overview",
        prompt=_component_prompt(slug, card_text, other_cards),
        slug=slug,
        context=context,
        codex_bin=codex_bin,
        codex_flags=codex_flags,
        codex_model=codex_model,
        verbose=verbose,
    )
    components = components_payload.get("components") or []

    for index, component in enumerate(components, start=1):
        comp_name = component.get("name") or f"Component {index}"
        comp_uid = component.get("id") or f"{slug}-c{index}-{uuid.uuid4().hex[:6]}"
        comp_entry: Dict[str, Any] = {
            "id": comp_uid,
            "name": comp_name,
            "summary": component.get("summary") or "",
            "rationale": component.get("rationale") or "",
            "notes": component.get("notes") or "",
            "subcomponents": [],
        }
        base_plan["components"].append(comp_entry)
        _emit_plan_snapshot(slug, base_plan)
        emit_event(
            "generator",
            "component_plan_component_started",
            slug=slug,
            task=f"plan/{slug}",
            component=comp_name,
            component_index=index,
        )

        sub_payload = _run_codex_json(
            label=f"subcomponents::{comp_name}",
            prompt=_subcomponent_prompt(
                slug=slug,
                card_text=card_text,
                component=comp_entry,
            ),
            slug=slug,
            context=context,
            codex_bin=codex_bin,
            codex_flags=codex_flags,
            codex_model=codex_model,
            verbose=verbose,
        )
        subcomponents = sub_payload.get("subcomponents") or []
        for sub_index, sub in enumerate(subcomponents, start=1):
            sub_name = sub.get("name") or f"{comp_name} :: Subcomponent {sub_index}"
            sub_uid = sub.get("id") or f"{comp_uid}-s{sub_index}-{uuid.uuid4().hex[:6]}"
            sub_entry: Dict[str, Any] = {
                "id": sub_uid,
                "name": sub_name,
                "summary": sub.get("summary") or "",
                "dependencies": sub.get("dependencies") or [],
                "risks": sub.get("risks") or [],
                "tests": [],
            }
            comp_entry["subcomponents"].append(sub_entry)
            _emit_plan_snapshot(slug, base_plan)
            emit_event(
                "generator",
                "component_plan_subcomponent_started",
                slug=slug,
                task=f"plan/{slug}",
                component=comp_name,
                subcomponent=sub_name,
                component_index=index,
                subcomponent_index=sub_index,
            )

            tests_payload = _run_codex_json(
                label=f"tests::{comp_name}::{sub_name}",
                prompt=_test_prompt(
                    slug=slug,
                    card_text=card_text,
                    component=comp_entry,
                    subcomponent=sub_entry,
                ),
                slug=slug,
                context=context,
                codex_bin=codex_bin,
                codex_flags=codex_flags,
                codex_model=codex_model,
                verbose=verbose,
            )
            tests = tests_payload.get("tests") or []
            for test_index, test in enumerate(tests, start=1):
                test_entry = {
                    "id": test.get("id")
                    or f"{sub_uid}-t{test_index}-{uuid.uuid4().hex[:6]}",
                    "name": test.get("name") or f"Test {test_index}",
                    "description": test.get("description") or "",
                    "type": test.get("type") or "pytest",
                    "verification": test.get("verification") or "",
                    "status": test.get("status") or "proposed",
                    "tags": test.get("tags") or [],
                }
                sub_entry["tests"].append(test_entry)
                _emit_plan_snapshot(slug, base_plan)

            emit_event(
                "generator",
                "component_plan_subcomponent_completed",
                slug=slug,
                task=f"plan/{slug}",
                component=comp_name,
                subcomponent=sub_name,
                total_tests=len(sub_entry["tests"]),
            )

        emit_event(
            "generator",
            "component_plan_component_completed",
            slug=slug,
            task=f"plan/{slug}",
            component=comp_name,
            subcomponents=len(comp_entry["subcomponents"]),
        )
        _emit_plan_snapshot(slug, base_plan)

    base_plan["status"] = "completed"
    base_plan["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    plan_path.write_text(json.dumps(base_plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    emit_event(
        "generator",
        "component_plan_completed",
        slug=slug,
        task=f"plan/{slug}",
        plan_path=str(plan_path),
    )
    _emit_plan_snapshot(slug, base_plan, plan_path=plan_path)
    if verbose:
        print(f"[planner] Component plan written to {plan_path}")
    return PlannerResult(plan=base_plan, path=plan_path)


def _emit_plan_snapshot(slug: str, plan: Dict[str, Any], *, plan_path: Path | None = None) -> None:
    meta: Dict[str, Any] = {"plan": plan}
    if plan_path is not None:
        meta["plan_path"] = str(plan_path)
    emit_event(
        "generator",
        "component_plan_snapshot",
        slug=slug,
        task=f"plan/{slug}",
        **meta,
    )


def _hash_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _collect_other_cards(cards_dir: Path, exclude: Path) -> List[Dict[str, str]]:
    payload: List[Dict[str, str]] = []
    if not cards_dir.exists():
        return payload
    for item in sorted(cards_dir.glob("*.md")):
        if item == exclude:
            continue
        try:
            payload.append(
                {
                    "path": str(item),
                    "name": item.stem.replace("-", " ").title(),
                }
            )
        except OSError:
            continue
    return payload


def _component_prompt(slug: str, card_text: str, other_cards: List[Dict[str, str]]) -> str:
    extras = "\n".join(f"- {card['name']} ({card['path']})" for card in other_cards) or "None"
    return f"""
You are an engineering planner. Analyse the Feature Card below for slug `{slug}` and produce a JSON object
with this shape:
{{
  "components": [
    {{
      "id": "<stable-id>",
      "name": "<concise component name>",
      "summary": "<what this component does>",
      "rationale": "<why it exists / business value>",
      "notes": "<implementation hints or constraints>"
    }}
  ]
}}

Guidelines:
- Focus on end-user behaviours and supporting systems implied by the Feature Card.
- Components should be coarse-grained areas of responsibility that we can later split into subcomponents.
- Return STRICT JSON (no markdown or explanations).

Existing Feature Cards in the repository:
{extras}

--- FEATURE CARD START ---
{card_text}
--- FEATURE CARD END ---
""".strip()


def _subcomponent_prompt(
    *,
    slug: str,
    card_text: str,
    component: Dict[str, Any],
) -> str:
    summary = component.get("summary", "")
    rationale = component.get("rationale", "")
    return f"""
You are breaking down component `{component.get('name')}` (slug `{slug}`) into subcomponents.
Return STRICT JSON object:
{{
  "subcomponents": [
    {{
      "id": "<stable-id>",
      "name": "<subcomponent name>",
      "summary": "<scope and responsibilities>",
      "dependencies": ["<optional external dependency>", "..."],
      "risks": ["<optional risk>", "..."]
    }}
  ]
}}

Guidelines:
- Subcomponents should be testable slices (e.g., CLI parsing, config validation, logging).
- Include dependencies/risks only if they are truly relevant.
- Base your reasoning on the component summary, rationale, and Feature Card.

Component summary: {summary}
Component rationale: {rationale}

--- FEATURE CARD START ---
{card_text}
--- FEATURE CARD END ---
""".strip()


def _test_prompt(
    *,
    slug: str,
    card_text: str,
    component: Dict[str, Any],
    subcomponent: Dict[str, Any],
) -> str:
    summary = subcomponent.get("summary", "")
    deps = ", ".join(subcomponent.get("dependencies") or []) or "None stated"
    return f"""
You are proposing deterministic pytest scenarios for slug `{slug}`.
Component: {component.get('name')}
Subcomponent: {subcomponent.get('name')}

Return STRICT JSON object:
{{
  "tests": [
    {{
      "id": "<stable-id>",
      "name": "<short test name>",
      "description": "<intent of the test>",
      "type": "pytest",
      "verification": "<how we assert success>",
      "status": "proposed",
      "tags": ["<optional>", "..."]
    }}
  ]
}}

Guidelines:
- Tests must be verifiable offline and avoid randomness.
- Cover happy path, edge cases, and failure behaviours suggested by the Feature Card.
- If a scenario already exists in spirit, call it out in the description.

Subcomponent summary: {summary}
Dependencies: {deps}

--- FEATURE CARD START ---
{card_text}
--- FEATURE CARD END ---
""".strip()


def _run_codex_json(
    *,
    label: str,
    prompt: str,
    slug: str,
    context: RexContext,
    codex_bin: str,
    codex_flags: str,
    codex_model: str,
    verbose: bool,
) -> Dict[str, Any]:
    if verbose:
        print(f"[planner] Calling Codex ({label})…")
    emit_event(
        "generator",
        "component_plan_stage_started",
        slug=slug,
        task=f"plan/{slug}",
        stage=label,
    )
    cmd = shlex.split(codex_bin) + ["exec"]
    if codex_flags.strip():
        cmd += shlex.split(codex_flags)
    if codex_model:
        cmd += ["--model", codex_model]
    cmd += ["--cd", str(context.root), "--", prompt]

    completed = subprocess.run(
        cmd,
        cwd=context.root,
        text=True,
        capture_output=True,
    )
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    if completed.returncode != 0:
        emit_event(
            "generator",
            "component_plan_stage_failed",
            slug=slug,
            task=f"plan/{slug}",
            stage=label,
            stderr=stderr.strip(),
        )
        raise RuntimeError(f"Codex ({label}) failed with exit code {completed.returncode}: {stderr.strip()}")

    payload = _extract_json(stdout)
    emit_event(
        "generator",
        "component_plan_stage_completed",
        slug=slug,
        task=f"plan/{slug}",
        stage=label,
    )
    return payload


def _extract_json(text: str) -> Dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        return {}
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    start_candidates = [stripped.find("{"), stripped.find("[")]
    start_candidates = [idx for idx in start_candidates if idx != -1]
    if not start_candidates:
        raise json.JSONDecodeError("No JSON object found", stripped, 0)
    start = min(start_candidates)
    for end in range(len(stripped), start, -1):
        fragment = stripped[start:end]
        try:
            data = json.loads(fragment)
            return data
        except json.JSONDecodeError:
            continue
    raise json.JSONDecodeError("Unable to decode JSON response", stripped, start)
