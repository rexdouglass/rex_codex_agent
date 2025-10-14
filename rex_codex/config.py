"""Centralised configuration defaults for rex-codex."""

from __future__ import annotations

from .utils import agent_src, repo_root


REPO_ROOT = repo_root()
AGENT_SRC = agent_src(REPO_ROOT)
CODENAME = "rex-codex"
DEFAULT_GENERATOR_MAX_FILES = 6
DEFAULT_GENERATOR_MAX_LINES = 300
DEFAULT_DISCRIMINATOR_MAX_FILES = 6
DEFAULT_DISCRIMINATOR_MAX_LINES = 300
DEFAULT_COVERAGE_MIN = 80
DEFAULT_RUNTIME_ALLOWLIST = ("src",)
DEFAULT_PROTECTED_PATHS = [
    "tests",
    "documents",
    "pytest.ini",
    "pyproject.toml",
    "mypy.ini",
    ".flake8",
    ".ruff.toml",
    "ruff.toml",
    "conftest.py",
    "tox.ini",
    "setup.cfg",
    ".coveragerc",
    ".pre-commit-config.yaml",
    "requirements.txt",
    "requirements-dev.txt",
    "requirements",
    "constraints.txt",
    "constraints-*.txt",
    "constraints",
    "Pipfile",
    "Pipfile.lock",
    "poetry.lock",
    "Dockerfile",
    "Dockerfile.*",
    ".github",
    ".gitlab-ci.yml",
    ".gitlab",
    "Makefile",
    "noxfile.py",
]
