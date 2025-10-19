"""Structured component planning for Feature Cards prior to spec generation."""

from __future__ import annotations

import hashlib
import json
import re
import textwrap
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .cards import FeatureCard
from .events import emit_event
from .llm import resolve_llm_provider
from .utils import RexContext, dump_json

COMPONENT_PLAN_SCHEMA_VERSION = "component-plan.v2"


@dataclass
class PlannerResult:
    plan: dict[str, Any]
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
        if (
            cached
            and cached.get("card_hash") == card_hash
            and cached.get("schema_version") == COMPONENT_PLAN_SCHEMA_VERSION
        ):
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

    base_plan: dict[str, Any] = {
        "schema_version": COMPONENT_PLAN_SCHEMA_VERSION,
        "card_path": str(card_path),
        "card_hash": card_hash,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "status": "in_progress",
        "components": [],
    }

    playbook_json = context.codex_ci_dir / f"playbook_{slug}.json"
    ledger_assumptions: list[dict[str, Any]] = []
    if playbook_json.exists():
        try:
            playbook_payload = json.loads(playbook_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            playbook_payload = {"error": "invalid_playbook_json"}
        base_plan["playbook_snapshot"] = playbook_payload
        ledger_assumptions = _extract_assumptions(playbook_payload)
    base_plan["assumptions"] = ledger_assumptions

    _emit_plan_snapshot(slug, base_plan)

    card_text = card_path.read_text(encoding="utf-8")
    other_cards = _collect_other_cards(card.path.parent, exclude=card_path)

    provider = resolve_llm_provider(
        context=context,
        codex_bin=codex_bin,
        codex_flags=codex_flags,
        codex_model=codex_model,
    )

    components_payload = provider.run_json(
        label="component-overview",
        prompt=_component_prompt(slug, card_text, other_cards),
        slug=slug,
        verbose=verbose,
    )
    components = _validate_components_payload(
        slug=slug,
        payload=components_payload,
    )

    for index, component in enumerate(components, start=1):
        comp_entry: dict[str, Any] = {
            "id": component["id"],
            "name": component["name"],
            "summary": component["summary"],
            "rationale": component["rationale"],
            "notes": component["notes"],
            "subcomponents": [],
        }
        comp_uid = comp_entry["id"]
        comp_name = comp_entry["name"]
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

        sub_payload = provider.run_json(
            label=f"subcomponents::{comp_name}",
            prompt=_subcomponent_prompt(
                slug=slug,
                card_text=card_text,
                component=comp_entry,
            ),
            slug=slug,
            verbose=verbose,
        )
        subcomponents = _validate_subcomponents_payload(
            slug=slug,
            component=comp_entry,
            payload=sub_payload,
        )
        for sub_index, sub in enumerate(subcomponents, start=1):
            sub_entry: dict[str, Any] = {
                "id": sub["id"],
                "name": sub["name"],
                "summary": sub["summary"],
                "dependencies": sub["dependencies"],
                "risks": sub["risks"],
                "tests": [],
            }
            sub_name = sub_entry["name"]
            sub_uid = sub_entry["id"]
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

            tests_payload = provider.run_json(
                label=f"tests::{comp_name}::{sub_name}",
                prompt=_test_prompt(
                    slug=slug,
                    card_text=card_text,
                    component=comp_entry,
                    subcomponent=sub_entry,
                    assumptions=ledger_assumptions,
                ),
                slug=slug,
                verbose=verbose,
            )
            tests = _validate_tests_payload(
                slug=slug,
                component=comp_entry,
                subcomponent=sub_entry,
                payload=tests_payload,
            )
            for test_index, test in enumerate(tests, start=1):
                test_entry = {
                    "id": test["id"],
                    "question": test["question"],
                    "measurement": test["measurement"],
                    "context": test["context"],
                    "status": test["status"],
                    "tags": test["tags"],
                    "assumptions": test["assumptions"],
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
    dump_json(
        plan_path,
        base_plan,
        ensure_ascii=False,
        sort_keys=False,
    )
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


def _emit_plan_snapshot(
    slug: str, plan: dict[str, Any], *, plan_path: Path | None = None
) -> None:
    meta: dict[str, Any] = {"plan": plan, "plan_slug": slug}
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


def _collect_other_cards(cards_dir: Path, exclude: Path) -> list[dict[str, str]]:
    payload: list[dict[str, str]] = []
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


class PlannerSchemaError(RuntimeError):
    """Raised when Codex planner output does not satisfy the expected schema."""


def _extract_assumptions(payload: Any) -> list[dict[str, Any]]:
    assumptions_root = {}
    if isinstance(payload, Mapping):
        assumptions_root = payload.get("assumptions", {})
    entries = assumptions_root.get("assumptions") if isinstance(assumptions_root, Mapping) else []
    sanitized: list[dict[str, Any]] = []
    if not isinstance(entries, list):
        return sanitized
    for raw in entries:
        if not isinstance(raw, Mapping):
            continue
        assumption_id = _clean_string(raw.get("id")).upper()
        text = _clean_string(raw.get("text"))
        risk = _clean_string(raw.get("risk"), default="unknown").lower()
        default_choice = _clean_string(raw.get("default_choice"))
        falsify = _clean_string_list(raw.get("ways_to_falsify"))
        sanitized.append(
            {
                "id": assumption_id or "",
                "text": text,
                "risk": risk or "unknown",
                "default_choice": default_choice,
                "ways_to_falsify": falsify,
            }
        )
    return sanitized


def _validate_components_payload(
    *, slug: str, payload: Any
) -> list[dict[str, Any]]:
    if not isinstance(payload, Mapping):
        raise PlannerSchemaError("Planner response must be a JSON object.")
    raw_components = payload.get("components")
    if not isinstance(raw_components, list):
        raise PlannerSchemaError("Planner response must contain a components list.")
    if len(raw_components) == 0:
        raise PlannerSchemaError("Planner must propose at least one component.")
    sanitized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw_component in enumerate(raw_components):
        if not isinstance(raw_component, Mapping):
            raise PlannerSchemaError(f"Component #{index + 1} must be an object.")
        name = _clean_string(
            raw_component.get("name"),
            default=f"Component {index + 1}",
        )
        summary = _clean_string(raw_component.get("summary"))
        rationale = _clean_string(raw_component.get("rationale"))
        notes = _clean_string(raw_component.get("notes"))
        candidate_id = _clean_string(raw_component.get("id"))
        component_id = _ensure_component_id(
            slug=slug,
            candidate=candidate_id,
            name=name,
            summary=summary,
            seen=seen_ids,
        )
        seen_ids.add(component_id)
        sanitized.append(
            {
                "id": component_id,
                "name": name,
                "summary": summary,
                "rationale": rationale,
                "notes": notes,
            }
        )
    return sanitized


def _validate_subcomponents_payload(
    *,
    slug: str,
    component: Mapping[str, Any],
    payload: Any,
) -> list[dict[str, Any]]:
    if not isinstance(payload, Mapping):
        raise PlannerSchemaError("Subcomponent response must be a JSON object.")
    raw_subcomponents = payload.get("subcomponents")
    if not isinstance(raw_subcomponents, list):
        raise PlannerSchemaError("Response must contain a subcomponents list.")
    if len(raw_subcomponents) == 0:
        raise PlannerSchemaError("Each component must have at least one subcomponent.")
    sanitized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw_subcomponent in enumerate(raw_subcomponents):
        if not isinstance(raw_subcomponent, Mapping):
            raise PlannerSchemaError(f"Subcomponent #{index + 1} must be an object.")
        name = _clean_string(
            raw_subcomponent.get("name"),
            default=f"{component.get('name', 'Component')} :: Subcomponent {index + 1}",
        )
        summary = _clean_string(raw_subcomponent.get("summary"))
        dependencies = _clean_string_list(raw_subcomponent.get("dependencies"))
        risks = _clean_string_list(raw_subcomponent.get("risks"))
        candidate_id = _clean_string(raw_subcomponent.get("id"))
        subcomponent_id = _ensure_subcomponent_id(
            slug=slug,
            component_id=str(component.get("id", "")),
            component_name=str(component.get("name", "")),
            candidate=candidate_id,
            name=name,
            summary=summary,
            seen=seen_ids,
        )
        seen_ids.add(subcomponent_id)
        sanitized.append(
            {
                "id": subcomponent_id,
                "name": name,
                "summary": summary,
                "dependencies": dependencies,
                "risks": risks,
            }
        )
    return sanitized


def _validate_tests_payload(
    *,
    slug: str,
    component: Mapping[str, Any],
    subcomponent: Mapping[str, Any],
    payload: Any,
) -> list[dict[str, Any]]:
    if not isinstance(payload, Mapping):
        raise PlannerSchemaError("Test response must be a JSON object.")
    raw_tests = payload.get("tests")
    if not isinstance(raw_tests, list):
        raise PlannerSchemaError("Response must contain a tests list.")
    if len(raw_tests) == 0:
        raise PlannerSchemaError(
            "Each subcomponent must propose at least one test (use status 'spec-gap' "
            "when a case cannot be written)."
        )
    sanitized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw_test in enumerate(raw_tests):
        if not isinstance(raw_test, Mapping):
            raise PlannerSchemaError(f"Test #{index + 1} must be an object.")
        question = _extract_question(raw_test, index + 1)
        measurement = _extract_measurement(raw_test)
        context_note = _clean_string(
            raw_test.get("context") or raw_test.get("description")
        )
        status = _clean_string(raw_test.get("status"), default="proposed") or "proposed"
        tags = _clean_string_list(raw_test.get("tags"))
        assumption_ids = _clean_assumption_ids(raw_test.get("assumptions"))
        candidate_id = _clean_string(raw_test.get("id"))
        if not measurement and status != "spec-gap":
            raise PlannerSchemaError(
                f"Test #{index + 1} must include a measurement or be marked spec-gap."
            )
        test_id = _ensure_test_id(
            slug=slug,
            component_id=str(component.get("id", "")),
            subcomponent_id=str(subcomponent.get("id", "")),
            question=question,
            candidate=candidate_id,
            seen=seen_ids,
        )
        seen_ids.add(test_id)
        sanitized.append(
            {
                "id": test_id,
                "question": question,
                "measurement": measurement,
                "context": context_note,
                "status": status or "proposed",
                "tags": tags,
                "assumptions": assumption_ids,
            }
        )
    return sanitized



def _component_prompt(
    slug: str, card_text: str, other_cards: list[dict[str, str]]
) -> str:
    extras = (
        "\n".join(f"- {card['name']} ({card['path']})" for card in other_cards)
        or "None"
    )
    return textwrap.dedent(
        f"""\
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
        """
    ).strip()


def _subcomponent_prompt(
    *,
    slug: str,
    card_text: str,
    component: dict[str, Any],
) -> str:
    summary = component.get("summary", "")
    rationale = component.get("rationale", "")
    return textwrap.dedent(
        f"""\
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
        """
    ).strip()


def _test_prompt(
    *,
    slug: str,
    card_text: str,
    component: dict[str, Any],
    subcomponent: dict[str, Any],
    assumptions: list[dict[str, Any]],
) -> str:
    summary = subcomponent.get("summary", "")
    deps = ", ".join(subcomponent.get("dependencies") or []) or "None stated"
    assumption_lines: list[str] = []
    critical_ids: list[str] = []
    for assumption in assumptions:
        if not isinstance(assumption, Mapping):
            continue
        assumption_id = str(assumption.get("id", "")).strip()
        text = str(assumption.get("text", "")).strip()
        risk = str(assumption.get("risk", "unknown")).strip()
        default_choice = str(assumption.get("default_choice", "")).strip()
        ways_to_falsify = assumption.get("ways_to_falsify") or []
        falsify_text = ""
        if ways_to_falsify:
            falsify_text = f" Ways to falsify: {', '.join(ways_to_falsify)}."
        line = f"- {assumption_id} (risk={risk}): {text}.{falsify_text}"
        if default_choice:
            line += f" Default: {default_choice}."
        assumption_lines.append(line)
        if risk.lower() in {"high", "critical"} and assumption_id:
            critical_ids.append(assumption_id)
    assumptions_block = "\n".join(assumption_lines) if assumption_lines else "None recorded."
    critical_clause = (
        "High/critical risk assumptions: "
        + ", ".join(critical_ids)
        if critical_ids
        else "No high/critical risk assumptions recorded."
    )
    return textwrap.dedent(
        f"""\
        You are proposing deterministic pytest scenarios for slug `{slug}`.
        Component: {component.get('name')}
        Subcomponent: {subcomponent.get('name')}

        Return STRICT JSON object:
        {{
          "tests": [
            {{
              "id": "<stable-id>",
              "question": "Does the CLI print Hello World by default?",
              "measurement": "Invoke the CLI with no arguments and assert stdout equals 'Hello World' and exit code is 0.",
              "context": "Optional extra notes or setup details",
              "status": "proposed",
              "assumptions": ["A-001"],
              "tags": ["happy-path", "cli"]
            }}
          ]
        }}

        Guidelines:
        - Every `question` must be a concrete yes/no style question framed from an observer's perspective (e.g. "Does quiet suppress output?").
        - `measurement` must describe the exact deterministic procedure used to answer the question (inputs, command, and assertions).
        - Use `context` for additional setup hints only when necessary; otherwise omit or keep short.
        - Tests must remain offline, hermetic, and avoid randomness or time-based assertions.
        - Cover happy path, edge cases, and failure behaviours implied by the Feature Card.
        - Prefer status "proposed" unless guidance indicates otherwise. Use status "spec-gap" only when a test cannot be written yet; include the related assumption IDs in that case.
        - Every test must include an `assumptions` array referencing relevant assumption IDs (e.g. A-001). When no assumption applies, return an empty array.
        - {critical_clause}
        - For each assumption, propose at least one verification or falsification strategy aligned with its ways to falsify. Explicitly note negative scenarios when stress-testing assumptions.

        Subcomponent summary: {summary}
        Dependencies: {deps}

        Assumption ledger entries for this Feature Card:
        {assumptions_block}

        --- FEATURE CARD START ---
        {card_text}
        --- FEATURE CARD END ---
        """
    ).strip()


def _clean_string(value: object, *, default: str = "") -> str:
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned if cleaned else default
    return default


def _clean_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        if isinstance(item, str):
            cleaned = item.strip()
            if cleaned:
                items.append(cleaned)
    return items


def _clean_assumption_ids(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        candidate = item.strip().upper()
        if not candidate:
            continue
        if not re.fullmatch(r"A-\d+", candidate):
            continue
        items.append(candidate)
    return items


def _normalize_identifier(text: str) -> str:
    lowered = text.lower()
    collapsed = re.sub(r"[^a-z0-9]+", "-", lowered)
    normalized = re.sub(r"-{2,}", "-", collapsed).strip("-")
    return normalized or "item"


def _stable_digest(*parts: str) -> str:
    joined = "::".join(part.strip().lower() for part in parts if part)
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()


def _dedupe_identifier(base: str, seen: set[str]) -> str:
    candidate = base
    index = 1
    while candidate in seen or not candidate:
        candidate = f"{base}-{index}"
        index += 1
    return candidate


def _ensure_component_id(
    *,
    slug: str,
    candidate: str,
    name: str,
    summary: str,
    seen: set[str],
) -> str:
    if candidate:
        normalized = _normalize_identifier(candidate)
        return _dedupe_identifier(normalized, seen)
    name_slug = _normalize_identifier(name or "component")
    digest = _stable_digest(slug, "component", name, summary)[:10]
    base = f"{slug}-c-{name_slug}-{digest}"
    return _dedupe_identifier(base, seen)


def _ensure_subcomponent_id(
    *,
    slug: str,
    component_id: str,
    component_name: str,
    candidate: str,
    name: str,
    summary: str,
    seen: set[str],
) -> str:
    if candidate:
        normalized = _normalize_identifier(candidate)
        return _dedupe_identifier(normalized, seen)
    name_slug = _normalize_identifier(name or "subcomponent")
    digest = _stable_digest(
        slug,
        component_id,
        component_name,
        "subcomponent",
        name,
        summary,
    )[:10]
    base = f"{slug}-s-{name_slug}-{digest}"
    return _dedupe_identifier(base, seen)


def _ensure_test_id(
    *,
    slug: str,
    component_id: str,
    subcomponent_id: str,
    question: str,
    candidate: str,
    seen: set[str],
) -> str:
    if candidate:
        normalized = _normalize_identifier(candidate)
        return _dedupe_identifier(normalized, seen)
    question_slug = _normalize_identifier(question[:60])
    digest = _stable_digest(
        slug,
        component_id,
        subcomponent_id,
        "test",
        question,
    )[:12]
    base = f"{slug}-t-{question_slug}-{digest}"
    return _dedupe_identifier(base, seen)


def _extract_question(test: Mapping[str, Any], index: int) -> str:
    if not isinstance(test, Mapping):
        return f"Test {index}?"
    for key in ("question", "name", "title"):
        value = test.get(key)
        if isinstance(value, str) and value.strip():
            return _ensure_question(value)
    return f"Test {index}?"


def _extract_measurement(test: Mapping[str, Any]) -> str:
    if not isinstance(test, Mapping):
        return ""
    for key in ("measurement", "verification", "how_to_verify", "strategy"):
        value = test.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    desc = test.get("description")
    if isinstance(desc, str) and desc.strip():
        return desc.strip()
    return ""


def _ensure_question(text: str) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        return "Question?"
    if cleaned.endswith("?"):
        return cleaned
    if cleaned[-1:] in ".!;":
        cleaned = cleaned[:-1].strip()
    lowered = cleaned.lower()
    prefixes = ("does ", "is ", "can ", "will ", "should ", "did ")
    if any(lowered.startswith(prefix) for prefix in prefixes):
        base = cleaned
    else:
        base = (
            f"Does {cleaned[0].lower() + cleaned[1:]}" if len(cleaned) > 1 else cleaned
        )
    return f"{base}?"
