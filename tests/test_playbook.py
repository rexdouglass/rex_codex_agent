import json
from pathlib import Path

from rex_codex.cards import FeatureCard
from rex_codex.playbook import build_playbook_artifacts, canonicalize_feature_card
from rex_codex.utils import RexContext, ensure_dir


def _make_context(tmp_path: Path) -> RexContext:
    codex_ci = ensure_dir(tmp_path / ".codex_ci")
    monitor = ensure_dir(tmp_path / ".agent" / "logs")
    rex_agent_file = tmp_path / "rex-agent.json"
    if not rex_agent_file.exists():
        rex_agent_file.write_text("{}", encoding="utf-8")
    return RexContext(
        root=tmp_path,
        codex_ci_dir=codex_ci,
        monitor_log_dir=monitor,
        rex_agent_file=rex_agent_file,
        venv_dir=tmp_path / ".venv",
    )


def _write_feature_card(path: Path) -> None:
    path.write_text(
        """status: proposed
id: FC-9999
epic: Payments
risk_level: medium
priority: P1
owner: qa-team
version: 2
dependencies: FC-0001, FC-0002

# Pause recurring transfer

## Summary
- Allow customers to pause and resume transfers without canceling the series.

## Acceptance Criteria
- AC-1: When the user pauses a transfer, no executions occur until it is resumed.
- AC-2: Resuming a paused transfer restores the schedule cadence.

## Non-Goals
- Editing the transfer amount.

## Open Questions
- Should paused transfers notify customers?

## Constraints
domain_invariants:
  - Transfers cannot schedule in the past
  - Currency is immutable after creation

## Observability
logs:
  - event: transfer.paused
  - event: transfer.resumed
metrics:
  - counter: transfers.paused_total

## Notes
Existing scheduler table `schedules_v2`.
""",
        encoding="utf-8",
    )


def test_canonicalize_feature_card(tmp_path: Path) -> None:
    cards_dir = ensure_dir(tmp_path / "documents" / "feature_cards")
    card_path = cards_dir / "pause-transfer.md"
    _write_feature_card(card_path)
    feature_card = FeatureCard(path=card_path, slug="pause-transfer", status="proposed")

    model = canonicalize_feature_card(feature_card)

    assert model.id == "FC-9999"
    assert model.priority == "P1"
    assert len(model.acceptance_criteria) == 2
    assert model.acceptance_criteria[0].id == "AC-1"
    assert (
        "Transfers cannot schedule in the past"
        in model.constraints["domain_invariants"]
    )
    assert "transfer.paused" in " ".join(model.observability.logs)


def test_build_playbook_artifacts_writes_outputs(tmp_path: Path) -> None:
    # Arrange repository skeleton with simple runtime/test files for inventory discovery.
    ensure_dir(tmp_path / "src").joinpath("scheduler.py").write_text(
        "def pause():\n    pass\n", encoding="utf-8"
    )
    tests_dir = ensure_dir(tmp_path / "tests" / "feature_specs" / "pause-transfer")
    tests_dir.joinpath("test_placeholder.py").write_text(
        "# FC-9999\n\ndef test_placeholder():\n    assert True\n", encoding="utf-8"
    )

    cards_dir = ensure_dir(tmp_path / "documents" / "feature_cards")
    card_path = cards_dir / "pause-transfer.md"
    _write_feature_card(card_path)
    feature_card = FeatureCard(path=card_path, slug="pause-transfer", status="proposed")

    context = _make_context(tmp_path)

    artifacts = build_playbook_artifacts(card=feature_card, context=context)

    json_path = context.codex_ci_dir / "playbook_pause-transfer.json"
    prompt_path = context.codex_ci_dir / "playbook_pause-transfer.prompt"
    csv_path = context.codex_ci_dir / "traceability_pause-transfer.csv"
    ledger_path = tmp_path / "documents" / "assumption_ledgers" / "pause-transfer.json"

    assert json_path.exists()
    assert prompt_path.exists()
    assert csv_path.exists()
    assert ledger_path.exists()

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["feature_card"]["id"] == "FC-9999"
    assert payload["repository_inventory"]["languages"] == ["python"]
    assert payload["test_spec_graph"]["capabilities"], "capabilities missing"

    csv_content = csv_path.read_text(encoding="utf-8").splitlines()
    assert (
        csv_content[0]
        == "test_id,feature_card,capability,scenario,observables,assumptions,test_type,components"
    )
    assert any(row.startswith("FC-9999-CAP-1-SC-") for row in csv_content[1:])

    assert artifacts.prompt_block
