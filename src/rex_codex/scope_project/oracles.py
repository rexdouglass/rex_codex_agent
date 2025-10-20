"""Declarative orchestration of extended test oracles."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from importlib import util as importlib_util
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import yaml

from .events import emit_event
from .utils import RexContext, repo_root


class OracleError(RuntimeError):
    """Raised when an oracle manifest is malformed."""


@dataclass(slots=True)
class OracleDefinition:
    """Single oracle execution specification."""

    name: str
    kind: str
    command: str
    description: str = ""
    cwd: Path | None = None
    env: dict[str, str] = field(default_factory=dict)
    required_paths: list[str] = field(default_factory=list)
    required_commands: list[str] = field(default_factory=list)
    required_modules: list[str] = field(default_factory=list)
    continue_on_error: bool = False
    timeout: int | None = None
    tags: list[str] = field(default_factory=list)

    @property
    def task_name(self) -> str:
        return f"oracle:{self.name}"


@dataclass(slots=True)
class OracleManifest:
    """Manifest describing all registered oracles."""

    schema_version: str
    default_fail_fast: bool = True
    notes: list[str] = field(default_factory=list)
    oracles: list[OracleDefinition] = field(default_factory=list)
    path: Path | None = None

    def select(self, names: Iterable[str] | None = None) -> list[OracleDefinition]:
        if not names:
            return list(self.oracles)
        lookup = {oracle.name: oracle for oracle in self.oracles}
        selected: list[OracleDefinition] = []
        for name in names:
            if name not in lookup:
                raise OracleError(f"Oracle '{name}' not found in manifest.")
            selected.append(lookup[name])
        return selected


@dataclass(slots=True)
class OracleResult:
    definition: OracleDefinition
    status: str
    returncode: int | None
    duration_seconds: float
    reason: str | None = None
    log_path: Path | None = None

    @property
    def passed(self) -> bool:
        return self.status == "passed"

    @property
    def failed(self) -> bool:
        return self.status == "failed"

    @property
    def skipped(self) -> bool:
        return self.status == "skipped"


DEFAULT_MANIFEST_PATH = Path("documents/oracles/oracles.yaml")
SUPPORTED_SCHEMA_VERSIONS = {"oracle-manifest.v1"}


def discover_manifest_path(
    context: RexContext, explicit: Path | None = None
) -> Path | None:
    if explicit:
        path = explicit if explicit.is_absolute() else context.root / explicit
        return path if path.exists() else None
    candidate = context.root / DEFAULT_MANIFEST_PATH
    return candidate if candidate.exists() else None


def load_manifest(context: RexContext, path: Path | None = None) -> OracleManifest | None:
    manifest_path = discover_manifest_path(context, path)
    if manifest_path is None:
        return None
    try:
        payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:  # pragma: no cover - YAML parser detail
        raise OracleError(f"Failed to parse oracle manifest: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise OracleError("Oracle manifest must contain a mapping at the top level.")
    schema_version = str(payload.get("schema_version", "")).strip()
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise OracleError(
            f"Unsupported oracle manifest schema '{schema_version}' "
            f"(expected one of: {', '.join(sorted(SUPPORTED_SCHEMA_VERSIONS))})"
        )
    default_fail_fast = bool(payload.get("default_fail_fast", True))
    notes = payload.get("notes") or []
    if not isinstance(notes, list):
        raise OracleError("Manifest field 'notes' must be a list when present.")
    raw_oracles = payload.get("oracles") or []
    if not isinstance(raw_oracles, list):
        raise OracleError("Manifest field 'oracles' must be a list.")
    oracles: list[OracleDefinition] = []
    for entry in raw_oracles:
        if not isinstance(entry, Mapping):
            raise OracleError("Each oracle entry must be a mapping.")
        try:
            definition = OracleDefinition(
                name=str(entry["name"]),
                kind=str(entry.get("kind", "custom")),
                command=str(entry["command"]),
                description=str(entry.get("description", "")),
                cwd=_resolve_optional_path(entry.get("cwd"), context),
                env=_normalize_str_mapping(entry.get("env")),
                required_paths=_normalize_str_list(entry.get("required_paths")),
                required_commands=_normalize_str_list(entry.get("required_commands")),
                required_modules=_normalize_str_list(entry.get("required_modules")),
                continue_on_error=bool(entry.get("continue_on_error", False)),
                timeout=_normalize_optional_int(entry.get("timeout")),
                tags=_normalize_str_list(entry.get("tags")),
            )
        except KeyError as exc:
            raise OracleError(f"Missing required key in oracle entry: {exc}") from exc
        oracles.append(definition)
    manifest = OracleManifest(
        schema_version=schema_version,
        default_fail_fast=default_fail_fast,
        notes=[str(note) for note in notes if note],
        oracles=oracles,
        path=manifest_path,
    )
    return manifest


def _normalize_str_mapping(value: Any) -> dict[str, str]:
    if not value:
        return {}
    if not isinstance(value, Mapping):
        raise OracleError("Oracle 'env' must be a mapping of KEY -> VALUE.")
    return {str(k): str(v) for k, v in value.items()}


def _normalize_str_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Sequence):
        return [str(item) for item in value]
    raise OracleError("Oracle list fields (paths/commands/modules/tags) must be sequences.")


def _normalize_optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise OracleError(f"Oracle timeout must be an integer, got {value!r}") from exc


def _resolve_optional_path(value: Any, context: RexContext) -> Path | None:
    if not value:
        return None
    candidate = Path(str(value))
    if not candidate.is_absolute():
        candidate = context.root / candidate
    return candidate


def run_oracles(
    manifest: OracleManifest,
    *,
    context: RexContext,
    names: Iterable[str] | None = None,
    fail_fast: bool | None = None,
    verbose: bool = True,
) -> tuple[int, list[OracleResult]]:
    selected = manifest.select(names)
    if not selected:
        return 0, []
    fail_fast = manifest.default_fail_fast if fail_fast is None else fail_fast
    results: list[OracleResult] = []
    exit_code = 0
    for oracle in selected:
        result = _run_single_oracle(oracle, context=context, verbose=verbose)
        results.append(result)
        if result.failed:
            exit_code = exit_code or (result.returncode or 1)
            if fail_fast and not oracle.continue_on_error:
                break
    return exit_code, results


def _run_single_oracle(
    oracle: OracleDefinition, *, context: RexContext, verbose: bool
) -> OracleResult:
    start = time.perf_counter()
    relative_root = repo_root()
    reason: str | None = None
    status = "passed"
    returncode: int | None = 0

    missing_path = _first_missing_path(oracle.required_paths, context.root)
    if missing_path:
        status = "skipped"
        reason = f"required path missing: {missing_path}"
        return _finalise_oracle_result(
            oracle,
            status=status,
            returncode=None,
            duration=time.perf_counter() - start,
            reason=reason,
        )

    missing_command = _first_missing_command(oracle.required_commands)
    if missing_command:
        status = "skipped"
        reason = f"required command not found: {missing_command}"
        return _finalise_oracle_result(
            oracle,
            status=status,
            returncode=None,
            duration=time.perf_counter() - start,
            reason=reason,
        )

    missing_module = _first_missing_module(oracle.required_modules)
    if missing_module:
        status = "skipped"
        reason = f"required module not installed: {missing_module}"
        return _finalise_oracle_result(
            oracle,
            status=status,
            returncode=None,
            duration=time.perf_counter() - start,
            reason=reason,
        )

    cwd = oracle.cwd or context.root
    env = os.environ.copy()
    env.update(oracle.env)
    try:
        cwd_display = str(cwd.relative_to(relative_root))
    except ValueError:
        cwd_display = str(cwd)

    emit_event(
        "oracles",
        "oracle_started",
        task=oracle.task_name,
        status="running",
        name=oracle.name,
        kind=oracle.kind,
        command=oracle.command,
        cwd=cwd_display,
        timeout=oracle.timeout,
        tags=oracle.tags,
    )
    if verbose:
        print(
            f"[oracles] {oracle.name}: executing `{oracle.command}` "
            f"(kind={oracle.kind})"
        )
    try:
        proc = subprocess.run(
            ["bash", "-lc", oracle.command],
            cwd=cwd,
            env=env,
            check=False,
            timeout=oracle.timeout,
        )
        returncode = proc.returncode
        if returncode != 0:
            status = "failed"
            reason = f"command exited with status {proc.returncode}"
    except subprocess.TimeoutExpired:
        status = "failed"
        returncode = None
        reason = (
            f"command timed out after {oracle.timeout}s"
            if oracle.timeout
            else "command timed out"
        )
    except OSError as exc:
        status = "failed"
        returncode = None
        reason = f"unable to start command: {exc}"

    duration = time.perf_counter() - start
    return _finalise_oracle_result(
        oracle,
        status=status,
        returncode=returncode,
        duration=duration,
        reason=reason,
    )


def _finalise_oracle_result(
    oracle: OracleDefinition,
    *,
    status: str,
    returncode: int | None,
    duration: float,
    reason: str | None,
) -> OracleResult:
    emit_event(
        "oracles",
        "oracle_completed" if status != "skipped" else "oracle_skipped",
        task=oracle.task_name,
        status=status,
        name=oracle.name,
        kind=oracle.kind,
        command=oracle.command,
        returncode=returncode,
        reason=reason,
        duration_ms=round(duration * 1000, 2),
        continue_on_error=oracle.continue_on_error,
    )
    return OracleResult(
        definition=oracle,
        status=status,
        returncode=returncode,
        duration_seconds=duration,
        reason=reason,
    )


def _first_missing_path(paths: Sequence[str], root: Path) -> str | None:
    for entry in paths:
        candidate = Path(entry)
        candidate = candidate if candidate.is_absolute() else root / candidate
        if not candidate.exists():
            return entry
    return None


def _first_missing_command(commands: Sequence[str]) -> str | None:
    for command in commands:
        if shutil.which(command) is None:
            return command
    return None


def _first_missing_module(modules: Sequence[str]) -> str | None:
    for module in modules:
        try:
            if importlib_util.find_spec(module) is None:
                return module
        except (ModuleNotFoundError, ValueError):
            return module
    return None


def format_results_table(results: Sequence[OracleResult]) -> str:
    if not results:
        return ""
    header = f"{'Oracle':<24} {'Kind':<14} {'Status':<9} {'Duration':>9}  Details"
    lines = [header, "-" * len(header)]
    for result in results:
        status_display = result.status.upper()
        duration_display = f"{result.duration_seconds:0.2f}s"
        detail = result.reason or ""
        lines.append(
            f"{result.definition.name:<24} "
            f"{result.definition.kind:<14} "
            f"{status_display:<9} "
            f"{duration_display:>9}  {detail}"
        )
    return "\n".join(lines)


def summarize_results(results: Sequence[OracleResult]) -> Mapping[str, Any]:
    summary: dict[str, Any] = {
        "total": len(results),
        "passed": sum(1 for result in results if result.passed),
        "failed": sum(1 for result in results if result.failed),
        "skipped": sum(1 for result in results if result.skipped),
        "results": [
            {
                "name": result.definition.name,
                "kind": result.definition.kind,
                "status": result.status,
                "returncode": result.returncode,
                "duration_seconds": round(result.duration_seconds, 3),
                "reason": result.reason,
            }
            for result in results
        ],
    }
    return summary
