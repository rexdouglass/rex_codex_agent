from __future__ import annotations

import os
import subprocess
from pathlib import Path


def test_packaging_install_prunes_dev_artifacts(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env.update(
        {
            "REPO_URL": str(repo_root),
            "REX_AGENT_SKIP_INIT": "1",
            "REX_AGENT_SKIP_DOCTOR": "1",
        }
    )
    subprocess.run(
        ["bash", str(repo_root / "packaging" / "install.sh")],
        cwd=tmp_path,
        check=True,
        env=env,
    )

    src_dir = tmp_path / ".rex_agent" / "src"
    assert src_dir.exists(), "Installer should clone agent sources into .rex_agent/src"
    assert not (src_dir / "for_external_GPT5_pro_audit").exists()
    assert not (src_dir / ".codex_ci").exists()
    assert not (src_dir / ".agent" / "logs").exists()

    # Run installer again to exercise reinstall path (should back up + clean).
    subprocess.run(
        ["bash", str(repo_root / "packaging" / "install.sh")],
        cwd=tmp_path,
        check=True,
        env=env,
    )
    backup_candidates = list(tmp_path.glob(".rex_agent.bak.*"))
    assert not backup_candidates, "Backup directory should be cleaned after reinstall"
    assert not (src_dir / "for_external_GPT5_pro_audit").exists()
