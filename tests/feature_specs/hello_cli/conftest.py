from __future__ import annotations

import importlib.util
import os
import runpy
import sys
from pathlib import Path

import pytest


def _project_root() -> Path:
    env_root = os.environ.get("ROOT")
    if env_root:
        candidate = Path(env_root).resolve()
        if (candidate / "src").exists():
            return candidate
    here = Path(__file__).resolve()
    for parent in (here,) + tuple(here.parents):
        if (parent / "src").exists():
            return parent
    return Path.cwd().resolve()


def _import_from_src(pkg: str):
    root = _project_root()
    module_file = root / "src" / pkg / "__init__.py"
    if not module_file.exists():
        raise FileNotFoundError(f"{module_file} not found")
    spec = importlib.util.spec_from_file_location(pkg, str(module_file))
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader  # type: ignore[truthy-bool]
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


@pytest.fixture(scope="session")
def hello():
    return _import_from_src("hello")


@pytest.fixture
def run_app():
    root = _project_root()

    def _run(*args: str) -> None:
        argv = ["hello", *args]
        original_argv = sys.argv[:]
        original_path = list(sys.path)
        source_path = root / "src"
        if str(source_path) not in sys.path:
            sys.path.insert(0, str(source_path))
        sys.argv = argv
        try:
            runpy.run_module("hello", run_name="__main__")
        except SystemExit as exc:  # mimic CLI invocation
            if exc.code not in (0, None):
                raise
        finally:
            sys.argv = original_argv
            sys.path[:] = original_path

    return _run
