from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from tests.feature_specs.hello_cli import conftest as cli_conftest

HELLO_MODULE = importlib.import_module("hello")
hello_main = HELLO_MODULE.main


def test_default_greeting(run_app, capsys):
    """AC#1 Run with default arguments and print `Hello World`."""

    run_app()
    captured = capsys.readouterr()
    assert captured.out == "Hello World\n"
    assert captured.err == ""


def test_message_override(run_app, capsys):
    """AC#2 Accept `--message` to override the greeting text."""

    run_app("--message", "Hi there")
    captured = capsys.readouterr()
    assert captured.out == "Hi there\n"
    assert captured.err == ""


@pytest.mark.parametrize("repeat", [2, 3])
def test_repeat_behavior(run_app, capsys, repeat):
    """AC#2 Accept `--repeat` to control repetition."""

    run_app("--repeat", str(repeat))
    captured = capsys.readouterr()
    expected = "Hello World\n" * repeat
    assert captured.out == expected
    assert captured.err == ""


def test_quiet_mode_suppresses_output(run_app, capsys):
    """AC#3 Support `--quiet` to suppress output entirely."""

    run_app("--message", "Muted", "--quiet")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_root_env_override(tmp_path, monkeypatch, capsys):
    """AC#1 Run with ROOT override so the CLI resolves modules from the env path."""

    alt_root = tmp_path / "alt_root"
    real_src = Path(__file__).resolve().parents[3] / "src" / "hello"
    alt_src = alt_root / "src"
    alt_src.mkdir(parents=True)
    (alt_src / "hello").symlink_to(real_src, target_is_directory=True)
    monkeypatch.setenv("ROOT", str(alt_root))

    original_module = sys.modules.pop("hello", None)
    try:
        reimported = cli_conftest._import_from_src("hello")
        assert cli_conftest._project_root() == alt_root
        result = reimported.main(["--message", "Env override"])
        assert result == 0
        captured = capsys.readouterr()
        assert captured.out == "Env override\n"
        assert captured.err == ""
    finally:
        if original_module is not None:
            sys.modules["hello"] = original_module


def test_invalid_repeat_argument_errors(capsys):
    """AC#2 Invalid `--repeat` values exit with an explicit parsing error."""

    with pytest.raises(SystemExit) as exc:
        hello_main(["--repeat", "invalid"])
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "invalid int value" in captured.err
