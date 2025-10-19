from __future__ import annotations

from typing import Sequence

from rex_codex.scope_project import doctor


def test_gather_diagnostics(monkeypatch):
    tool_paths = {
        "python3": "/usr/bin/python3",
        "bash": "/bin/bash",
        "node": "/usr/bin/node",
        "npx": "/usr/bin/npx",
        "docker": "/usr/bin/docker",
        "echo": "/bin/echo",
    }

    def fake_which(name: str) -> str | None:
        return tool_paths.get(name)

    versions = {
        ("python3", "--version"): "Python 3.11.5",
        ("bash", "--version"): "GNU bash, version 5.2.0(1)-release",
        ("node", "--version"): "v18.12.0",
        ("npx", "--version"): "10.2.0",
        ("docker", "--version"): "Docker version 25.0.0, build test",
    }

    def fake_run(command: Sequence[str]) -> str | None:
        key = (command[0], command[1]) if len(command) > 1 else (command[0],)
        return versions.get(key)

    monkeypatch.setattr(doctor, "which", fake_which)
    monkeypatch.setattr(doctor, "_run_version_command", fake_run)
    monkeypatch.setenv("CODEX_BIN", "echo codex")

    results = doctor.gather_diagnostics()
    statuses = {check.name: check.status for check in results}
    assert statuses["python3"] == "ok"
    assert statuses["bash"] == "ok"
    assert statuses["node"] == "ok"
    assert statuses["npx"] == "ok"
    assert statuses["codex"] == "ok"


def test_check_tool_missing(monkeypatch):
    monkeypatch.setattr(doctor, "which", lambda name: None)
    result = doctor._check_tool(name="python3", command=["python3", "--version"])
    assert result.status == "error"
    assert "not found" in result.message
