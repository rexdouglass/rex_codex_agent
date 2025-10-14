from __future__ import annotations

from rex_codex.cli import build_parser


def test_install_parser_accepts_force_and_channel() -> None:
    parser = build_parser()
    args = parser.parse_args(["install", "--force", "--channel", "main"])
    assert args.command == "install"
    assert args.force is True
    assert args.channel == "main"
