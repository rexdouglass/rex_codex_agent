from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(
    os.environ.get("CI") == "true", reason="uses local working tree via PYTHONPATH"
)
def test_init_copies_requirements(monkeypatch, tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{repo_root / 'src'}:{env.get('PYTHONPATH', '')}"
    env["REX_SRC"] = str(repo_root)
    subprocess.run(
        ["python3", "-m", "rex_codex", "init", "--no-self-update"],
        cwd=tmp_path,
        check=True,
        env=env,
    )
    expected = (repo_root / "requirements.txt").read_text(encoding="utf-8")
    actual = (tmp_path / "requirements.txt").read_text(encoding="utf-8")
    assert actual == expected
