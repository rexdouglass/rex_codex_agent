"""Project initialisation for rex-codex."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from . import __version__
from .config import AGENT_SRC
from .self_update import self_update
from .utils import (
    RexContext,
    dump_json,
    ensure_dir,
    ensure_python,
    ensure_requirements_installed,
    repo_root,
)


def _copy_if_missing(src: Path, dest: Path) -> None:
    if dest.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def run_init(*, context: RexContext | None = None, perform_self_update: bool = True) -> None:
    context = context or RexContext.discover()
    if perform_self_update:
        self_update()

    print("[*] Bootstrapping Python environment…")
    ensure_python(context)

    requirements_template = AGENT_SRC / "templates" / "requirements-dev.txt"
    ensure_requirements_installed(context, requirements_template)

    root = context.root
    ensure_dir(root / "tests" / "enforcement")
    ensure_dir(root / "documents" / "feature_cards")

    template_root = AGENT_SRC / "templates"
    copies = {
        "AGENTS.md": root / "AGENTS.md",
        "pytest.ini": root / "pytest.ini",
        "pyproject.toml": root / "pyproject.toml",
        "mypy.ini": root / "mypy.ini",
        "conftest.py": root / "conftest.py",
        ".flake8": root / ".flake8",
    }
    for rel, dest in copies.items():
        src = template_root / rel
        if src.exists():
            _copy_if_missing(src, dest)

    card_readme = template_root / "documents" / "feature_cards" / "README.md"
    if card_readme.exists():
        _copy_if_missing(card_readme, root / "documents" / "feature_cards" / "README.md")

    enforcement_dir = template_root / "tests" / "enforcement"
    if enforcement_dir.exists():
        for item in enforcement_dir.glob("**/*"):
            if item.is_file():
                rel = item.relative_to(enforcement_dir)
                dest = root / "tests" / "enforcement" / rel
                _copy_if_missing(item, dest)

    agent_state = {
        "stages": [
            "sanity",
            "deps",
            "specs",
            "unit",
            "style",
        ],
        "llm": {"bin": "npx --yes @openai/codex", "flags": "--yolo", "model": ""},
        "update_on_run": True,
        "feature": {
            "active_card": None,
            "active_slug": None,
            "updated_at": None,
        },
        "version": __version__,
    }
    dump_json(context.rex_agent_file, agent_state)
    print("[✓] Project initialized. Try: ./rex-codex loop")

