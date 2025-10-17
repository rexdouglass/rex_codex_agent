from __future__ import annotations

import subprocess
from pathlib import Path

from rex_codex.utils import RexContext, create_audit_snapshot


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "audit@test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Audit Bot"], cwd=path, check=True)


def _commit(path: Path, message: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", message], cwd=path, check=True)


def _mark_as_agent_repo(path: Path) -> None:
    pkg = path / "rex_codex"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    scripts = path / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "selftest_loop.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    bin_dir = path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    (bin_dir / "fake-codex").write_text("#!/bin/bash\n", encoding="utf-8")


def test_create_audit_snapshot_respects_disable_auto_commit(
    monkeypatch, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    (repo / "README.md").write_text("baseline\n", encoding="utf-8")
    _commit(repo, "init")

    monkeypatch.setenv("ROOT", str(repo))
    monkeypatch.setenv("REX_DISABLE_AUTO_COMMIT", "1")
    context = RexContext.discover()

    count_before = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    create_audit_snapshot(context, auto_commit=True)

    count_after = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    assert count_after == count_before


def test_create_audit_snapshot_skips_push_when_env_set(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    (repo / "README.md").write_text("baseline\n", encoding="utf-8")
    _commit(repo, "init")

    monkeypatch.setenv("ROOT", str(repo))
    monkeypatch.delenv("REX_DISABLE_AUTO_COMMIT", raising=False)
    monkeypatch.setenv("REX_DISABLE_AUTO_PUSH", "1")
    context = RexContext.discover()

    count_before = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    create_audit_snapshot(context, auto_commit=True)
    captured = capsys.readouterr()

    count_after = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    assert int(count_after) == int(count_before) + 1
    assert "Skipping git push (REX_DISABLE_AUTO_PUSH is set)." in captured.out


def test_agent_repo_defaults_to_testing_mode(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "agent"
    repo.mkdir()
    _init_git_repo(repo)
    (repo / "README.md").write_text("baseline\n", encoding="utf-8")
    _commit(repo, "init")
    _mark_as_agent_repo(repo)

    monkeypatch.setenv("ROOT", str(repo))
    monkeypatch.delenv("REX_DISABLE_AUTO_COMMIT", raising=False)
    monkeypatch.delenv("REX_DISABLE_AUTO_PUSH", raising=False)
    monkeypatch.delenv("REX_AGENT_FORCE_BUILD", raising=False)
    context = RexContext.discover()

    count_before = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    create_audit_snapshot(context, auto_commit=True)

    count_after = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    assert count_after == count_before
