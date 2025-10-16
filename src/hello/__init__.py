"""Toy CLI runtime used by generator HUD demos."""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

DEFAULT_MESSAGE = "Hello World"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hello", add_help=True)
    parser.add_argument(
        "--message",
        default=DEFAULT_MESSAGE,
        help="Override the greeting text.",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="Number of times to print the greeting (default: 1).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress all stdout output (always returns exit code 0).",
    )
    return parser


def _render_message(message: str, repeat: int) -> str:
    repeat = max(repeat, 0)
    if repeat == 0:
        return ""
    lines = [message] * repeat
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.quiet:
        return 0
    output = _render_message(args.message, args.repeat)
    sys.stdout.write(output)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
