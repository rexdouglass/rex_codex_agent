"""Helpers for managing the per-project rex-codex runtime."""

from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, MutableMapping

from rex_codex.scope_project import utils as runtime_utils

AGENT_DIR_NAME = ".agent"
LOCK_FILENAME = "agent.lock"
MANIFEST_FILENAME = "manifest.json"
CONFIG_FILENAME = "config.yaml"


def _default_agent_dir(root: Path) -> Path:
    return root / AGENT_DIR_NAME


def load_lockfile(
    root: Path | None = None, *, path: Path | None = None
) -> MutableMapping[str, Any]:
    """Load the agent.lock file from the project runtime directory."""
    root = root or runtime_utils.repo_root()
    lock_path = path or (_default_agent_dir(root) / LOCK_FILENAME)
    if not lock_path.exists():
        return {}
    return json.loads(lock_path.read_text(encoding="utf-8"))


def write_lockfile(
    data: Mapping[str, Any],
    root: Path | None = None,
    *,
    path: Path | None = None,
) -> Path:
    """Persist the lockfile JSON to disk with a trailing newline."""
    root = root or runtime_utils.repo_root()
    agent_dir = _default_agent_dir(root)
    runtime_utils.ensure_dir(agent_dir)
    lock_path = path or (agent_dir / LOCK_FILENAME)
    payload = json.dumps(data, indent=2, sort_keys=True)
    lock_path.write_text(f"{payload}\n", encoding="utf-8")
    return lock_path


@dataclass
class RuntimeBootstrapper:
    """Coordinate creation and cleanup of the per-project runtime surface."""

    root: Path
    context: runtime_utils.RexContext

    @classmethod
    def from_root(cls, root: Path | None = None) -> "RuntimeBootstrapper":
        root = root or runtime_utils.repo_root()
        context = runtime_utils.RexContext(
            root=root,
            codex_ci_dir=runtime_utils.ensure_dir(root / ".codex_ci"),
            monitor_log_dir=runtime_utils.ensure_dir(root / ".agent" / "logs"),
            rex_agent_file=root / "rex-agent.json",
            venv_dir=root / ".venv",
        )
        return cls(root=root, context=context)

    @property
    def agent_dir(self) -> Path:
        return _default_agent_dir(self.root)

    @property
    def runtime_dir(self) -> Path:
        return runtime_utils.ensure_dir(self.agent_dir / "runtime")

    @property
    def lock_path(self) -> Path:
        return self.agent_dir / LOCK_FILENAME

    @property
    def manifest_path(self) -> Path:
        return self.agent_dir / MANIFEST_FILENAME

    def ensure_structure(self) -> None:
        """Create runtime directories if they do not exist."""
        runtime_utils.ensure_dir(self.agent_dir)
        runtime_utils.ensure_dir(self.agent_dir / "logs")
        runtime_utils.ensure_dir(self.agent_dir / "hooks")
        runtime_utils.ensure_dir(self.runtime_dir)

    def ensure_lock(self, *, version: str) -> Mapping[str, Any]:
        """Create or update a minimal lockfile with the supplied agent version."""
        lock_data = load_lockfile(self.root)
        lock_data["agent"] = {"name": "rex-codex", "version": version}
        lock_data.setdefault("python", sys.version.split()[0])
        write_lockfile(lock_data, self.root)
        return lock_data

    def ensure_manifest(self) -> None:
        """Initialise the manifest file if it does not exist."""
        if self.manifest_path.exists():
            return
        payload = {"created": [], "modified": [], "version": 1}
        runtime_utils.dump_json(self.manifest_path, payload)

    def bootstrap(self, *, version: str) -> None:
        """Ensure all per-project runtime surfaces exist."""
        self.ensure_structure()
        self.ensure_manifest()
        self.ensure_lock(version=version)

    def destroy(self) -> None:
        """Remove generated artefacts described in the manifest."""
        manifest = runtime_utils.load_json(self.manifest_path)
        trash_root = runtime_utils.ensure_dir(self.agent_dir / ".trash")
        session = runtime_utils.ensure_dir(
            trash_root / datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        )
        for entry in manifest.get("created", []):
            path = self.root / entry.get("path", "")
            if path.is_file() or path.is_dir():
                target = session / path.name
                shutil.move(str(path), str(target))
        manifest["created"] = []
        runtime_utils.dump_json(self.manifest_path, manifest)
