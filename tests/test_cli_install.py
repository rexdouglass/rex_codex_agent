from __future__ import annotations

from rex_codex.cli import build_parser


def test_install_parser_accepts_force_and_channel() -> None:
    parser = build_parser()
    args = parser.parse_args(["install", "--force", "--channel", "main"])
    assert args.command == "install"
    assert args.force is True
    assert args.channel == "main"


def test_generator_parser_quiet_and_tail() -> None:
    parser = build_parser()
    args = parser.parse_args(["generator", "--quiet", "--tail", "200"])
    assert args.command == "generator"
    assert args.quiet is True
    assert args.tail == 200


def test_loop_parser_quiet_and_tail() -> None:
    parser = build_parser()
    args = parser.parse_args(["loop", "--quiet", "--tail", "150"])
    assert args.command == "loop"
    assert args.quiet is True
    assert args.tail == 150


def test_discriminator_parser_quiet_and_tail() -> None:
    parser = build_parser()
    args = parser.parse_args(["discriminator", "--quiet", "--tail", "90"])
    assert args.command == "discriminator"
    assert args.quiet is True
    assert args.tail == 90


def test_logs_parser_filters() -> None:
    parser = build_parser()
    args = parser.parse_args(["logs", "--generator", "--lines", "50"])
    assert args.command == "logs"
    assert args.generator is True
    assert args.discriminator is False
    assert args.lines == 50
