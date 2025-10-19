"""Implementation of the Codex testing playbook.

This module codifies the guidance from AGENTS.md into deterministic helpers that
translate Feature Cards into canonical data, assumption ledgers, scenario plans,
and repository intelligence snapshots. The generator imports these artefacts to
keep prompts grounded and to persist traceability evidence for audits.
"""

from __future__ import annotations

import csv
import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .cards import FeatureCard
from .utils import RexContext, dump_json, ensure_dir

PLAYBOOK_ARTIFACT_SCHEMA_VERSION = "playbook-artifacts.v2"
ASSUMPTION_LEDGER_SCHEMA_VERSION = "assumption-ledger.v2"

# ---------------------------------------------------------------------------
# Canonical data model
# ---------------------------------------------------------------------------


def _slug_to_feature_id(slug: str) -> str:
    token = re.sub(r"[^A-Za-z0-9]+", "-", slug).strip("-")
    token = token.upper()
    if token.startswith("FC-"):
        return token
    return f"FC-{token}"


def _normalize_heading(name: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return normalized or "section"


def _parse_sections(text: str) -> tuple[dict[str, list[str]], str | None]:
    sections: dict[str, list[str]] = {"__root__": []}
    current = "__root__"
    first_heading: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip("\n")
        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            level = len(heading.group(1))
            title = heading.group(2).strip()
            key = _normalize_heading(title)
            sections.setdefault(key, [])
            current = key
            if level == 1 and first_heading is None:
                first_heading = title
            continue
        sections.setdefault(current, []).append(line)
    return sections, first_heading


def _extract_metadata(lines: list[str]) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for line in lines:
        if not line or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if not key:
            continue
        metadata[key] = value
    return metadata


def _extract_bullets(lines: list[str]) -> list[str]:
    bullets: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("- "):
            bullets.append(stripped[2:].strip())
    return bullets


def _extract_keyed_lists(lines: list[str]) -> dict[str, list[str]]:
    keyed: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.endswith(":"):
            current = _normalize_heading(stripped[:-1])
            keyed.setdefault(current, [])
            continue
        if stripped.startswith("- "):
            value = stripped[2:].strip()
            if current:
                keyed.setdefault(current, []).append(value)
            else:
                keyed.setdefault("items", []).append(value)
        elif current:
            keyed.setdefault(current, []).append(stripped)
    return keyed


def _parse_csv_list(value: str) -> list[str]:
    if not value:
        return []
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1]
        items = [item.strip().strip("'\"") for item in inner.split(",")]
        return [item for item in items if item]
    items = [item.strip() for item in re.split(r"[,;]", value)]
    if len(items) == 1 and " " in items[0]:
        # allow space-separated dependency list
        items = [item.strip() for item in items[0].split() if item.strip()]
    return [item for item in items if item]


def _strip_wrapper(text: str) -> str:
    return text.strip().strip("\"'`")


@dataclass
class AcceptanceCriterion:
    id: str
    text: str

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "text": self.text}


@dataclass
class ObservabilityHints:
    logs: list[str] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    metrics: list[str] = field(default_factory=list)
    traces: list[str] = field(default_factory=list)
    other: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, list[str]]:
        return {
            "logs": self.logs,
            "events": self.events,
            "metrics": self.metrics,
            "traces": self.traces,
            "other": self.other,
        }


@dataclass
class FeatureCardModel:
    slug: str
    card_path: str
    id: str
    title: str
    epic: str
    risk_level: str
    priority: str
    owner: str
    version: int
    dependencies: list[str]
    acceptance_criteria: list[AcceptanceCriterion]
    non_goals: list[str]
    open_questions: list[str]
    constraints: dict[str, list[str]]
    observability: ObservabilityHints
    notes: str
    summary: str

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["acceptance_criteria"] = [
            criterion.to_dict() for criterion in self.acceptance_criteria
        ]
        data["observability"] = self.observability.to_dict()
        return data


def canonicalize_feature_card(card: FeatureCard) -> FeatureCardModel:
    text = card.path.read_text(encoding="utf-8")
    sections, first_heading = _parse_sections(text)
    metadata = _extract_metadata(sections.get("__root__", []))
    summary_lines = sections.get("summary", [])
    notes_lines = sections.get("notes", [])

    meta_aliases = {
        "id": "id",
        "feature_id": "id",
        "feature-card": "id",
        "feature_card": "id",
        "card_id": "id",
        "risk": "risk_level",
        "risk_level": "risk_level",
        "priority": "priority",
        "owner": "owner",
        "team": "owner",
        "version": "version",
        "dependencies": "dependencies",
        "depends": "dependencies",
        "epic": "epic",
    }

    meta_store: dict[str, str] = {}
    for key, value in metadata.items():
        target = meta_aliases.get(key)
        if not target:
            continue
        meta_store[target] = value

    title = first_heading or card.slug.replace("-", " ").title()
    card_id = meta_store.get("id") or _slug_to_feature_id(card.slug)
    epic = meta_store.get("epic", "")
    risk_level = (meta_store.get("risk_level") or "unknown").lower()
    priority = meta_store.get("priority", "unknown").upper()
    owner = meta_store.get("owner", "")
    version_value = meta_store.get("version", "1")
    try:
        version = int(float(version_value))
    except ValueError:
        version = 1
    dependencies = _parse_csv_list(meta_store.get("dependencies", ""))

    acceptance_lines = []
    for tag in ("acceptance_criteria", "acceptance", "criteria"):
        if sections.get(tag):
            acceptance_lines = sections.get(tag, [])
            break
    acceptance_bullets = _extract_bullets(acceptance_lines)
    acceptance: list[AcceptanceCriterion] = []
    for index, bullet in enumerate(acceptance_bullets, start=1):
        match = re.match(
            r"^(AC(?:[-_#\s]?)(\d+))[:\s.-]*(.*)$", bullet, flags=re.IGNORECASE
        )
        if match:
            number = match.group(2)
            remainder = match.group(3).strip() or bullet
            acceptance.append(AcceptanceCriterion(f"AC-{int(number)}", remainder))
        else:
            acceptance.append(AcceptanceCriterion(f"AC-{index}", bullet.strip()))

    non_goals = _extract_bullets(
        sections.get("non_goals", [])
        or sections.get("non-goals", [])
        or sections.get("out_of_scope", [])
    )
    open_questions = _extract_bullets(
        sections.get("open_questions", [])
        or sections.get("questions", [])
        or sections.get("unknowns", [])
    )

    constraint_lines = (
        sections.get("constraints", [])
        or sections.get("limitations", [])
        or sections.get("domain_invariants", [])
    )
    constraints = _extract_keyed_lists(constraint_lines)
    if not constraints:
        # Preserve raw text when structure is unknown
        filtered = [line for line in constraint_lines if line.strip()]
        if filtered:
            constraints["items"] = filtered

    observability_lines = (
        sections.get("observability", [])
        or sections.get("observability_hints", [])
        or sections.get("telemetry", [])
    )
    observability_pairs = _extract_keyed_lists(observability_lines)
    observability = ObservabilityHints()
    for key, values in observability_pairs.items():
        if key in {"logs", "log"}:
            observability.logs.extend(map(_strip_wrapper, values))
        elif key in {"metrics", "metric"}:
            observability.metrics.extend(map(_strip_wrapper, values))
        elif key in {"events", "event"}:
            observability.events.extend(map(_strip_wrapper, values))
        elif key in {"traces", "trace"}:
            observability.traces.extend(map(_strip_wrapper, values))
        else:
            observability.other.extend(map(_strip_wrapper, values))

    notes_text = "\n".join(line for line in notes_lines if line.strip()).strip()
    summary_text = "\n".join(line for line in summary_lines if line.strip()).strip()

    return FeatureCardModel(
        slug=card.slug,
        card_path=str(card.path),
        id=card_id,
        title=title,
        epic=epic,
        risk_level=risk_level or "unknown",
        priority=priority or "UNKNOWN",
        owner=owner,
        version=version,
        dependencies=dependencies,
        acceptance_criteria=acceptance,
        non_goals=non_goals,
        open_questions=open_questions,
        constraints=constraints,
        observability=observability,
        notes=notes_text,
        summary=summary_text,
    )


# ---------------------------------------------------------------------------
# Assumption ledger
# ---------------------------------------------------------------------------


def _normalise_assumption_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


@dataclass
class Assumption:
    id: str
    text: str
    rationale: str = ""
    risk: str = "medium"
    default_choice: str = ""
    ways_to_falsify: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "text": self.text,
            "rationale": self.rationale,
            "risk": self.risk,
            "default_choice": self.default_choice,
            "ways_to_falsify": self.ways_to_falsify,
        }


class AssumptionLedger:
    def __init__(self, path: Path, feature_id: str):
        self.path = path
        self.feature_id = feature_id
        self.assumptions: list[Assumption] = []
        self.escalation_hints: list[str] = []
        self._index: dict[str, Assumption] = {}

    @classmethod
    def load(cls, context: RexContext, feature: FeatureCardModel) -> AssumptionLedger:
        ledger_dir = ensure_dir(context.root / "documents" / "assumption_ledgers")
        ledger_path = ledger_dir / f"{feature.slug}.json"
        ledger = cls(ledger_path, feature.id)
        if ledger_path.exists():
            try:
                data = json.loads(ledger_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                data = {}
            version = data.get("schema_version")
            if version not in (None, ASSUMPTION_LEDGER_SCHEMA_VERSION):
                # Preserve best-effort compatibility by continuing to parse but flagging via metadata.
                warning = (
                    f"[migration-required] Unsupported ledger schema {version!r}; regenerated on save"
                )
                if warning not in ledger.escalation_hints:
                    ledger.escalation_hints.append(warning)
            for item in data.get("assumptions", []):
                assumption = Assumption(
                    id=item.get("id", ""),
                    text=item.get("text", ""),
                    rationale=item.get("rationale", ""),
                    risk=item.get("risk", "medium"),
                    default_choice=item.get("default_choice", ""),
                    ways_to_falsify=item.get("ways_to_falsify", []),
                )
                if assumption.id:
                    ledger.assumptions.append(assumption)
                    ledger._index[_normalise_assumption_text(assumption.text)] = (
                        assumption
                    )
            ledger.escalation_hints = data.get("escalation_hints", [])
        return ledger

    def _next_id(self) -> str:
        existing_numbers = [
            int(match.group(1))
            for assumption in self.assumptions
            if (match := re.match(r"A-(\d+)", assumption.id))
        ]
        next_number = max(existing_numbers, default=0) + 1
        return f"A-{next_number:03d}"

    def require(
        self,
        text: str,
        *,
        rationale: str,
        risk: str = "medium",
        default_choice: str = "",
        ways_to_falsify: Sequence[str] | None = None,
    ) -> str:
        normalized = _normalise_assumption_text(text)
        existing = self._index.get(normalized)
        if existing:
            return existing.id
        assumption = Assumption(
            id=self._next_id(),
            text=text.strip(),
            rationale=rationale.strip(),
            risk=risk,
            default_choice=default_choice.strip(),
            ways_to_falsify=list(ways_to_falsify or []),
        )
        self.assumptions.append(assumption)
        self._index[normalized] = assumption
        return assumption.id

    def add_escalation_hint(self, hint: str) -> None:
        cleaned = hint.strip()
        if not cleaned:
            return
        if cleaned not in self.escalation_hints:
            self.escalation_hints.append(cleaned)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": ASSUMPTION_LEDGER_SCHEMA_VERSION,
            "feature_id": self.feature_id,
            "assumptions": [assumption.to_dict() for assumption in self.assumptions],
            "escalation_hints": self.escalation_hints,
        }

    def save(self) -> None:
        payload = self.to_dict()
        dump_json(self.path, payload, ensure_ascii=False, sort_keys=False)


# ---------------------------------------------------------------------------
# Repository intelligence
# ---------------------------------------------------------------------------


EXTENSION_LANG_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
}


@dataclass
class RepositoryInventory:
    languages: list[str]
    test_frameworks: list[str]
    important_paths: dict[str, str]
    feature_tags: dict[str, list[str]]
    api_schemas: list[str]
    event_emitters: dict[str, list[str]]

    def to_dict(self) -> dict[str, object]:
        return {
            "languages": self.languages,
            "test_frameworks": self.test_frameworks,
            "important_paths": self.important_paths,
            "feature_tags": self.feature_tags,
            "api_schemas": self.api_schemas,
            "event_emitters": self.event_emitters,
        }

    def components_for_feature(self, feature_id: str, slug: str) -> list[str]:
        matches = set()
        lookup_keys = {feature_id.upper(), slug.upper(), slug.replace("-", "_").upper()}
        for key, paths in self.feature_tags.items():
            if key.upper() in lookup_keys:
                matches.update(paths)
        return sorted(matches)


def inventory_repository(context: RexContext) -> RepositoryInventory:
    root = context.root
    languages: set[str] = set()
    feature_tags: dict[str, list[str]] = {}
    event_emitters: dict[str, list[str]] = {}

    feature_pattern = re.compile(r"FC-[A-Za-z0-9_-]+")
    event_pattern = re.compile(
        r"""(?:
        emit\(\s*["'](?P<event1>[a-zA-Z0-9_.:-]+)["']
        |
        ["'](?P<event2>[a-zA-Z0-9_.:-]+)["']\s*\)
        )""",
        re.VERBOSE,
    )

    search_extensions = {".py", ".js", ".ts", ".tsx", ".jsx", ".md"}

    for path in root.rglob("*"):
        if path.is_dir():
            continue
        suffix = path.suffix.lower()
        language = EXTENSION_LANG_MAP.get(suffix)
        if language:
            languages.add(language)
        if suffix not in search_extensions:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        rel_path = context.relative(path)
        for match in feature_pattern.findall(text):
            feature_tags.setdefault(match.upper(), []).append(rel_path)
        for event_match in event_pattern.finditer(text):
            event_name = event_match.group("event1") or event_match.group("event2")
            if not event_name:
                continue
            event_emitters.setdefault(event_name, []).append(rel_path)

    test_frameworks: list[str] = []
    if (root / "pytest.ini").exists() or (root / "pyproject.toml").exists():
        test_frameworks.append("pytest")
    if (root / "playwright.config.ts").exists() or (
        root / "playwright.config.js"
    ).exists():
        test_frameworks.append("playwright")
    if (root / "package.json").exists():
        try:
            pkg = json.loads((root / "package.json").read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pkg = {}
        scripts = ",".join(pkg.get("scripts", {}).keys())
        deps = ",".join(pkg.get("dependencies", {}).keys())
        dev_deps = ",".join(pkg.get("devDependencies", {}).keys())
        combined = f"{scripts},{deps},{dev_deps}".lower()
        if "jest" in combined and "jest" not in test_frameworks:
            test_frameworks.append("jest")

    api_schemas: list[str] = []
    for candidate in root.rglob("*.yaml"):
        name = candidate.name.lower()
        if "openapi" in name or "swagger" in name:
            api_schemas.append(context.relative(candidate))
    for candidate in root.rglob("*.graphql"):
        api_schemas.append(context.relative(candidate))

    important_paths = {
        "tests_dir": context.relative(root / "tests"),
        "src_dir": context.relative(root / "src"),
        "documents_dir": context.relative(root / "documents"),
    }

    return RepositoryInventory(
        languages=sorted(languages),
        test_frameworks=sorted(test_frameworks),
        important_paths=important_paths,
        feature_tags={key: sorted(set(paths)) for key, paths in feature_tags.items()},
        api_schemas=sorted(api_schemas),
        event_emitters={
            key: sorted(set(paths)) for key, paths in event_emitters.items()
        },
    )


# ---------------------------------------------------------------------------
# Scenario synthesis
# ---------------------------------------------------------------------------


@dataclass
class Scenario:
    id: str
    kind: str
    summary: str
    preconditions: list[str]
    steps: list[str]
    assertions: list[str]
    observables: list[str]
    assumptions: list[str]
    test_types: list[str]
    components: list[str]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class Capability:
    id: str
    source_ac: str
    statement: str
    preconditions: list[str]
    triggers: list[str]
    observables: list[str]
    negative_space: list[str]
    measurement_strategy: list[str]
    test_types: list[str]
    edge_cases: list[str]
    invariants: list[str]
    scenarios: list[Scenario]

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["scenarios"] = [scenario.to_dict() for scenario in self.scenarios]
        return data


@dataclass
class TestSpecGraph:
    feature_card_id: str
    capabilities: list[Capability]

    def to_dict(self) -> dict[str, object]:
        return {
            "feature_card_id": self.feature_card_id,
            "capabilities": [capability.to_dict() for capability in self.capabilities],
        }


def _split_sentences(text: str) -> list[str]:
    raw = re.split(r"(?<=[.!?])\s+", text)
    return [segment.strip() for segment in raw if segment.strip()]


def _lower(text: str) -> str:
    return text.lower()


def _extract_phrases_by_keywords(
    sentences: Iterable[str], keywords: Sequence[str]
) -> list[str]:
    matches: list[str] = []
    for sentence in sentences:
        lowered = sentence.lower()
        if any(keyword in lowered for keyword in keywords):
            matches.append(sentence)
    return matches


def _fallback_assumption(
    ledger: AssumptionLedger, capability_id: str, subject: str
) -> str:
    return ledger.require(
        f"{capability_id}: clarify {subject}",
        rationale="Auto-generated because the Feature Card omits this detail.",
        risk="medium",
        default_choice="Document behaviour with product/QA follow-up.",
        ways_to_falsify=[
            "Product guidance contradicts this assumption",
            "Existing implementation documents explicit behaviour",
        ],
    )


def _derive_measurements(observables: list[str]) -> list[str]:
    measurements: list[str] = []
    for observable in observables:
        measurements.append(f"Validate observable: {observable}")
    if not measurements:
        measurements.append(
            "Establish measurable outcome for this capability (event, API response, or state change)."
        )
    return measurements


def _derive_invariants(constraints: dict[str, list[str]]) -> list[str]:
    invariants: list[str] = []
    for key, values in constraints.items():
        if "invariant" in key or "domain" in key:
            invariants.extend(values)
    return invariants


def _select_test_types(
    capability_statement: str,
    observables: list[str],
    repo_inventory: RepositoryInventory,
) -> list[str]:
    lowered = capability_statement.lower()
    types: list[str] = ["unit", "integration"]
    if any(term in lowered for term in ("api", "endpoint", "http", "response")):
        types.append("contract")
    if any(term in lowered for term in ("ui", "screen", "button", "page")):
        types.append("e2e")
    if any("event" in obs.lower() for obs in observables):
        types.append("contract")
    if "playwright" in repo_inventory.test_frameworks:
        types.append("e2e")
    return sorted(dict.fromkeys(types))


def _scenario_test_types(kind: str, base_types: Sequence[str]) -> list[str]:
    mapping = {
        "happy_path": ("integration", "e2e"),
        "boundary": ("unit", "property", "integration"),
        "negative": ("unit", "integration"),
        "idempotency": ("property", "integration"),
    }
    return sorted(dict.fromkeys(mapping.get(kind, base_types)))


def _derive_components(
    inventory: RepositoryInventory, feature_id: str, slug: str
) -> list[str]:
    components = inventory.components_for_feature(feature_id, slug)
    if components:
        return components
    default_paths = [inventory.important_paths.get("tests_dir", "tests")]
    return [path for path in default_paths if path]


def _build_scenarios_for_capability(
    *,
    feature: FeatureCardModel,
    capability: Capability,
    sentences: list[str],
    ledger: AssumptionLedger,
    inventory: RepositoryInventory,
) -> list[Scenario]:
    scenarios: list[Scenario] = []
    counter = 1

    def next_id() -> str:
        nonlocal counter
        ident = f"SC-{counter:02d}"
        counter += 1
        return ident

    components = _derive_components(inventory, feature.id, feature.slug)

    # Happy path scenario
    happy_id = next_id()
    happy_observables = capability.observables or capability.preconditions
    happy_assumptions: list[str] = []
    if not capability.triggers:
        happy_assumptions.append(
            _fallback_assumption(ledger, capability.id, "trigger condition")
        )
    if not happy_observables:
        happy_assumptions.append(
            _fallback_assumption(ledger, capability.id, "observable outcome")
        )
    happy_preconditions = capability.preconditions or [
        "System in default state derived from card summary."
    ]
    happy_steps = capability.triggers or [capability.statement]
    scenarios.append(
        Scenario(
            id=happy_id,
            kind="happy_path",
            summary=f"Validate {capability.statement}",
            preconditions=happy_preconditions,
            steps=happy_steps,
            assertions=capability.observables or [capability.statement],
            observables=capability.observables or happy_observables,
            assumptions=happy_assumptions,
            test_types=_scenario_test_types("happy_path", capability.test_types),
            components=components,
        )
    )

    # Boundary scenario heuristics
    boundary_sentences = _extract_phrases_by_keywords(
        sentences,
        [
            "edge",
            "boundary",
            "just before",
            "just after",
            "within",
            "until",
            "before",
            "after",
            "minimum",
            "maximum",
        ],
    )
    if boundary_sentences:
        boundary_id = next_id()
        scenarios.append(
            Scenario(
                id=boundary_id,
                kind="boundary",
                summary=f"Exercise boundary conditions for {capability.statement}",
                preconditions=happy_preconditions,
                steps=boundary_sentences,
                assertions=capability.observables or boundary_sentences,
                observables=capability.observables or boundary_sentences,
                assumptions=[],
                test_types=_scenario_test_types("boundary", capability.test_types),
                components=components,
            )
        )

    # Negative scenario heuristics
    negative_sentences = _extract_phrases_by_keywords(
        sentences,
        ["cannot", "must not", "should not", "reject", "error", "invalid"],
    )
    if negative_sentences:
        negative_id = next_id()
        scenarios.append(
            Scenario(
                id=negative_id,
                kind="negative",
                summary=f"Reject invalid flows for {capability.statement}",
                preconditions=happy_preconditions,
                steps=negative_sentences,
                assertions=negative_sentences,
                observables=capability.observables or negative_sentences,
                assumptions=[],
                test_types=_scenario_test_types("negative", capability.test_types),
                components=components,
            )
        )

    # Idempotency scenario encourages monotonic improvement
    idempotency_id = next_id()
    assumption = ledger.require(
        f"{capability.id}: repeated trigger should be idempotent",
        rationale="Guard against regressions when actions repeat.",
        risk="medium",
        default_choice="Repeated invocation preserves state.",
        ways_to_falsify=["Existing system intentionally allows repeated side-effects"],
    )
    scenarios.append(
        Scenario(
            id=idempotency_id,
            kind="idempotency",
            summary=f"Repeated execution of {capability.statement} is idempotent",
            preconditions=happy_preconditions,
            steps=[
                "Execute capability once to reach expected state",
                "Execute the same trigger again",
            ],
            assertions=["State and observable outputs remain unchanged"],
            observables=capability.observables or ["State unchanged"],
            assumptions=[assumption],
            test_types=_scenario_test_types("idempotency", capability.test_types),
            components=components,
        )
    )

    return scenarios


def build_test_spec_graph(
    feature: FeatureCardModel,
    *,
    ledger: AssumptionLedger,
    inventory: RepositoryInventory,
) -> TestSpecGraph:
    capabilities: list[Capability] = []
    constraints = feature.constraints
    invariants = _derive_invariants(constraints)

    if not feature.acceptance_criteria:
        # Create placeholder capability when the card is missing ACs
        placeholder = Capability(
            id="CAP-1",
            source_ac="AC-1",
            statement="Documented behaviour missing from Feature Card.",
            preconditions=[],
            triggers=[],
            observables=[],
            negative_space=[],
            measurement_strategy=["Define acceptance criteria for this Feature Card."],
            test_types=["unit", "integration"],
            edge_cases=[],
            invariants=invariants,
            scenarios=[],
        )
        assumption_id = ledger.require(
            "Feature Card lacks explicit acceptance criteria.",
            rationale="Specs cannot proceed without acceptance criteria; placeholder added.",
            risk="high",
            default_choice="Collaborate with product to capture criteria.",
            ways_to_falsify=["Product requirements document includes explicit ACs."],
        )
        placeholder.scenarios = [
            Scenario(
                id="SC-01",
                kind="gap",
                summary="Capture missing acceptance criteria before proceeding.",
                preconditions=[],
                steps=["Document acceptance criteria for this feature."],
                assertions=["Acceptance criteria recorded in Feature Card."],
                observables=["Updated Feature Card"],
                assumptions=[assumption_id],
                test_types=["process"],
                components=[],
            )
        ]
        capabilities.append(placeholder)
        return TestSpecGraph(feature.id, capabilities)

    for index, criterion in enumerate(feature.acceptance_criteria, start=1):
        cap_id = f"CAP-{index}"
        sentences = _split_sentences(criterion.text)
        preconditions = _extract_phrases_by_keywords(
            sentences, ["given ", "given that", "assume"]
        )
        triggers = _extract_phrases_by_keywords(
            sentences, ["when ", "once ", "after ", "before ", "trigger", "user", "api"]
        )
        observables = _extract_phrases_by_keywords(
            sentences,
            ["then", "should", "must", "ensure", "result", "observable", "state"],
        )
        negative_space = _extract_phrases_by_keywords(
            sentences, ["cannot", "must not", "should not", "never"]
        )
        measurement = _derive_measurements(observables)
        test_types = _select_test_types(criterion.text, observables, inventory)

        capability = Capability(
            id=cap_id,
            source_ac=criterion.id,
            statement=criterion.text,
            preconditions=preconditions,
            triggers=triggers,
            observables=observables,
            negative_space=negative_space,
            measurement_strategy=measurement,
            test_types=test_types,
            edge_cases=[],
            invariants=invariants,
            scenarios=[],
        )
        capability.scenarios = _build_scenarios_for_capability(
            feature=feature,
            capability=capability,
            sentences=sentences,
            ledger=ledger,
            inventory=inventory,
        )
        capabilities.append(capability)

    return TestSpecGraph(feature.id, capabilities)


# ---------------------------------------------------------------------------
# Artefact emission
# ---------------------------------------------------------------------------


@dataclass
class PlaybookArtifacts:
    feature: FeatureCardModel
    inventory: RepositoryInventory
    graph: TestSpecGraph
    ledger: AssumptionLedger
    traceability_rows: list[dict[str, str]]
    prompt_block: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": PLAYBOOK_ARTIFACT_SCHEMA_VERSION,
            "feature_card": self.feature.to_dict(),
            "repository_inventory": self.inventory.to_dict(),
            "test_spec_graph": self.graph.to_dict(),
            "assumptions": self.ledger.to_dict(),
            "traceability": self.traceability_rows,
            "prompt_block": self.prompt_block,
        }


def _build_traceability_rows(
    feature: FeatureCardModel, graph: TestSpecGraph
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for capability in graph.capabilities:
        for scenario in capability.scenarios:
            test_id = "-".join(
                part
                for part in (
                    feature.id.replace(" ", "").upper(),
                    capability.id,
                    scenario.id,
                )
                if part
            )
            rows.append(
                {
                    "test_id": test_id,
                    "feature_card": feature.id,
                    "capability": capability.id,
                    "scenario": scenario.id,
                    "observables": "; ".join(sorted(set(scenario.observables))),
                    "assumptions": "; ".join(sorted(scenario.assumptions)),
                    "test_type": "; ".join(sorted(set(scenario.test_types))),
                    "components": "; ".join(sorted(set(scenario.components))),
                }
            )
    return rows


def _render_prompt_block(artifacts: PlaybookArtifacts) -> str:
    feature = artifacts.feature
    graph = artifacts.graph
    ledger = artifacts.ledger
    inventory = artifacts.inventory

    lines = [
        f"Feature Card ID: {feature.id}",
        f"Title: {feature.title}",
        f"Priority: {feature.priority} | Risk: {feature.risk_level} | Owner: {feature.owner or 'unknown'}",
    ]
    if feature.dependencies:
        lines.append(f"Dependencies: {', '.join(feature.dependencies)}")
    if feature.summary:
        lines.append(f"Summary: {feature.summary}")
    lines.append("")
    lines.append("Acceptance Criteria → Capabilities:")
    for capability in graph.capabilities:
        lines.append(
            f"- {capability.id} ({capability.source_ac}): {capability.statement}"
        )
        if capability.preconditions:
            lines.append(f"  Preconditions: {', '.join(capability.preconditions)}")
        if capability.triggers:
            lines.append(f"  Triggers: {', '.join(capability.triggers)}")
        if capability.observables:
            lines.append(f"  Observables: {', '.join(capability.observables)}")
        lines.append(f"  Test types: {', '.join(capability.test_types)}")
        for scenario in capability.scenarios:
            lines.append(f"    • {scenario.id} [{scenario.kind}]: {scenario.summary}")
            if scenario.assumptions:
                lines.append(f"      Assumptions: {', '.join(scenario.assumptions)}")
    lines.append("")
    if ledger.assumptions:
        lines.append("Assumption Ledger:")
        for assumption in ledger.assumptions:
            lines.append(
                f"- {assumption.id} ({assumption.risk}): {assumption.text} "
                f"[default={assumption.default_choice}]"
            )
    if ledger.escalation_hints:
        lines.append("")
        lines.append("Escalation Hints:")
        for hint in ledger.escalation_hints:
            lines.append(f"- {hint}")
    lines.append("")
    lines.append("Repository Signals:")
    lines.append(f"- Languages: {', '.join(inventory.languages) or 'unknown'}")
    lines.append(
        f"- Test frameworks: {', '.join(inventory.test_frameworks) or 'unspecified'}"
    )
    mapped_components = inventory.components_for_feature(feature.id, feature.slug)
    if mapped_components:
        lines.append(f"- Existing feature tags: {', '.join(mapped_components)}")
    if inventory.api_schemas:
        lines.append(f"- API schemas: {', '.join(inventory.api_schemas)}")
    if inventory.event_emitters:
        sample = ", ".join(sorted(list(inventory.event_emitters.keys())[:5]))
        lines.append(f"- Known events: {sample}")
    return "\n".join(lines)


def build_playbook_artifacts(
    *,
    card: FeatureCard,
    context: RexContext,
) -> PlaybookArtifacts:
    feature = canonicalize_feature_card(card)
    inventory = inventory_repository(context)
    ledger = AssumptionLedger.load(context, feature)
    graph = build_test_spec_graph(feature, ledger=ledger, inventory=inventory)
    traceability_rows = _build_traceability_rows(feature, graph)
    artifacts = PlaybookArtifacts(
        feature=feature,
        inventory=inventory,
        graph=graph,
        ledger=ledger,
        traceability_rows=traceability_rows,
        prompt_block="",  # set below after ledger refresh
    )
    artifacts.prompt_block = _render_prompt_block(artifacts)

    # Persist artefacts
    codex_ci = ensure_dir(context.codex_ci_dir)
    playbook_json = codex_ci / f"playbook_{card.slug}.json"
    playbook_prompt = codex_ci / f"playbook_{card.slug}.prompt"
    traceability_csv = codex_ci / f"traceability_{card.slug}.csv"

    ledger.save()

    dump_json(
        playbook_json,
        artifacts.to_dict(),
        ensure_ascii=False,
        sort_keys=False,
    )
    playbook_prompt.write_text(artifacts.prompt_block + "\n", encoding="utf-8")

    if traceability_rows:
        with traceability_csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "test_id",
                    "feature_card",
                    "capability",
                    "scenario",
                    "observables",
                    "assumptions",
                    "test_type",
                    "components",
                ],
            )
            writer.writeheader()
            for row in traceability_rows:
                writer.writerow(row)
    else:
        traceability_csv.write_text(
            "test_id,feature_card,capability,scenario,observables,assumptions,test_type,components\n",
            encoding="utf-8",
        )

    return artifacts


# Public exports
__all__ = [
    "AcceptanceCriterion",
    "Assumption",
    "AssumptionLedger",
    "Capability",
    "FeatureCardModel",
    "ObservabilityHints",
    "PlaybookArtifacts",
    "RepositoryInventory",
    "Scenario",
    "TestSpecGraph",
    "build_playbook_artifacts",
    "build_test_spec_graph",
    "canonicalize_feature_card",
    "inventory_repository",
]
