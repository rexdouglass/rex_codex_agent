from __future__ import annotations

from pathlib import Path

import pytest

from rex_codex.scope_project import llm
from rex_codex.scope_project.utils import RexContext


class DummyProvider(llm.LLMProvider):
    def run_json(self, *, label: str, prompt: str, slug: str, verbose: bool = True) -> dict[str, object]:
        return {"label": label, "prompt": prompt, "slug": slug, "provider": "dummy"}


@pytest.fixture()
def context(tmp_path: Path) -> RexContext:
    root = tmp_path
    codex_ci = root / ".codex_ci"
    monitor_logs = root / ".agent" / "logs"
    codex_ci.mkdir(parents=True, exist_ok=True)
    monitor_logs.mkdir(parents=True, exist_ok=True)
    return RexContext(
        root=root,
        codex_ci_dir=codex_ci,
        monitor_log_dir=monitor_logs,
        rex_agent_file=root / "rex-agent.json",
        venv_dir=root / ".venv",
    )


def test_custom_provider(monkeypatch: pytest.MonkeyPatch, context: RexContext) -> None:
    llm.register_llm_provider("dummy", lambda config: DummyProvider(config))
    monkeypatch.setenv("REX_LLM_PROVIDER", "dummy")

    provider = llm.resolve_llm_provider(
        context=context,
        codex_bin="echo",
        codex_flags="",
        codex_model="",
    )
    result = provider.run_json(label="component", prompt="{}", slug="fc-123", verbose=False)
    assert result["provider"] == "dummy"
    assert result["slug"] == "fc-123"

    llm.reset_llm_providers()


def test_unknown_provider(context: RexContext, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REX_LLM_PROVIDER", "unknown-provider")
    with pytest.raises(llm.LLMInvocationError):
        llm.resolve_llm_provider(
            context=context,
            codex_bin="echo",
            codex_flags="",
            codex_model="",
        )
