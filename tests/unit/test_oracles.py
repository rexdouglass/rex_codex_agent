from __future__ import annotations

import os
from pathlib import Path
import shlex
import sys

import pytest

from rex_codex.scope_project import oracles
from rex_codex.scope_project.oracles import OracleDefinition, OracleManifest, run_oracles
from rex_codex.scope_project.utils import RexContext, ensure_dir


@pytest.fixture()
def context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RexContext:
    root = tmp_path
    codex_ci = ensure_dir(root / ".codex_ci")
    monitor_logs = ensure_dir(root / ".agent" / "logs")
    ctx = RexContext(
        root=root,
        codex_ci_dir=codex_ci,
        monitor_log_dir=monitor_logs,
        rex_agent_file=root / "rex-agent.json",
        venv_dir=root / ".venv",
    )
    # keep event streams local to the tmpdir
    monkeypatch.setenv("REX_EVENTS_FILE", str(codex_ci / "events.jsonl"))
    monkeypatch.setenv("REX_MONITOR_EVENTS_FILE", str(monitor_logs / "events.jsonl"))
    return ctx


def _write_manifest(path: Path, payload: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    return path


def test_load_manifest_roundtrip(tmp_path: Path, context: RexContext) -> None:
    manifest_path = _write_manifest(
        tmp_path / "documents" / "oracles.yaml",
        """
schema_version: oracle-manifest.v1
default_fail_fast: false
oracles:
  - name: smoke
    kind: property
    command: python -c "print('ok')"
    description: Simple smoke oracle.
""",
    )
    manifest = oracles.load_manifest(context, manifest_path)
    assert manifest is not None
    assert manifest.schema_version == "oracle-manifest.v1"
    assert manifest.default_fail_fast is False
    assert len(manifest.oracles) == 1
    assert manifest.oracles[0].name == "smoke"


def test_run_oracle_skips_missing_requirements(context: RexContext) -> None:
    oracle = OracleDefinition(
        name="needs-path",
        kind="bdd",
        command="behave --stop",
        required_paths=["features"],
    )
    manifest = OracleManifest(schema_version="oracle-manifest.v1", oracles=[oracle])
    code, results = run_oracles(manifest, context=context, verbose=False)
    assert code == 0
    assert len(results) == 1
    assert results[0].status == "skipped"
    assert "required path" in (results[0].reason or "")


def test_run_oracle_executes_command(context: RexContext) -> None:
    script = context.root / "oracle_success.py"
    script.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}"
    oracle = OracleDefinition(
        name="echo",
        kind="property",
        command=command,
    )
    manifest = OracleManifest(schema_version="oracle-manifest.v1", oracles=[oracle])
    code, results = run_oracles(manifest, context=context, verbose=False)
    assert code == 0
    assert len(results) == 1
    assert results[0].passed


def test_run_oracle_failure_propagates_exit_code(context: RexContext) -> None:
    script = context.root / "oracle_failure.py"
    script.write_text("import sys\nsys.exit(7)\n", encoding="utf-8")
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}"
    oracle = OracleDefinition(
        name="failure",
        kind="mutation",
        command=command,
    )
    manifest = OracleManifest(schema_version="oracle-manifest.v1", oracles=[oracle])
    code, results = run_oracles(manifest, context=context, verbose=False)
    assert code == 7
    assert len(results) == 1
    assert results[0].failed
    assert results[0].returncode == 7
