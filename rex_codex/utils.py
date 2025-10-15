"""Shared utilities for the rex-codex Python CLI."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import os
import shlex
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Mapping, MutableMapping, Optional, Sequence, Set


class RexError(RuntimeError):
    """Raised when a command should exit with a non-zero status."""


def _env_root() -> Optional[Path]:
    root = os.environ.get("ROOT")
    if root:
        return Path(root).resolve()
    return None


def repo_root() -> Path:
    """Return the repository root, favouring the git toplevel."""
    if cached := _env_root():
        return cached
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return Path(completed.stdout.strip()).resolve()
    except subprocess.CalledProcessError:
        return Path.cwd().resolve()


def agent_home(root: Path | None = None) -> Path:
    root = root or repo_root()
    return root / ".rex_agent"


def agent_src(root: Path | None = None) -> Path:
    root = root or repo_root()
    env_src = os.environ.get("REX_SRC")
    if env_src:
        return Path(env_src).resolve()
    return agent_home(root) / "src"


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def dump_json(path: Path, data: Mapping) -> None:
    text = json.dumps(data, indent=2, sort_keys=True)
    path.write_text(f"{text}\n", encoding="utf-8")


def which(executable: str) -> Optional[str]:
    from shutil import which as _which

    return _which(executable)


def shlex_join(cmd: Sequence[str]) -> str:
    return shlex.join(cmd)


def run(
    cmd: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    check: bool = True,
    capture_output: bool = False,
    text: bool = True,
) -> subprocess.CompletedProcess:
    """Thin wrapper around subprocess.run with sensible defaults."""
    merged_env: MutableMapping[str, str]
    if env is None:
        merged_env = os.environ.copy()
    else:
        merged_env = {**os.environ, **env}
    kwargs: Dict[str, object] = {"cwd": cwd, "env": merged_env, "check": check}
    if capture_output:
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE
    if text:
        kwargs["text"] = True
    return subprocess.run(list(cmd), **kwargs)  # type: ignore[arg-type]


@dataclass(frozen=True)
class RexContext:
    root: Path
    codex_ci_dir: Path
    rex_agent_file: Path
    venv_dir: Path

    @classmethod
    def discover(cls) -> "RexContext":
        root = repo_root()
        codex_ci = ensure_dir(root / ".codex_ci")
        return cls(
            root=root,
            codex_ci_dir=codex_ci,
            rex_agent_file=root / "rex-agent.json",
            venv_dir=root / ".venv",
        )

    def relative(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.root))
        except ValueError:
            return str(path)


class FileLock:
    """Simple advisory file lock using fcntl."""

    def __init__(self, lock_path: Path):
        self.lock_path = lock_path
        self._fd: Optional[int] = None

    def acquire(self, blocking: bool = False) -> None:
        import fcntl

        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o666)
        flag = fcntl.LOCK_EX
        if not blocking:
            flag |= fcntl.LOCK_NB
        try:
            fcntl.flock(fd, flag)
        except BlockingIOError as exc:  # pragma: no cover - depends on runtime race
            os.close(fd)
            raise RexError(f"Another rex-codex process holds {self.lock_path}") from exc
        self._fd = fd

    def release(self) -> None:
        import fcntl

        if self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> "FileLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


@contextmanager
def lock_file(path: Path) -> Iterator[None]:
    lock = FileLock(path)
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


def ensure_python(context: RexContext, *, quiet: bool = False) -> None:
    if which("python3") is None:
        raise RexError("python3 not found on PATH")
    if not context.venv_dir.exists():
        if not quiet:
            print("[*] Creating Python virtual environment (.venv)…")
        run(["python3", "-m", "venv", str(context.venv_dir)])
    pip = context.venv_dir / "bin" / "pip"
    run([str(pip), "install", "--upgrade", "pip"], check=True)


def activate_venv(context: RexContext) -> Dict[str, str]:
    env = os.environ.copy()
    env["VIRTUAL_ENV"] = str(context.venv_dir)
    bin_path = context.venv_dir / "bin"
    current_path = env.get("PATH", "")
    env["PATH"] = f"{bin_path}{os.pathsep}{current_path}"
    return env


def read_lines(path: Path) -> List[str]:
    if not path.exists():
        return []
    return [line.rstrip("\n") for line in path.read_text(encoding="utf-8").splitlines()]


def write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def print_header(title: str) -> None:
    print(f"=== {title} ===")


def prompt(message: str) -> str:
    try:
        return input(message)
    except EOFError:
        return ""


def ask_confirmation(message: str, *, expected: str) -> bool:
    response = prompt(message)
    return response.strip() == expected


def ensure_requirements_installed(
    context: RexContext,
    requirements_template: Path,
    *,
    quiet: bool = True,
) -> None:
    env = activate_venv(context)
    pip = context.venv_dir / "bin" / "pip"
    base_cmd: List[str] = [str(pip), "install"]
    if quiet:
        base_cmd.append("-q")
    if requirements_template.exists():
        run(base_cmd + ["-r", str(requirements_template)], env=env)
    else:
        baseline = [
            "pytest==8.0.2",
            "pytest-xdist==3.5.0",
            "pytest-cov==4.1.0",
            "black==24.4.2",
            "isort==5.13.2",
            "ruff==0.3.2",
            "flake8==7.0.0",
            "mypy==1.8.0",
        ]
        run(base_cmd + baseline, env=env)


def _audit_candidate_paths(root: Path) -> List[Path]:
    patterns = [
        "*.md",
        "AGENTS.md",
        "README.md",
        "documents/**/*.md",
        "bin/**/*.py",
        "bin/**/*.sh",
        "scripts/**/*.py",
        "scripts/**/*.sh",
        "rex_codex/**/*.py",
    ]
    seen: Set[Path] = set()
    excluded_root = root / "for_external_GPT5_pro_audit"
    for pattern in patterns:
        for path in root.glob(pattern):
            if not path.is_file():
                continue
            if excluded_root in path.parents:
                continue
            seen.add(path.resolve())
    return sorted(seen)


def _write_audit_file(audit_path: Path, files: List[Path]) -> None:
    with audit_path.open("w", encoding="utf-8") as fh:
        fh.write(f"# External GPT5-Pro Audit Snapshot\n")
        fh.write(f"Generated at {datetime.now(UTC).isoformat()}\n\n")
        for file_path in files:
            resolved = file_path.as_posix()
            fh.write(f"=== {resolved} ===\n")
            try:
                contents = file_path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:  # pragma: no cover - filesystem errors
                fh.write(f"[Error reading file: {exc}]\n\n")
                continue
            fh.write(contents)
            if not contents.endswith("\n"):
                fh.write("\n")
            fh.write("\n")


def _auto_commit_and_push(root: Path, audit_path: Path) -> None:
    run(["git", "add", "-A"], cwd=root, check=False)
    status = run(
        ["git", "status", "--porcelain"],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if not (status.stdout or "").strip():
        print("[audit] No changes detected; skipping commit.")
        return
    message = f"chore: external audit snapshot {audit_path.name}"
    commit = run(
        ["git", "commit", "-m", message],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if commit.returncode != 0:
        print(f"[audit] git commit failed: {commit.stderr or commit.stdout}")
        return
    push = run(["git", "push"], cwd=root, capture_output=True, check=False)
    if push.returncode != 0:
        print(f"[audit] git push failed: {push.stderr or push.stdout}")


def create_audit_snapshot(context: RexContext, *, auto_commit: bool = True) -> Path:
    root = context.root
    audit_dir = ensure_dir(root / "for_external_GPT5_pro_audit")
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    audit_path = audit_dir / f"audit_{timestamp}.md"
    files = _audit_candidate_paths(root)
    if not files:
        print("[audit] No candidate files found for snapshot.")
        return audit_path
    _write_audit_file(audit_path, files)
    print(f"[audit] Snapshot written to {audit_path}")
    if auto_commit:
        _auto_commit_and_push(root, audit_path)
    return audit_path
