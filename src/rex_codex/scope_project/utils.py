"""Shared utilities for the rex-codex Python CLI."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class RexError(RuntimeError):
    """Raised when a command should exit with a non-zero status."""


def _env_root() -> Path | None:
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


def _atomic_write(path: Path, text: str) -> None:
    """Persist ``text`` to ``path`` atomically with fsync to reduce corruption."""

    ensure_dir(path.parent)
    temp_path: Path | None = None
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        delete=False,
        prefix=f".{path.name}.",
    ) as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
        temp_path = Path(handle.name)
    if temp_path is None:
        raise RuntimeError(f"Failed to write temporary file for {path}")
    try:
        os.replace(temp_path, path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    _fsync_directory(path.parent)


def _fsync_directory(directory: Path) -> None:
    try:
        dir_fd = os.open(directory, os.O_DIRECTORY)
    except (AttributeError, FileNotFoundError, NotADirectoryError, OSError):
        return
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def dump_json(
    path: Path,
    data: object,
    *,
    sort_keys: bool = True,
    ensure_ascii: bool = True,
) -> None:
    text = json.dumps(data, indent=2, sort_keys=sort_keys, ensure_ascii=ensure_ascii)
    _atomic_write(path, f"{text}\n")


def which(executable: str) -> str | None:
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
) -> subprocess.CompletedProcess[str]:
    """Thin wrapper around subprocess.run with sensible defaults."""
    if env is None:
        merged_env: dict[str, str] = dict(os.environ)
    else:
        merged_env = {**os.environ, **env}
    kwargs: dict[str, Any] = {"cwd": cwd, "env": merged_env, "check": check}
    if capture_output:
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE
    if text:
        kwargs["text"] = True
    return subprocess.run(list(cmd), **kwargs)


@dataclass(frozen=True)
class RexContext:
    root: Path
    codex_ci_dir: Path
    monitor_log_dir: Path
    rex_agent_file: Path
    venv_dir: Path

    @classmethod
    def discover(cls) -> RexContext:
        root = repo_root()
        codex_ci = ensure_dir(root / ".codex_ci")
        monitor_logs = ensure_dir(root / ".agent" / "logs")
        return cls(
            root=root,
            codex_ci_dir=codex_ci,
            monitor_log_dir=monitor_logs,
            rex_agent_file=root / "rex-agent.json",
            venv_dir=root / ".venv",
        )

    def relative(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.root))
        except ValueError:
            return str(path)

    def is_agent_repo(self) -> bool:
        """Return True when the current root appears to be the agent source tree."""
        package_sentinels = [
            self.root / "src" / "rex_codex" / "__init__.py",
            self.root / "rex_codex" / "__init__.py",
        ]
        if not any(candidate.exists() for candidate in package_sentinels):
            return False
        other_sentinels = [
            self.root / "scripts" / "selftest_loop.sh",
            self.root / "bin" / "rex-codex",
        ]
        return all(item.exists() for item in other_sentinels)


def _codex_flags_tokens(flags: str) -> list[str]:
    if not flags or not flags.strip():
        return []
    try:
        return shlex.split(flags)
    except ValueError:
        return flags.split()


def _parse_codex_config_value(raw: str) -> object:
    value = raw.strip()
    if not value:
        return ""
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        lowered = value.lower()
        if lowered in {"true", "false"}:
            return lowered == "true"
        if lowered in {"null", "none"}:
            return None
        try:
            if "." in value:
                return float(value)
            return int(value)
        except ValueError:
            return value


def parse_codex_config_overrides(
    flags: str,
) -> tuple[list[dict[str, object]], dict[str, dict[str, object]]]:
    tokens = _codex_flags_tokens(flags)
    entries: list[dict[str, object]] = []
    mapping: dict[str, dict[str, object]] = {}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        payload: str | None = None
        source: str | None = None
        if token in {"-c", "--config"}:
            index += 1
            if index < len(tokens):
                payload = tokens[index]
                source = token
        elif token.startswith("--config="):
            payload = token[len("--config=") :]
            source = "--config"
        elif token.startswith("-c") and token not in {"-c", "--config"}:
            payload = token[2:]
            source = "-c"
        if payload is None:
            index += 1
            continue
        if "=" not in payload:
            index += 1
            continue
        key, raw_value = payload.split("=", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        if not key:
            index += 1
            continue
        parsed_value = _parse_codex_config_value(raw_value)
        entry: dict[str, object] = {
            "key": key,
            "value": raw_value,
            "source": source or "",
        }
        if parsed_value != raw_value:
            entry["parsed"] = parsed_value
        entries.append(entry)
        mapping[key] = entry
        index += 1
    return entries, mapping


def _extract_model_from_flags(flags: str) -> tuple[str | None, str | None]:
    tokens = _codex_flags_tokens(flags)
    for idx, token in enumerate(tokens):
        if token in {"--model", "-m"}:
            if idx + 1 < len(tokens):
                return tokens[idx + 1], token
            continue
        if token.startswith("--model="):
            return token.split("=", 1)[1], "--model"
        if token.startswith("-m") and len(token) > 2:
            return token[2:], "-m"
    return None, None


def _collect_llm_env_parameters() -> tuple[dict[str, object], dict[str, str]]:
    values: dict[str, object] = {}
    sources: dict[str, str] = {}

    def capture(env_var: str, key: str, parser: type) -> None:
        raw = os.environ.get(env_var)
        if raw is None:
            return
        text = raw.strip()
        if not text:
            return
        try:
            if parser is float:
                value = float(text)
            elif parser is int:
                value = int(text)
            else:
                value = text
        except (TypeError, ValueError):
            return
        values[key] = value
        sources[key] = f"env:{env_var}"

    capture("CODEX_TEMPERATURE", "temperature", float)
    capture("CODEX_TOP_P", "top_p", float)
    capture("CODEX_MAX_OUTPUT_TOKENS", "max_output_tokens", int)
    capture("CODEX_SEED", "seed", int)
    effort = os.environ.get("CODEX_REASONING_EFFORT")
    if effort and effort.strip():
        values["reasoning_effort"] = effort.strip()
        sources["reasoning_effort"] = "env:CODEX_REASONING_EFFORT"
    return values, sources


def build_llm_settings(
    *,
    codex_bin: str,
    codex_flags: str,
    codex_model: str,
) -> dict[str, object]:
    overrides, mapping = parse_codex_config_overrides(codex_flags)
    model = (codex_model or "").strip()
    model_source: str | None = None
    if model:
        model_source = "env:MODEL"
    else:
        flagged_model, flag_source = _extract_model_from_flags(codex_flags)
        if flagged_model:
            model = flagged_model
            model_source = f"flag:{flag_source}"
        elif "model" in mapping:
            entry = mapping["model"]
            parsed = entry.get("parsed")
            value = parsed if parsed is not None else entry.get("value", "")
            model = str(value).strip()
            if model:
                model_source = "config:model"
    parameters, parameter_sources = _collect_llm_env_parameters()
    settings: dict[str, object] = {
        "bin": codex_bin,
        "flags": codex_flags,
        "model": model,
        "model_explicit": bool(model),
    }
    if model_source:
        settings["model_source"] = model_source
    if overrides:
        settings["config_overrides"] = overrides
    if parameters:
        settings["parameters"] = parameters
    if parameter_sources:
        settings["parameter_sources"] = parameter_sources
    return settings


def update_llm_settings(
    context: RexContext,
    *,
    codex_bin: str,
    codex_flags: str,
    codex_model: str,
) -> dict[str, object]:
    payload = build_llm_settings(
        codex_bin=codex_bin,
        codex_flags=codex_flags,
        codex_model=codex_model,
    )
    snapshot = load_json(context.rex_agent_file)
    llm_state = dict(payload)
    llm_state["updated_at"] = (
        datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    )
    snapshot["llm"] = llm_state
    dump_json(context.rex_agent_file, snapshot)
    return payload


class FileLock:
    """Simple advisory file lock using fcntl."""

    def __init__(self, lock_path: Path):
        self.lock_path = lock_path
        self._fd: int | None = None

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

    def __enter__(self) -> FileLock:
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
    if context.venv_dir.exists():
        if not quiet:
            print("[*] Resetting Python virtual environment (.venv)…")
        import shutil

        shutil.rmtree(context.venv_dir)
    else:
        if not quiet:
            print("[*] Creating Python virtual environment (.venv)…")
    run(["python3", "-m", "venv", str(context.venv_dir)])
    pip = context.venv_dir / "bin" / "pip"
    run(
        [str(pip), "install", "--upgrade", "pip"],
        check=True,
        capture_output=quiet,
        text=True,
    )


def activate_venv(context: RexContext) -> dict[str, str]:
    env = os.environ.copy()
    env["VIRTUAL_ENV"] = str(context.venv_dir)
    bin_path = context.venv_dir / "bin"
    current_path = env.get("PATH", "")
    env["PATH"] = f"{bin_path}{os.pathsep}{current_path}"
    return env


def read_lines(path: Path) -> list[str]:
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
    base_cmd: list[str] = [str(pip), "install"]
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


def _audit_candidate_paths(root: Path) -> list[Path]:
    patterns = [
        "*.md",
        "AGENTS.md",
        "README.md",
        ".codex_ci_latest.log",
        ".codex_ci/*.log",
        "documents/**/*.md",
        "bin/**/*.py",
        "bin/**/*.sh",
        "scripts/**/*.py",
        "scripts/**/*.sh",
        "rex_codex/**/*.py",
        "src/rex_codex/**/*.py",
    ]
    seen: set[Path] = set()
    excluded_root = root / "for_external_GPT5_pro_audit"
    for pattern in patterns:
        for path in root.glob(pattern):
            if not path.is_file():
                continue
            if excluded_root in path.parents:
                continue
            seen.add(path.resolve())
    return sorted(seen)


def _is_gitignored(root: Path, path: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    try:
        result = run(
            ["git", "check-ignore", "-q", "--", str(relative)],
            cwd=root,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        return False
    return result.returncode == 0


def _render_directory_listing(root: Path) -> str:
    max_depth = 3
    per_dir_limit = 25
    line_budget = 400
    skip_dir_names = {
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".nox",
        ".idea",
    }
    skip_contents_dirs = {".git"}
    gitignore_cache: dict[Path, bool] = {}
    lines: list[str] = []
    truncated = False

    def add_line(text: str) -> bool:
        nonlocal line_budget, truncated
        if truncated:
            return False
        if line_budget <= 0:
            lines.append("  ... (directory listing truncated)")
            truncated = True
            return False
        lines.append(text)
        line_budget -= 1
        return True

    def is_gitignored_cached(path: Path) -> bool:
        resolved = path.resolve()
        if resolved in gitignore_cache:
            return gitignore_cache[resolved]
        ignored = _is_gitignored(root, resolved)
        gitignore_cache[resolved] = ignored
        return ignored

    def walk(path: Path, depth: int) -> None:
        if truncated:
            return
        try:
            entries = sorted(
                path.iterdir(),
                key=lambda item: (not item.is_dir(), item.name.lower()),
            )
        except OSError as exc:
            add_line(f"{'  ' * (depth + 1)}[Error listing {path.name}: {exc}]")
            return

        filtered: list[Path] = []
        for entry in entries:
            if entry.is_dir() and entry.name in skip_dir_names:
                continue
            filtered.append(entry)

        shown = 0
        total_entries = len(filtered)
        for entry in filtered:
            if truncated:
                break
            if shown >= per_dir_limit:
                break
            rel = entry.relative_to(root)
            indent = "  " * (depth + 1)
            if entry.is_dir():
                ignored = is_gitignored_cached(entry)
                if ignored:
                    add_line(
                        f"{indent}{rel.as_posix()}/ (gitignored; contents omitted)"
                    )
                elif entry.name in skip_contents_dirs:
                    add_line(f"{indent}{rel.as_posix()}/ (contents omitted)")
                elif depth + 1 >= max_depth:
                    add_line(f"{indent}{rel.as_posix()}/ (depth limit)")
                else:
                    add_line(f"{indent}{rel.as_posix()}/")
                    walk(entry, depth + 1)
                shown += 1
            else:
                add_line(f"{indent}{rel.as_posix()}")
                shown += 1

        remaining = total_entries - shown
        if remaining > 0 and not truncated:
            indent = "  " * (depth + 1)
            add_line(f"{indent}... ({remaining} more entries)")

    add_line("./")
    walk(root, 0)
    return "\n".join(lines)


def _write_audit_file(audit_path: Path, root: Path, files: list[Path]) -> None:
    with audit_path.open("w", encoding="utf-8") as fh:
        fh.write("# External GPT5-Pro Audit Snapshot\n")
        fh.write(f"Generated at {datetime.now(UTC).isoformat()}\n\n")
        fh.write("## Repository Layout\n")
        fh.write(_render_directory_listing(root))
        fh.write("\n\n")
        fh.write("## File Snapshots\n\n")
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


def _env_flag(name: str) -> bool:
    value = os.environ.get(name, "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


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
    if _env_flag("REX_DISABLE_AUTO_PUSH"):
        print("[audit] Skipping git push (REX_DISABLE_AUTO_PUSH is set).")
        return
    push = run(["git", "push"], cwd=root, capture_output=True, check=False)
    if push.returncode != 0:
        print(f"[audit] git push failed: {push.stderr or push.stdout}")


def create_audit_snapshot(
    context: RexContext,
    *,
    auto_commit: bool = True,
    extra_sections: list[tuple[str, Sequence[str]]] | None = None,
) -> Path:
    root = context.root
    audit_dir = ensure_dir(root / "for_external_GPT5_pro_audit")
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    audit_path = audit_dir / f"audit_{timestamp}.md"
    files = _audit_candidate_paths(root)
    if not files:
        print("[audit] No candidate files found for snapshot.")
        return audit_path
    _write_audit_file(audit_path, root, files)
    if extra_sections:
        with audit_path.open("a", encoding="utf-8") as fh:
            for title, lines in extra_sections:
                fh.write(f"\n## {title}\n\n")
                for line in lines:
                    if line is None:
                        continue
                    fh.write(f"{line}\n")
    print(f"[audit] Snapshot written to {audit_path}")
    if context.is_agent_repo() and not _env_flag("REX_AGENT_FORCE_BUILD"):
        if auto_commit:
            print(
                "[audit] Detected rex-codex source tree; defaulting to testing mode (auto commit disabled)."
            )
        auto_commit = False
    if _env_flag("REX_DISABLE_AUTO_COMMIT"):
        auto_commit = False
    if auto_commit:
        _auto_commit_and_push(root, audit_path)
    return audit_path
