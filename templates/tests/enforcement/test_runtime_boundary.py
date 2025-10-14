from __future__ import annotations

import ast
from pathlib import Path

import pytest

RUNTIME_ROOTS = ("src", "app")


def iter_runtime_modules() -> list[Path]:
    modules: list[Path] = []
    for root in RUNTIME_ROOTS:
        base = Path(root)
        if not base.is_dir():
            continue
        modules.extend(
            path
            for path in base.rglob("*.py")
            if "tests" not in path.parts and not path.name.startswith("_")
        )
    return modules


@pytest.mark.unit
def test_runtime_does_not_import_tests_namespace() -> None:
    modules = iter_runtime_modules()
    if not modules:
        pytest.skip("No runtime modules to audit yet")

    offenders: list[str] = []
    for module_path in modules:
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("tests"):
                        offenders.append(f"{module_path}:{node.lineno} imports {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module.startswith("tests"):
                    offenders.append(f"{module_path}:{node.lineno} imports {module}")

    if offenders:
        joined = "\n".join(sorted(offenders))
        pytest.fail(f"Runtime must not import tests:\n{joined}")
