import os
import socket
import time
from collections.abc import Generator
from contextlib import contextmanager

import pytest


@pytest.fixture(autouse=True)
def _enforce_synthetic_run_level(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default all tests to local synthetic mode."""
    monkeypatch.setenv(
        "SYNTHETIC_RUN_LEVEL",
        os.getenv("SYNTHETIC_RUN_LEVEL", "local"),
    )


def _deny_network(*_: object, **__: object) -> None:
    raise RuntimeError("Network access blocked during tests")


@contextmanager
def _deny_sleep() -> Generator[None, None, None]:
    original_sleep = time.sleep

    def _guarded_sleep(seconds: float) -> None:
        raise RuntimeError(f"time.sleep({seconds}) blocked during tests")

    time.sleep = _guarded_sleep
    try:
        yield
    finally:
        time.sleep = original_sleep


@pytest.fixture(autouse=True)
def _enforce_offline(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    """Block network and sleep unless explicitly allowed."""
    monkeypatch.setattr(socket, "create_connection", _deny_network)
    with _deny_sleep():
        yield
