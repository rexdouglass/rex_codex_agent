from __future__ import annotations

import pytest


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
