"""Hermeticity enforcement for generated specs."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterable, List, Tuple


BANNED_IMPORT_MODULES = {
    "requests": "network access via requests",
    "httpx": "network access via httpx",
    "urllib": "network access via urllib",
    "urllib3": "network access via urllib3",
    "aiohttp": "network access via aiohttp",
    "socket": "network access via socket",
    "secrets": "secrets module is non-deterministic; inject fixed values instead",
}

BANNED_CALL_PREFIXES = {
    "requests.": "network access via requests",
    "httpx.": "network access via httpx",
    "urllib.": "network access via urllib",
    "urllib3.": "network access via urllib3",
    "aiohttp.": "network access via aiohttp",
    "socket.": "network access via socket",
    "secrets.": "secrets module is non-deterministic; inject fixed values instead",
    "numpy.random.": "numpy.random must be seeded deterministically; avoid direct usage",
    "random.SystemRandom.": "SystemRandom uses system entropy; avoid in specs",
}

BANNED_CALL_EXACT = {
    "time.sleep": "time.sleep introduces nondeterministic delays",
    "asyncio.sleep": "asyncio.sleep introduces nondeterministic delays",
    "subprocess.run": "subprocess usage requires explicit stubbing",
    "subprocess.Popen": "subprocess usage requires explicit stubbing",
    "subprocess.call": "subprocess usage requires explicit stubbing",
    "os.system": "os.system usage should be avoided in specs",
    "time.time": "use a deterministic clock stub instead of time.time",
    "time.perf_counter": "use a deterministic clock stub instead of time.perf_counter",
    "time.monotonic": "use a deterministic clock stub instead of time.monotonic",
    "datetime.datetime.now": "use a frozen datetime or dependency injection",
    "datetime.datetime.utcnow": "use a frozen datetime or dependency injection",
    "datetime.datetime.today": "use a frozen datetime or dependency injection",
    "datetime.date.today": "use a frozen date or dependency injection",
    "pytest.skip": "skipping generated specs is not allowed",
    "pytest.xfail": "xfailing generated specs is not allowed",
    "os.urandom": "use deterministic stubs instead of os.urandom",
    "uuid.uuid4": "use a fixed UUID in specs instead of uuid.uuid4",
    "uuid.uuid1": "use a fixed UUID in specs instead of uuid.uuid1",
}

RANDOM_PREFIXES = ("random.", "numpy.random.")
RANDOM_ALLOWED = {"random.seed", "numpy.random.seed"}


class HermeticVisitor(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.aliases: dict[str, str] = {}
        self.violations: List[Tuple[Path, int, str]] = []

    def add_violation(self, lineno: int, detail: str) -> None:
        self.violations.append((self.path, lineno, detail))

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            name = alias.asname or alias.name.split(".")[-1]
            self.aliases[name] = alias.name
            root = alias.name.split(".")[0]
            if root in BANNED_IMPORT_MODULES:
                self.add_violation(node.lineno, f"import {alias.name} ({BANNED_IMPORT_MODULES[root]})")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        root = module.split(".")[0] if module else ""
        if root in BANNED_IMPORT_MODULES:
            self.add_violation(node.lineno, f"from {module} import ... ({BANNED_IMPORT_MODULES[root]})")
        for alias in node.names:
            target = f"{module}.{alias.name}" if module else alias.name
            name = alias.asname or alias.name
            self.aliases[name] = target
        self.generic_visit(node)

    def resolve(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return self.aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            base = self.resolve(node.value)
            if base:
                return f"{base}.{node.attr}"
            return node.attr
        return None

    def visit_Call(self, node: ast.Call) -> None:
        call_name = self.resolve(node.func)
        if call_name:
            if call_name in BANNED_CALL_EXACT:
                self.add_violation(node.lineno, f"{call_name} ({BANNED_CALL_EXACT[call_name]})")
            elif (
                any(call_name.startswith(prefix) for prefix in RANDOM_PREFIXES)
                and call_name not in RANDOM_ALLOWED
            ):
                self.add_violation(node.lineno, f"{call_name} (set a deterministic seed or avoid randomness)")
            else:
                for prefix, reason in BANNED_CALL_PREFIXES.items():
                    if call_name.startswith(prefix):
                        self.add_violation(node.lineno, f"{call_name} ({reason})")
                        break
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        for dec in node.decorator_list:
            name = self.resolve(dec)
            if not name:
                continue
            lname = name.lower()
            if lname.startswith("pytest.mark.skip") or lname.startswith("pytest.mark.xfail"):
                self.add_violation(
                    getattr(dec, "lineno", node.lineno),
                    f"{name} (skipping/xfailing specs is forbidden)",
                )
            elif lname.startswith("pytest.mark.skipif"):
                args = getattr(dec, "args", [])
                if args:
                    first = args[0]
                    value = getattr(first, "value", None)
                    if value is True:
                        self.add_violation(
                            getattr(dec, "lineno", node.lineno),
                            "pytest.mark.skipif(True, ...) is forbidden",
                        )
        self.generic_visit(node)


def ensure_hermetic(specs_dir: Path) -> bool:
    violations: List[Tuple[Path, int, str]] = []
    for path in specs_dir.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as err:
            violations.append((path, err.lineno or 0, f"SyntaxError: {err}"))
            continue
        visitor = HermeticVisitor(path)
        visitor.visit(tree)
        violations.extend(visitor.violations)

    if violations:
        for path, lineno, detail in violations:
            location = f"{path}:{lineno}" if lineno else str(path)
            print(f"{location}: {detail}")
        return False
    return True
