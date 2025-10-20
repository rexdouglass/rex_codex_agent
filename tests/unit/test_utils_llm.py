from __future__ import annotations

import json

import pytest

from rex_codex.scope_project import utils


def _mk_context(tmp_path):
    root = tmp_path
    codex_ci = root / ".codex_ci"
    codex_ci.mkdir()
    monitor_logs = root / ".agent" / "logs"
    monitor_logs.mkdir(parents=True)
    rex_agent_file = root / "rex-agent.json"
    venv_dir = root / ".venv"
    return utils.RexContext(
        root=root,
        codex_ci_dir=codex_ci,
        monitor_log_dir=monitor_logs,
        rex_agent_file=rex_agent_file,
        venv_dir=venv_dir,
    )


def test_build_llm_settings_prefers_explicit_model(monkeypatch):
    monkeypatch.setenv("CODEX_TEMPERATURE", "0.3")
    settings = utils.build_llm_settings(
        codex_bin="npx --yes @openai/codex",
        codex_flags="",
        codex_model="gpt-5-codex",
    )
    assert settings["model"] == "gpt-5-codex"
    assert settings["model_source"] == "env:MODEL"
    assert settings["parameters"]["temperature"] == pytest.approx(0.3)
    assert settings["parameter_sources"]["temperature"] == "env:CODEX_TEMPERATURE"


def test_build_llm_settings_extracts_model_from_flags():
    settings = utils.build_llm_settings(
        codex_bin="npx --yes @openai/codex",
        codex_flags="--model o3 --config sampling.temperature=0.1",
        codex_model="",
    )
    assert settings["model"] == "o3"
    assert settings["model_source"] == "flag:--model"
    overrides = {entry["key"]: entry for entry in settings["config_overrides"]}
    assert overrides["sampling.temperature"]["parsed"] == pytest.approx(0.1)


def test_update_llm_settings_persists_snapshot(tmp_path):
    context = _mk_context(tmp_path)
    utils.update_llm_settings(
        context,
        codex_bin="npx --yes @openai/codex",
        codex_flags="-c sandbox_permissions=[\"disk-full-read-access\"]",
        codex_model="gpt-5-codex",
    )
    payload = json.loads(context.rex_agent_file.read_text())
    llm = payload["llm"]
    assert llm["model"] == "gpt-5-codex"
    assert llm["model_explicit"] is True
    assert isinstance(llm["updated_at"], str) and llm["updated_at"].endswith("Z")
    overrides = llm["config_overrides"]
    assert overrides and overrides[0]["key"] == "sandbox_permissions"
