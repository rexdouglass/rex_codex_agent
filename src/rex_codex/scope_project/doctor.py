"""Diagnostics for rex-codex."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Sequence

from .utils import RexContext, dump_json, load_json, which


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    status: str
    message: str
    path: str | None = None
    hint: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "name": self.name,
            "status": self.status,
            "message": self.message,
        }
        if self.path:
            payload["path"] = self.path
        if self.hint:
            payload["hint"] = self.hint
        return payload


def run_doctor(
    *,
    output: str = "text",
    context: RexContext | None = None,
    persist: bool = True,
) -> list[DoctorCheck]:
    context = context or RexContext.discover()
    results = gather_diagnostics()
    if output == "json":
        print(json.dumps([check.to_dict() for check in results], indent=2))
    else:
        for check in results:
            prefix = "[doctor]"
            location = f" ({check.path})" if check.path else ""
            line = f"{prefix} {check.name}: {check.status.upper()} - {check.message}{location}"
            print(line)
            if check.hint:
                print(f"          hint: {check.hint}")
    if persist and context:
        _record_results(context, results)
    return results


def gather_diagnostics() -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    checks.append(
        _check_tool(
            name="python3",
            command=["python3", "--version"],
            minimum=(3, 10),
            hint="Install Python 3.10+ and ensure it is on PATH.",
        )
    )
    checks.append(
        _check_tool(
            name="bash",
            command=["bash", "--version"],
            minimum=(4, 0),
            hint="Install Bash 4+ (macOS users can `brew install bash`).",
        )
    )
    checks.append(
        _check_tool(
            name="node",
            command=["node", "--version"],
            minimum=(18, 0),
            hint="Install Node.js 18+ (https://nodejs.org).",
        )
    )
    checks.append(
        _check_tool(
            name="npx",
            command=["npx", "--version"],
            minimum=(10, 0),
            hint="Upgrade npm to obtain a modern npx (npm install -g npm).",
        )
    )

    checks.append(_check_codex_cli())

    docker_check = _check_tool(
        name="docker",
        command=["docker", "--version"],
        hint="Install Docker if you plan to run containerised workflows.",
        treat_missing_as_warn=True,
    )
    checks.append(docker_check)

    return checks


def _check_tool(
    *,
    name: str,
    command: Sequence[str],
    minimum: tuple[int, ...] | None = None,
    hint: str | None = None,
    treat_missing_as_warn: bool = False,
) -> DoctorCheck:
    path = which(name)
    if not path:
        status = "warn" if treat_missing_as_warn else "error"
        message = "not found on PATH"
        return DoctorCheck(name=name, status=status, message=message, hint=hint)

    version_output = _run_version_command(command)
    if version_output is None:
        return DoctorCheck(
            name=name,
            status="warn",
            message="unable to determine version",
            path=path,
            hint=hint,
        )

    message = version_output.splitlines()[0].strip()
    if minimum is None:
        return DoctorCheck(name=name, status="ok", message=message, path=path)

    detected = _extract_version_tuple(version_output)
    if detected is None:
        return DoctorCheck(
            name=name,
            status="warn",
            message=f"unable to parse version from: {message}",
            path=path,
            hint=hint,
        )
    if detected >= minimum:
        return DoctorCheck(name=name, status="ok", message=message, path=path)
    return DoctorCheck(
        name=name,
        status="error",
        message=f"version {'.'.join(map(str, detected))} < required {'.'.join(map(str, minimum))}",
        path=path,
        hint=hint,
    )


def _check_codex_cli() -> DoctorCheck:
    codex_bin = os.environ.get("CODEX_BIN", "npx --yes @openai/codex")
    tokens = shlex.split(codex_bin)
    if not tokens:
        return DoctorCheck(
            name="codex",
            status="error",
            message="CODEX_BIN is empty",
            hint="Set CODEX_BIN or install npx @openai/codex.",
        )
    primary = tokens[0]
    path = which(primary)
    if not path:
        return DoctorCheck(
            name="codex",
            status="error",
            message=f"{primary} not found on PATH",
            hint="Install the Codex CLI (`npm install -g @openai/codex`) or ensure npx is available.",
        )
    return DoctorCheck(
        name="codex",
        status="ok",
        message=f"using {codex_bin}",
        path=path,
    )


def _run_version_command(cmd: Sequence[str]) -> str | None:
    try:
        completed = subprocess.run(
            list(cmd),
            capture_output=True,
            text=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    raw = completed.stdout.strip() or completed.stderr.strip()
    return raw


_VERSION_RE = re.compile(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?")


def _extract_version_tuple(text: str) -> tuple[int, ...] | None:
    match = _VERSION_RE.search(text)
    if not match:
        return None
    parts = [int(part) for part in match.groups(default="0")]
    while parts and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def _record_results(context: RexContext, results: list[DoctorCheck]) -> None:
    snapshot = load_json(context.rex_agent_file)
    doctor_state = snapshot.setdefault("doctor", {})
    status = "ok"
    errors = [check.name for check in results if check.status == "error"]
    warnings = [check.name for check in results if check.status == "warn"]
    if errors:
        status = "error"
    elif warnings:
        status = "warn"
    doctor_state.update(
        {
            "last_run": _utc_now(),
            "status": status,
            "errors": errors,
            "warnings": warnings,
            "checks": [check.to_dict() for check in results],
        }
    )
    dump_json(context.rex_agent_file, snapshot)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
