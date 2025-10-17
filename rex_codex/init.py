"""Project initialisation for rex-codex."""

from __future__ import annotations

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
    run,
    which,
)


def _copy_if_missing(src: Path, dest: Path) -> None:
    if dest.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def run_init(
    *, context: RexContext | None = None, perform_self_update: bool = True
) -> None:
    context = context or RexContext.discover()
    if perform_self_update:
        self_update()

    print("[*] Bootstrapping Python environment…")
    ensure_python(context)

    requirements_template = AGENT_SRC / "templates" / "requirements-dev.txt"
    ensure_requirements_installed(context, requirements_template, quiet=False)

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
        _copy_if_missing(
            card_readme, root / "documents" / "feature_cards" / "README.md"
        )

    enforcement_dir = template_root / "tests" / "enforcement"
    if enforcement_dir.exists():
        for item in enforcement_dir.glob("**/*"):
            if item.is_file():
                rel = item.relative_to(enforcement_dir)
                dest = root / "tests" / "enforcement" / rel
                _copy_if_missing(item, dest)

    monitor_dir = root / "monitor"
    package_json = monitor_dir / "package.json"
    if package_json.exists():
        node_modules = monitor_dir / "node_modules"
        if node_modules.exists():
            print("[*] Monitor dependencies already installed (monitor/node_modules present).")
        else:
            npm = which("npm")
            if npm is None:
                print("[!] Skipping monitor npm install: npm not found on PATH.")
            else:
                print("[*] Installing monitor dependencies (npm install)…")
                run(
                    [npm, "install", "--no-fund", "--no-audit"],
                    cwd=monitor_dir,
                    check=True,
                )
                print("[✓] Monitor dependencies installed.")

    agent_state = {
        "stages": [
            "sanity",
            "deps",
            "specs",
            "unit",
            "style",
        ],
        "llm": {"bin": "npx --yes @openai/codex", "flags": "--yolo", "model": ""},
        "feature": {
            "active_card": None,
            "active_slug": None,
            "updated_at": None,
        },
        "version": __version__,
    }
    dump_json(context.rex_agent_file, agent_state)
    print("[✓] Project initialized. Try: ./rex-codex loop")
