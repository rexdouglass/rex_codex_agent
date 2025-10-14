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


def has_spec(text: str | None) -> bool:
    if not text:
        return False
    normalized = text.strip()
    return ">>>" in normalized or "@spec_case" in normalized


def function_missing_contract(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    if node.name.startswith("_"):
        return False
    if not has_spec(ast.get_docstring(node)):
        return True
    for arg in node.args.args + node.args.kwonlyargs:
        if arg.arg in {"self", "cls"}:
            continue
        if arg.annotation is None:
            return True
    if node.returns is None:
        return True
    return False


@pytest.mark.unit
def test_public_functions_have_specs_and_types() -> None:
    modules = iter_runtime_modules()
    if not modules:
        pytest.skip("No runtime modules to audit yet")

    violations: list[str] = []
    for module_path in modules:
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if function_missing_contract(node):
                    violations.append(f"{module_path}:{node.lineno} missing spec or type hints")
            elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
                docstring = ast.get_docstring(node)
                if not has_spec(docstring):
                    violations.append(f"{module_path}:{node.lineno} class missing doc spec")

    if violations:
        joined = "\n".join(sorted(violations))
        pytest.fail(f"Spec/type guardrail violations:\n{joined}")
