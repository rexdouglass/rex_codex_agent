"""LLM provider abstraction for Codex and future backends."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict

from .events import emit_event
from .utils import RexContext


class LLMInvocationError(RuntimeError):
    """Raised when the configured LLM backend cannot satisfy a request."""


@dataclass(slots=True)
class LLMProviderConfig:
    context: RexContext
    codex_bin: str
    codex_flags: str
    codex_model: str


class LLMProvider:
    """Abstract base class for LLM JSON helpers."""

    def __init__(self, config: LLMProviderConfig):
        self.config = config

    def run_json(
        self,
        *,
        label: str,
        prompt: str,
        slug: str,
        verbose: bool = True,
    ) -> dict[str, Any]:
        raise NotImplementedError


class CodexLLMProvider(LLMProvider):
    """Provider that shells out to the Codex CLI expecting JSON output."""

    def run_json(
        self,
        *,
        label: str,
        prompt: str,
        slug: str,
        verbose: bool = True,
    ) -> dict[str, Any]:
        attempts = _planner_attempts()
        backoff = _planner_backoff()
        delay = _planner_initial_delay()
        timeout = _codex_timeout_seconds()

        for attempt in range(1, attempts + 1):
            if verbose:
                attempt_label = f"{label} (attempt {attempt}/{attempts})"
                print(f"[planner] Calling Codex {attempt_label}…")
            emit_event(
                "generator",
                "component_plan_stage_started",
                slug=slug,
                task=f"plan/{slug}",
                stage=label,
                attempt=attempt,
                provider="codex",
            )
            cmd = _build_codex_command(
                bin_spec=self.config.codex_bin,
                flags=self.config.codex_flags,
                model=self.config.codex_model,
                prompt=prompt,
                cwd=self.config.context.root,
            )

            try:
                completed = subprocess.run(
                    cmd,
                    cwd=self.config.context.root,
                    text=True,
                    capture_output=True,
                    timeout=timeout or None,
                )
            except subprocess.TimeoutExpired as exc:
                _emit_stage_failure(
                    slug=slug,
                    label=label,
                    attempt=attempt,
                    reason="timeout",
                    extra={"timeout_seconds": timeout},
                )
                if attempt < attempts:
                    _emit_stage_retry(
                        slug=slug,
                        label=label,
                        attempt=attempt + 1,
                        reason="timeout",
                    )
                    time.sleep(delay)
                    delay *= backoff
                    continue
                raise LLMInvocationError(
                    f"Codex ({label}) timed out after {timeout} seconds"
                ) from exc

            stdout = completed.stdout or ""
            stderr = completed.stderr or ""
            if completed.returncode != 0:
                _emit_stage_failure(
                    slug=slug,
                    label=label,
                    attempt=attempt,
                    reason="returncode",
                    extra={
                        "returncode": completed.returncode,
                        "stderr": stderr.strip(),
                    },
                )
                if attempt < attempts:
                    _emit_stage_retry(
                        slug=slug,
                        label=label,
                        attempt=attempt + 1,
                        reason="returncode",
                    )
                    time.sleep(delay)
                    delay *= backoff
                    continue
                raise LLMInvocationError(
                    f"Codex ({label}) failed with exit code {completed.returncode}: {stderr.strip()}"
                )

            stripped_stdout = stdout.strip()
            try:
                payload = json.loads(stripped_stdout)
            except json.JSONDecodeError as exc:
                _emit_stage_failure(
                    slug=slug,
                    label=label,
                    attempt=attempt,
                    reason="invalid_json",
                    extra={"output_preview": stripped_stdout[:2000]},
                )
                if attempt < attempts:
                    _emit_stage_retry(
                        slug=slug,
                        label=label,
                        attempt=attempt + 1,
                        reason="invalid_json",
                    )
                    time.sleep(delay)
                    delay *= backoff
                    continue
                raise LLMInvocationError(
                    f"Codex ({label}) did not return STRICT JSON: {exc.msg}"
                ) from exc

            emit_event(
                "generator",
                "component_plan_stage_completed",
                slug=slug,
                task=f"plan/{slug}",
                stage=label,
                attempt=attempt,
                provider="codex",
            )
            return payload

        raise LLMInvocationError(f"Codex ({label}) exhausted retries without success.")


def _planner_attempts() -> int:
    raw_attempts = os.environ.get("CODEX_PLANNER_RETRIES", "3")
    try:
        attempts = int(raw_attempts)
    except (TypeError, ValueError):
        attempts = 3
    return max(1, attempts)


def _planner_backoff() -> float:
    raw = os.environ.get("CODEX_PLANNER_BACKOFF")
    if isinstance(raw, str):
        raw = raw.strip()
    if raw:
        try:
            return max(1.0, float(raw))
        except ValueError:
            pass
    return 1.8


def _planner_initial_delay() -> float:
    raw = os.environ.get("CODEX_PLANNER_DELAY")
    if isinstance(raw, str):
        raw = raw.strip()
    if raw:
        try:
            value = float(raw)
        except ValueError:
            value = 1.4
    else:
        value = 1.4
    return max(0.0, value)


def _codex_timeout_seconds() -> int | None:
    raw = os.environ.get("CODEX_TIMEOUT_SECONDS")
    if raw is None:
        return 300
    try:
        value = int(raw.strip())
    except (TypeError, ValueError):
        return 300
    return value if value > 0 else None


def _build_codex_command(
    *,
    bin_spec: str,
    flags: str,
    model: str,
    prompt: str,
    cwd: os.PathLike[str] | str,
) -> list[str]:
    cmd = shlex.split(bin_spec) + ["exec"]
    if flags.strip():
        cmd += shlex.split(flags)
    if model:
        cmd += ["--model", model]
    cmd += ["--cd", str(cwd), "--", prompt]
    return cmd


def _emit_stage_failure(
    *,
    slug: str,
    label: str,
    attempt: int,
    reason: str,
    extra: Dict[str, Any] | None = None,
) -> None:
    payload: Dict[str, Any] = {
        "task": f"plan/{slug}",
        "stage": label,
        "attempt": attempt,
        "reason": reason,
    }
    if extra:
        payload.update(extra)
    emit_event(
        "generator",
        "component_plan_stage_failed",
        slug=slug,
        **payload,
    )


def _emit_stage_retry(
    *,
    slug: str,
    label: str,
    attempt: int,
    reason: str,
) -> None:
    emit_event(
        "generator",
        "component_plan_stage_retry",
        slug=slug,
        task=f"plan/{slug}",
        stage=label,
        attempt=attempt,
        reason=reason,
    )


_PROVIDER_FACTORIES: dict[str, Callable[[LLMProviderConfig], LLMProvider]] = {}


def register_llm_provider(
    name: str, factory: Callable[[LLMProviderConfig], LLMProvider]
) -> None:
    _PROVIDER_FACTORIES[name.lower()] = factory


def reset_llm_providers() -> None:
    """Reset the provider registry to built-in defaults (mainly for tests)."""

    _PROVIDER_FACTORIES.clear()
    register_llm_provider("codex", CodexLLMProvider)


def resolve_llm_provider(
    *,
    context: RexContext,
    codex_bin: str,
    codex_flags: str,
    codex_model: str,
) -> LLMProvider:
    provider_key = os.environ.get("REX_LLM_PROVIDER", "codex").strip().lower() or "codex"
    factory = _PROVIDER_FACTORIES.get(provider_key)
    if factory is None:
        raise LLMInvocationError(f"Unknown LLM provider: {provider_key}")
    config = LLMProviderConfig(
        context=context,
        codex_bin=codex_bin,
        codex_flags=codex_flags,
        codex_model=codex_model,
    )
    return factory(config)


# Register default provider set.
reset_llm_providers()


__all__ = [
    "LLMInvocationError",
    "LLMProviderConfig",
    "LLMProvider",
    "CodexLLMProvider",
    "register_llm_provider",
    "reset_llm_providers",
    "resolve_llm_provider",
]
